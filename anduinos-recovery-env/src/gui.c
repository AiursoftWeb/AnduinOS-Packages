/* Small graphical browser. All storage operations use recovery-action and the
 * existing offline engine; this process never edits the installed filesystem. */
#define _GNU_SOURCE
#include <lvgl/lvgl.h>
#include <jv.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/kd.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/wait.h>
#include <unistd.h>
#include "cursor.h"

#define MAX_RESPONSE (16 * 1024 * 1024)
struct snapshot { char *id, *title, *date; bool ready, marked; };
static struct snapshot *snapshots;
static int snapshot_count;
static lv_obj_t *body, *dialog, *pointer;
static lv_group_t *main_group, *dialog_group;
static lv_font_t *body_font, *title_font;
static int terminal = -1, old_mode = KD_TEXT;
static volatile sig_atomic_t stopping;
static pid_t worker;
static int worker_fd = -1;
static char *response;
static size_t response_length;
static bool response_overflow;
static char operation[16];

static void start_action(const char *action, const char *identifier);
static void listed(jv report);
static void failed(const char *message);

static void restore_terminal(void) {
    if (terminal >= 0) { ioctl(terminal, KDSETMODE, old_mode); close(terminal); terminal = -1; }
}
static void stop(int signum) { (void)signum; stopping = 1; }
static jv field(jv object, const char *name) { return jv_object_get(jv_copy(object), jv_string(name)); }
static char *string_field(jv object, const char *name) {
    jv value = field(object, name);
    char *text = strdup(jv_get_kind(value) == JV_KIND_STRING ? jv_string_value(value) : "");
    jv_free(value);
    return text;
}
static bool uuid_valid(const char *value) {
    if (strlen(value) != 36) return false;
    for (int i = 0; i < 36; i++) {
        if (i == 8 || i == 13 || i == 18 || i == 23) { if (value[i] != '-') return false; }
        else if (!strchr("0123456789abcdefABCDEF", value[i])) return false;
    }
    return true;
}
static void select_group(lv_group_t *group) {
    lv_group_set_default(group);
    lv_indev_t *input = NULL;
    while ((input = lv_indev_get_next(input)))
        if (lv_indev_get_type(input) == LV_INDEV_TYPE_KEYPAD) lv_indev_set_group(input, group);
}
static lv_obj_t *label(lv_obj_t *parent, const char *text, unsigned color) {
    lv_obj_t *o = lv_label_create(parent);
    lv_label_set_text(o, text);
    lv_obj_set_style_text_color(o, lv_color_hex(color), 0);
    lv_obj_set_width(o, lv_pct(100));
    return o;
}
static void page(const char *title, const char *subtitle) {
    lv_group_remove_all_objs(main_group);
    lv_obj_clean(body);
    lv_obj_t *heading = label(body, title, 0xf0f0f0);
    lv_obj_set_style_text_font(heading, title_font, 0);
    label(body, subtitle, 0xbdbdbd);
}
static lv_obj_t *button(lv_obj_t *parent, const char *text, lv_event_cb_t cb, void *data, bool primary) {
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_set_style_bg_color(b, lv_color_hex(primary ? 0xe95420 : 0x484848), 0);
    lv_obj_set_style_radius(b, 9, 0);
    lv_obj_set_style_pad_hor(b, 20, 0); lv_obj_set_style_pad_ver(b, 12, 0);
    lv_obj_set_style_outline_color(b, lv_color_hex(0xf5ad73), LV_STATE_FOCUSED);
    lv_obj_set_style_outline_width(b, 2, LV_STATE_FOCUSED);
    lv_obj_t *caption = lv_label_create(b);
    lv_label_set_text(caption, text); lv_obj_center(caption);
    lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, data);
    return b;
}
static lv_obj_t *footer(void) {
    lv_obj_t *row = lv_obj_create(body); lv_obj_remove_style_all(row);
    lv_obj_set_size(row, lv_pct(100), LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_END, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(row, 16, 0);
    lv_obj_set_style_pad_all(row, 4, 0);
    return row;
}
static void spacer(void) {
    lv_obj_t *space = lv_obj_create(body); lv_obj_remove_style_all(space);
    lv_obj_set_flex_grow(space, 1);
}
static void restart(lv_event_t *e) { (void)e; start_action("restart", NULL); }
static void retry(lv_event_t *e) { (void)e; start_action("list", NULL); }
static void resume(lv_event_t *e) { (void)e; start_action("resume", NULL); }
static void close_dialog(void) {
    if (!dialog) return;
    lv_msgbox_close(dialog); dialog = NULL;
    select_group(main_group);
    lv_group_delete(dialog_group); dialog_group = NULL;
}
static void cancel(lv_event_t *e) { (void)e; close_dialog(); }
static void dialog_key(lv_event_t *e) { if (lv_event_get_key(e) == LV_KEY_ESC) close_dialog(); }
static void accept(lv_event_t *e) {
    struct snapshot *s = lv_event_get_user_data(e);
    char identifier[37]; snprintf(identifier, sizeof(identifier), "%s", s->id);
    close_dialog(); start_action("restore", identifier);
}
static void confirm(lv_event_t *e) {
    struct snapshot *s = lv_event_get_user_data(e);
    if (!s->ready || worker) return;
    dialog_group = lv_group_create(); select_group(dialog_group);
    dialog = lv_msgbox_create(NULL);
    int width = lv_display_get_horizontal_resolution(NULL);
    lv_obj_set_width(dialog, width > 600 ? 560 : width - 32);
    lv_obj_set_style_bg_color(dialog, lv_color_hex(0x303030), 0);
    lv_obj_set_style_text_color(dialog, lv_color_hex(0xf0f0f0), 0);
    lv_obj_set_style_text_font(dialog, body_font, 0);
    lv_obj_set_style_radius(dialog, 16, 0);
    lv_obj_set_style_pad_all(dialog, 20, 0);
    lv_obj_set_style_bg_color(lv_obj_get_parent(dialog), lv_color_hex(0), 0);
    lv_obj_set_style_bg_opa(lv_obj_get_parent(dialog), LV_OPA_60, 0);
    lv_msgbox_add_title(dialog, "Roll back the system?");
    lv_msgbox_add_text(dialog, s->title);
    lv_msgbox_add_text(dialog, s->date);
    lv_msgbox_add_text(dialog, "System files and installed applications will return to this snapshot. Personal files will stay unchanged.\n\nNo new backup snapshot will be created.");
    lv_obj_t *no = lv_msgbox_add_footer_button(dialog, "Cancel");
    lv_obj_t *yes = lv_msgbox_add_footer_button(dialog, "Roll Back");
    lv_obj_set_style_bg_color(no, lv_color_hex(0x484848), 0);
    lv_obj_set_style_bg_color(yes, lv_color_hex(0xe95420), 0);
    lv_obj_set_height(lv_msgbox_get_footer(dialog), LV_SIZE_CONTENT);
    lv_obj_set_height(no, 44); lv_obj_set_height(yes, 44);
    lv_obj_set_style_radius(no, 9, 0); lv_obj_set_style_radius(yes, 9, 0);
    lv_obj_add_event_cb(no, cancel, LV_EVENT_CLICKED, NULL);
    lv_obj_add_event_cb(yes, accept, LV_EVENT_CLICKED, s);
    lv_obj_add_event_cb(no, dialog_key, LV_EVENT_KEY, NULL);
    lv_obj_add_event_cb(yes, dialog_key, LV_EVENT_KEY, NULL);
    lv_group_focus_obj(no);
}
static void free_snapshots(void) {
    for (int i = 0; i < snapshot_count; i++) {
        free(snapshots[i].id); free(snapshots[i].title); free(snapshots[i].date);
    }
    free(snapshots); snapshots = NULL; snapshot_count = 0;
}
static void listed(jv report) {
    jv schema = field(report, "schema"), records = field(report, "deployments");
    bool valid = jv_get_kind(schema) == JV_KIND_NUMBER && jv_number_value(schema) == 1
                 && jv_get_kind(records) == JV_KIND_ARRAY;
    jv_free(schema);
    if (!valid) { jv_free(records); failed("The snapshot catalog is not supported by this recovery environment."); return; }
    free_snapshots();
    jv pending = field(report, "pending");
    bool interrupted = jv_get_kind(pending) != JV_KIND_NULL && jv_is_valid(pending);
    jv_free(pending);
    if (interrupted) {
        jv_free(records);
        page("Finish the interrupted recovery", "A previously confirmed rollback did not finish. Resume it before choosing another snapshot.");
        spacer(); lv_obj_t *row = footer();
        button(row, "Restart", restart, NULL, false);
        button(row, "Resume Recovery", resume, NULL, true);
        return;
    }
    page("AnduinOS Recovery", "Choose a system snapshot to roll back to. Your personal files will stay unchanged.");
    snapshot_count = jv_array_length(jv_copy(records));
    snapshots = calloc((size_t)snapshot_count, sizeof(*snapshots));
    if (snapshot_count && !snapshots) { snapshot_count = 0; jv_free(records); failed("Not enough memory to display the snapshot catalog."); return; }
    char *marked = string_field(report, "last_successful_boot_id");
    lv_obj_t *list = lv_obj_create(body);
    lv_obj_set_width(list, lv_pct(100)); lv_obj_set_flex_grow(list, 1);
    lv_obj_set_style_bg_color(list, lv_color_hex(0x303030), 0);
    lv_obj_set_style_radius(list, 14, 0); lv_obj_set_style_border_width(list, 0, 0);
    lv_obj_set_style_pad_all(list, 0, 0); lv_obj_set_style_pad_row(list, 0, 0);
    lv_obj_set_flex_flow(list, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_scroll_dir(list, LV_DIR_VER);
    for (int i = 0; i < snapshot_count; i++) {
        jv record = jv_array_get(jv_copy(records), i);
        if (jv_get_kind(record) != JV_KIND_OBJECT) { jv_free(record); continue; }
        struct snapshot *s = &snapshots[i];
        s->id = string_field(record, "id"); s->title = string_field(record, "title"); s->date = string_field(record, "created_at");
        char *state = string_field(record, "state"); jv failure = field(record, "failure");
        s->ready = uuid_valid(s->id) && !strcmp(state, "ready") && jv_get_kind(failure) == JV_KIND_NULL;
        s->marked = s->ready && !strcmp(s->id, marked);
        free(state); jv_free(failure); jv_free(record);
        lv_obj_t *row = lv_obj_create(list); lv_obj_remove_style_all(row);
        lv_obj_set_size(row, lv_pct(100), LV_SIZE_CONTENT);
        lv_obj_set_style_pad_all(row, 20, 0); lv_obj_set_style_pad_column(row, 20, 0);
        lv_obj_set_style_border_color(row, lv_color_hex(0x242424), 0);
        lv_obj_set_style_border_width(row, 1, 0); lv_obj_set_style_border_side(row, LV_BORDER_SIDE_BOTTOM, 0);
        lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
        lv_obj_set_flex_align(row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
        lv_obj_t *info = lv_obj_create(row); lv_obj_remove_style_all(info);
        lv_obj_set_flex_grow(info, 1); lv_obj_set_height(info, LV_SIZE_CONTENT);
        lv_obj_set_flex_flow(info, LV_FLEX_FLOW_COLUMN); lv_obj_set_style_pad_row(info, 6, 0);
        label(info, s->date, 0xf0f0f0); label(info, s->title, 0xbdbdbd);
        if (!s->ready) label(info, "This snapshot is not ready for recovery.", 0xd6ac70);
        if (s->marked) {
            lv_obj_t *badge = label(row, "Last successful boot", 0x92d4a7);
            lv_obj_set_width(badge, LV_SIZE_CONTENT);
        }
        lv_obj_t *b = button(row, "Roll Back", confirm, s, true);
        if (!s->ready) lv_obj_add_state(b, LV_STATE_DISABLED);
    }
    jv_free(records); free(marked);
    if (!snapshot_count) label(list, "No system snapshots are available.", 0xbdbdbd);
    jv issues = field(report, "issues");
    if (jv_get_kind(issues) == JV_KIND_ARRAY && jv_array_length(jv_copy(issues)))
        label(body, "Some snapshots could not be read. Only recognized snapshots are listed.", 0xd6ac70);
    jv_free(issues);
    button(footer(), "Restart", restart, NULL, false);
}
static void failed(const char *message) {
    page("Recovery needs attention", "The operation could not be completed. You can retry or restart; an interrupted rollback can be resumed here.");
    lv_obj_t *details = lv_obj_create(body);
    lv_obj_set_width(details, lv_pct(100)); lv_obj_set_flex_grow(details, 1);
    lv_obj_set_scroll_dir(details, LV_DIR_VER);
    label(details, message, 0xf0f0f0);
    lv_obj_t *row = footer();
    button(row, "Restart", restart, NULL, false); button(row, "Try Again", retry, NULL, true);
    fprintf(stderr, "Recovery action failed: %s\n", message);
}
static void poll_worker(lv_timer_t *timer) {
    (void)timer;
    if (!worker) return;
    char buffer[4096]; ssize_t count;
    while ((count = read(worker_fd, buffer, sizeof(buffer))) > 0) {
        if (response_length + (size_t)count > MAX_RESPONSE) { response_overflow = true; continue; }
        char *new_response = realloc(response, response_length + (size_t)count + 1);
        if (!new_response) { response_overflow = true; continue; }
        response = new_response;
        memcpy(response + response_length, buffer, (size_t)count); response_length += (size_t)count;
        response[response_length] = 0;
    }
    int status;
    if (waitpid(worker, &status, WNOHANG) <= 0) return;
    /* A child can exit between the last read and waitpid; drain its final bytes. */
    while ((count = read(worker_fd, buffer, sizeof(buffer))) > 0) {
        if (response_length + (size_t)count > MAX_RESPONSE) { response_overflow = true; continue; }
        char *new_response = realloc(response, response_length + (size_t)count + 1);
        if (!new_response) { response_overflow = true; continue; }
        response = new_response; memcpy(response + response_length, buffer, (size_t)count);
        response_length += (size_t)count; response[response_length] = 0;
    }
    close(worker_fd); worker_fd = -1; worker = 0;
    fprintf(stderr, "Recovery action %s finished: %d\n", operation, status);
    if (response_overflow) { failed("The recovery response is too large to display."); return; }
    if (!WIFEXITED(status) || WEXITSTATUS(status)) {
        char details[8192] = {0};
        FILE *errors = fopen("/run/recovery-action-error", "r");
        if (errors) { size_t n = fread(details, 1, sizeof(details) - 1, errors); details[n] = 0; fclose(errors); }
        failed(*details ? details : response && *response ? response : "The recovery operation failed without returning details.");
        return;
    }
    if (!strcmp(operation, "list")) {
        jv report = jv_parse(response ? response : "");
        if (jv_get_kind(report) == JV_KIND_OBJECT) listed(report);
        else failed("The recovery engine returned an unreadable snapshot catalog.");
        jv_free(report);
    } else if (strcmp(operation, "restart")) {
        page("Your system has been restored", "Restart to use the restored system. Your personal files were left unchanged.");
        spacer(); button(footer(), "Restart", restart, NULL, true);
    }
}
static void start_action(const char *action, const char *identifier) {
    if (worker) return;
    close_dialog();
    bool loading = !strcmp(action, "list"), restarting = !strcmp(action, "restart");
    page(loading ? "AnduinOS Recovery" : restarting ? "Restarting…" : "Restoring your system…",
         loading ? "Looking for system snapshots…" : restarting ? "Please wait." : "Keep the computer powered on. This may take a few minutes.");
    lv_obj_t *spinner = lv_spinner_create(body); lv_obj_set_size(spinner, 40, 40);
    snprintf(operation, sizeof(operation), "%s", action);
    free(response); response = NULL; response_length = 0; response_overflow = false;
    int fds[2];
    if (pipe2(fds, O_CLOEXEC)) { failed(strerror(errno)); return; }
    worker = fork();
    if (!worker) {
        close(fds[0]); dup2(fds[1], STDOUT_FILENO); close(fds[1]);
        /* Diagnostic output must never become part of the JSON catalog. */
        int errors = open("/run/recovery-action-error", O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
        if (errors < 0) { perror("Could not open recovery log"); _exit(126); }
        dup2(errors, STDERR_FILENO); close(errors);
        const char *args[] = {"/usr/bin/recovery-action", action, identifier, NULL};
        execv(args[0], (char *const *)args);
        perror("Could not start recovery-action"); _exit(127);
    }
    close(fds[1]);
    if (worker < 0) { close(fds[0]); worker = 0; failed(strerror(errno)); return; }
    worker_fd = fds[0]; fcntl(worker_fd, F_SETFL, O_NONBLOCK);
}
static void input_added(lv_indev_t *input, lv_evdev_type_t type, void *data) {
    (void)data;
    if (type == LV_EVDEV_TYPE_KEY) lv_indev_set_group(input, dialog ? dialog_group : main_group);
    else if (lv_indev_get_type(input) == LV_INDEV_TYPE_POINTER) {
        if (!pointer) {
            pointer = lv_image_create(lv_layer_top()); lv_image_set_src(pointer, &fluent_cursor);
            lv_obj_set_style_translate_x(pointer, -FLUENT_CURSOR_HOT_X, 0);
            lv_obj_set_style_translate_y(pointer, -FLUENT_CURSOR_HOT_Y, 0);
            lv_obj_set_clickable(pointer, false);
        }
        lv_indev_set_cursor(input, pointer);
    }
}
int main(int argc, char **argv) {
    terminal = open("/dev/tty0", O_RDWR | O_CLOEXEC);
    if (argc == 2 && !strcmp(argv[1], "text")) { restore_terminal(); return 0; }
    if (terminal >= 0) ioctl(terminal, KDGETMODE, &old_mode);
    atexit(restore_terminal); signal(SIGTERM, stop); signal(SIGINT, stop);
    lv_init();
    lv_display_t *display;
    if (argc == 2 && !strcmp(argv[1], "fbdev")) {
        if (access("/dev/fb0", R_OK | W_OK)) return 1;
        display = lv_linux_fbdev_create(); lv_linux_fbdev_set_file(display, "/dev/fb0");
    } else {
        display = lv_linux_drm_create();
        char *path = lv_linux_drm_find_device_path();
        if (!path) return 1;
        lv_result_t result = lv_linux_drm_set_file(display, path, -1); lv_free(path);
        if (result != LV_RESULT_OK) return 1;
    }
    int width = lv_display_get_horizontal_resolution(display), height = lv_display_get_vertical_resolution(display);
    if (width < 640 || height < 480) return 1;
    if (terminal >= 0) ioctl(terminal, KDSETMODE, KD_GRAPHICS);
    body_font = lv_freetype_font_create("/usr/share/fonts/DejaVuSans.ttf", LV_FREETYPE_FONT_RENDER_MODE_BITMAP, 18, LV_FREETYPE_FONT_STYLE_NORMAL);
    title_font = lv_freetype_font_create("/usr/share/fonts/DejaVuSans.ttf", LV_FREETYPE_FONT_RENDER_MODE_BITMAP, 28, LV_FREETYPE_FONT_STYLE_BOLD);
    lv_font_t *cjk = lv_freetype_font_create("/usr/share/fonts/wqy-microhei.ttc", LV_FREETYPE_FONT_RENDER_MODE_BITMAP, 18, LV_FREETYPE_FONT_STYLE_NORMAL);
    if (!body_font || !title_font || !cjk) return 1;
    body_font->fallback = cjk;
    lv_theme_default_init(display, lv_color_hex(0xe95420), lv_color_hex(0x92d4a7), true, body_font);
    main_group = lv_group_create(); select_group(main_group);
    lv_obj_set_style_bg_color(lv_screen_active(), lv_color_hex(0x242424), 0);
    body = lv_obj_create(lv_screen_active()); lv_obj_remove_style_all(body);
    lv_obj_set_size(body, width > 1024 ? 960 : width - 48, height - 64);
    lv_obj_align(body, LV_ALIGN_TOP_MID, 0, 32);
    lv_obj_set_flex_flow(body, LV_FLEX_FLOW_COLUMN); lv_obj_set_style_pad_row(body, 18, 0);
    lv_obj_set_scrollable(body, false);
    if (lv_evdev_discovery_start(input_added, NULL) != LV_RESULT_OK) return 1;
    lv_timer_create(poll_worker, 100, NULL);
    start_action("list", NULL);
    while (!stopping) { uint32_t delay = lv_timer_handler(); usleep((delay > 20 ? 20 : delay) * 1000); }
    /* Do not abandon a confirmed operation merely because the display stops. */
    while (worker) { poll_worker(NULL); usleep(100000); }
    return 0;
}
