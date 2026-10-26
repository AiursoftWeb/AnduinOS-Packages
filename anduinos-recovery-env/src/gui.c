/*
 * Recovery interface for the standalone initramfs.
 *
 * LVGL owns display and input handling. A single child process runs
 * recovery-action; all filesystem changes belong to that helper and the
 * existing offline recovery engine, never to this interface.
 */
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

/* Default Fluent-round-Dark theme_selected_bg_color. */
#define ACCENT_BLUE 0x3281ea
#define MAX_RESPONSE (16 * 1024 * 1024)
#define ERROR_LOG "/run/recovery-action-error"
#define MAX_ERROR_DETAILS (64 * 1024)

/* Strings are owned by this catalog and released by free_snapshots(). */
struct snapshot {
    char *id;
    char *title;
    char *date;
    bool ready;
    bool marked;
};

static struct snapshot *snapshots;
static int snapshot_count;
static bool current_saved;

/* Widgets belong to LVGL; replacing a page deletes its child widgets. */
static lv_obj_t *body;
static lv_obj_t *dialog;
static lv_obj_t *pointer;
static lv_obj_t *tabs;
static bool information_page;
static lv_group_t *main_group;
static lv_group_t *dialog_group;
static lv_font_t *body_font;
static lv_font_t *title_font;

/* Restore the console mode when leaving the graphical interface. */
static int terminal = -1;
static int old_mode = KD_TEXT;
static volatile sig_atomic_t stopping;

/* At most one helper is active. Its stdout pipe carries the JSON response. */
static pid_t worker;
static int worker_fd = -1;
static char *response;
static size_t response_length;
static bool response_overflow;
static char operation[16];

/* Keep the last failure stable while moving between its summary and details. */
static char failure_summary[384];
static char failure_details[MAX_ERROR_DETAILS];
static bool action_log_current;

/* Read-only inspection has a deadline; a confirmed rollback never does. */
static uint32_t information_started;
static bool information_timed_out;

static void start_action(const char *action, const char *identifier);
static void listed(jv report);
static void failed(const char *message);
static lv_obj_t *button(
    lv_obj_t *parent, const char *text, lv_event_cb_t callback, void *data, bool primary);

static void show_recovery(lv_event_t *event)
{
    (void)event;
    start_action("list", NULL);
}

static void show_information(lv_event_t *event)
{
    (void)event;
    start_action("information", NULL);
}

static void restore_terminal(void)
{
    if (terminal >= 0) {
        ioctl(terminal, KDSETMODE, old_mode);
        close(terminal);
        terminal = -1;
    }
}

static void stop(int signum)
{
    (void)signum;
    stopping = 1;
}

/* jq consumes its arguments; copying here keeps the caller's object alive. */
static jv field(jv object, const char *name)
{
    return jv_object_get(jv_copy(object), jv_string(name));
}

/* The caller owns the returned string, independently of the JSON value. */
static char *string_field(jv object, const char *name)
{
    jv value = field(object, name);
    char *text = strdup(jv_get_kind(value) == JV_KIND_STRING ? jv_string_value(value) : "");

    jv_free(value);
    return text;
}

static char *snapshot_date(jv record)
{
    char *text = string_field(record, "created_at");
    size_t length = strlen(text);

    if (length > 19 && text[10] == 'T' && text[length - 1] == 'Z') {
        /* Records use UTC; omit nanoseconds without inventing a local timezone. */
        text[10] = ' ';
        text[19] = 0;
        char *formatted = NULL;
        if (asprintf(&formatted, "%s UTC", text) >= 0) {
            free(text);
            return formatted;
        }
    }

    return text;
}

static bool uuid_valid(const char *value)
{
    if (strlen(value) != 36) {
        return false;
    }

    for (int i = 0; i < 36; i++) {
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (value[i] != '-') {
                return false;
            }
        } else if (!strchr("0123456789abcdefABCDEF", value[i])) {
            return false;
        }
    }

    return true;
}

/* Existing and newly discovered keyboards must use the same focus group. */
static void select_group(lv_group_t *group)
{
    lv_group_set_default(group);
    lv_indev_t *input = NULL;
    while ((input = lv_indev_get_next(input))) {
        if (lv_indev_get_type(input) == LV_INDEV_TYPE_KEYPAD) {
            lv_indev_set_group(input, group);
        }
    }
}

static lv_obj_t *label(lv_obj_t *parent, const char *text, unsigned color)
{
    lv_obj_t *widget = lv_label_create(parent);
    lv_label_set_text(widget, text);
    lv_obj_set_style_text_color(widget, lv_color_hex(color), 0);
    lv_obj_set_width(widget, lv_pct(100));
    return widget;
}

static void page(const char *title, const char *subtitle)
{
    lv_group_remove_all_objs(main_group);
    lv_obj_clean(body);
    tabs = lv_obj_create(body);
    lv_obj_remove_style_all(tabs);
    lv_obj_set_size(tabs, lv_pct(100), LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(tabs, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(tabs, 12, 0);
    lv_obj_t *recovery_tab = button(tabs, "System Recovery", show_recovery, NULL, false);
    lv_obj_t *information_tab = button(tabs, "System Information", show_information, NULL, false);
    lv_obj_set_style_bg_color(information_page ? information_tab : recovery_tab,
        lv_color_mix(lv_color_hex(ACCENT_BLUE), lv_color_hex(0x484848), LV_OPA_30),
        0);

    lv_obj_t *heading = label(body, title, 0xf0f0f0);
    lv_obj_set_style_text_font(heading, title_font, 0);
    label(body, subtitle, 0xbdbdbd);
}

static lv_obj_t *button(
    lv_obj_t *parent, const char *text, lv_event_cb_t callback, void *data, bool primary)
{
    lv_obj_t *control = lv_button_create(parent);
    lv_obj_set_style_bg_color(control, lv_color_hex(primary ? ACCENT_BLUE : 0x484848), 0);
    lv_obj_set_style_radius(control, 9, 0);
    lv_obj_set_style_pad_hor(control, 20, 0);
    lv_obj_set_style_pad_ver(control, 12, 0);

    /* Keep focus inside the button: tab containers clip external outlines. */
    lv_obj_set_style_outline_width(control, 0, LV_STATE_FOCUSED);
    lv_obj_set_style_border_width(control, 2, 0);
    lv_obj_set_style_border_opa(control, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_color(control, lv_color_hex(ACCENT_BLUE), LV_STATE_FOCUSED);
    lv_obj_set_style_border_opa(control, LV_OPA_COVER, LV_STATE_FOCUSED);

    lv_obj_t *caption = lv_label_create(control);
    lv_label_set_text(caption, text);
    lv_obj_center(caption);
    lv_obj_add_event_cb(control, callback, LV_EVENT_CLICKED, data);
    return control;
}

static lv_obj_t *footer(void)
{
    lv_obj_t *row = lv_obj_create(body);
    lv_obj_remove_style_all(row);
    lv_obj_set_size(row, lv_pct(100), LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_END, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(row, 16, 0);
    lv_obj_set_style_pad_all(row, 4, 0);
    return row;
}

static void spacer(void)
{
    lv_obj_t *space = lv_obj_create(body);
    lv_obj_remove_style_all(space);
    lv_obj_set_flex_grow(space, 1);
}

static void restart(lv_event_t *event)
{
    (void)event;
    start_action("restart", NULL);
}

static void retry(lv_event_t *event)
{
    (void)event;
    start_action(information_page ? "information" : "list", NULL);
}

static void resume(lv_event_t *event)
{
    (void)event;
    start_action("resume", NULL);
}

static void create_snapshot(lv_event_t *event)
{
    (void)event;
    start_action("create", NULL);
}

static void close_dialog(void)
{
    if (!dialog) {
        return;
    }
    lv_msgbox_close(dialog);
    dialog = NULL;
    select_group(main_group);
    lv_group_delete(dialog_group);
    dialog_group = NULL;
}

static void cancel(lv_event_t *event)
{
    (void)event;
    close_dialog();
}

static void dialog_key(lv_event_t *event)
{
    switch (lv_event_get_key(event)) {
    case LV_KEY_LEFT:
        lv_group_focus_prev(dialog_group);
        break;
    case LV_KEY_RIGHT:
        lv_group_focus_next(dialog_group);
        break;
    case LV_KEY_ESC:
        close_dialog();
        break;
    default:
        break;
    }
}

static void accept(lv_event_t *event)
{
    struct snapshot *snapshot = lv_event_get_user_data(event);
    char identifier[37];
    snprintf(identifier, sizeof(identifier), "%s", snapshot->id);
    close_dialog();
    start_action("restore", identifier);
}

static void confirm(lv_event_t *event)
{
    struct snapshot *snapshot = lv_event_get_user_data(event);
    if (!snapshot->ready || worker) {
        return;
    }
    dialog_group = lv_group_create();
    select_group(dialog_group);
    dialog = lv_msgbox_create(NULL);
    int width = lv_display_get_horizontal_resolution(NULL);
    lv_obj_set_width(dialog, width > 600 ? 560 : width - 32);
    lv_obj_set_style_max_height(dialog, lv_display_get_vertical_resolution(NULL) - 32, 0);
    lv_obj_set_style_bg_color(dialog, lv_color_hex(0x303030), 0);
    lv_obj_set_style_text_color(dialog, lv_color_hex(0xf0f0f0), 0);
    lv_obj_set_style_text_font(dialog, body_font, 0);
    lv_obj_set_style_radius(dialog, 16, 0);
    lv_obj_set_style_pad_all(dialog, 20, 0);
    lv_obj_set_style_bg_color(lv_obj_get_parent(dialog), lv_color_hex(0), 0);
    lv_obj_set_style_bg_opa(lv_obj_get_parent(dialog), LV_OPA_60, 0);
    lv_msgbox_add_title(dialog, "Roll back the system?");
    lv_msgbox_add_text(dialog, snapshot->title);
    lv_msgbox_add_text(dialog, snapshot->date);
    lv_msgbox_add_text(dialog,
        "System files and installed applications will return to this snapshot. Personal files will "
        "stay unchanged.\n\nNo new backup snapshot will be created.");
    if (!current_saved) {
        lv_obj_t *warning = lv_msgbox_add_text(dialog,
            "DATA NOT INCLUDED IN A SNAPSHOT WILL BE LOST!");
        lv_obj_set_style_text_font(warning, title_font, 0);
        lv_obj_set_style_text_color(warning, lv_color_hex(0xffb4ab), 0);
    }
    lv_obj_t *no = lv_msgbox_add_footer_button(dialog, "Cancel");
    lv_obj_t *yes = lv_msgbox_add_footer_button(dialog, "Roll Back");
    lv_obj_set_style_bg_color(no, lv_color_hex(0x484848), 0);
    lv_obj_set_style_bg_color(yes, lv_color_hex(ACCENT_BLUE), 0);
    lv_obj_set_height(lv_msgbox_get_footer(dialog), LV_SIZE_CONTENT);
    lv_obj_set_height(no, 44);
    lv_obj_set_height(yes, 44);
    lv_obj_set_style_radius(no, 9, 0);
    lv_obj_set_style_radius(yes, 9, 0);
    lv_obj_add_event_cb(no, cancel, LV_EVENT_CLICKED, NULL);
    lv_obj_add_event_cb(yes, accept, LV_EVENT_CLICKED, snapshot);
    lv_obj_add_event_cb(no, dialog_key, LV_EVENT_KEY, NULL);
    lv_obj_add_event_cb(yes, dialog_key, LV_EVENT_KEY, NULL);

    /* Pressing Enter immediately must cancel, not authorize a rollback. */
    lv_group_focus_obj(no);
}

static void free_snapshots(void)
{
    for (int i = 0; i < snapshot_count; i++) {
        free(snapshots[i].id);
        free(snapshots[i].title);
        free(snapshots[i].date);
    }
    free(snapshots);
    snapshots = NULL;
    snapshot_count = 0;
}

static void listed(jv report)
{
    jv schema = field(report, "schema");
    jv records = field(report, "deployments");
    bool valid = jv_get_kind(schema) == JV_KIND_NUMBER && jv_number_value(schema) == 1 &&
                 jv_get_kind(records) == JV_KIND_ARRAY;
    jv_free(schema);
    if (!valid) {
        jv_free(records);
        failed("The snapshot catalog is not supported by this recovery environment.");
        return;
    }
    free_snapshots();
    jv pending = field(report, "pending");
    bool interrupted = jv_get_kind(pending) != JV_KIND_NULL && jv_is_valid(pending);
    jv_free(pending);
    if (interrupted) {
        jv_free(records);
        page("Finish the interrupted recovery",
            "A previously confirmed rollback did not finish. Resume it before choosing another "
            "snapshot.");
        spacer();
        lv_obj_t *row = footer();
        button(row, "Restart", restart, NULL, false);
        button(row, "Resume Recovery", resume, NULL, true);
        return;
    }
    page("AnduinOS Recovery",
        "Choose a system snapshot to roll back to. Your personal files will stay unchanged.");
    snapshot_count = jv_array_length(jv_copy(records));
    snapshots = calloc((size_t)snapshot_count, sizeof(*snapshots));
    if (snapshot_count && !snapshots) {
        snapshot_count = 0;
        jv_free(records);
        failed("Not enough memory to display the snapshot catalog.");
        return;
    }
    char *marked = string_field(report, "last_successful_boot_id");
    char *saved = string_field(report, "current_snapshot_id");
    current_saved = false;
    lv_obj_t *list = lv_obj_create(body);
    lv_obj_set_width(list, lv_pct(100));
    lv_obj_set_flex_grow(list, 1);
    lv_obj_set_style_bg_color(list, lv_color_hex(0x303030), 0);
    lv_obj_set_style_radius(list, 14, 0);
    lv_obj_set_style_border_width(list, 0, 0);
    lv_obj_set_style_pad_all(list, 0, 0);
    lv_obj_set_style_pad_row(list, 0, 0);
    lv_obj_set_flex_flow(list, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_scroll_dir(list, LV_DIR_VER);
    for (int i = 0; i < snapshot_count; i++) {
        jv record = jv_array_get(jv_copy(records), i);
        if (jv_get_kind(record) != JV_KIND_OBJECT) {
            jv_free(record);
            continue;
        }
        struct snapshot *snapshot = &snapshots[i];
        snapshot->id = string_field(record, "id");
        snapshot->title = string_field(record, "title");
        snapshot->date = snapshot_date(record);
        char *state = string_field(record, "state");
        jv failure = field(record, "failure");
        snapshot->ready = uuid_valid(snapshot->id) && !strcmp(state, "ready") &&
                          jv_get_kind(failure) == JV_KIND_NULL;
        snapshot->marked = snapshot->ready && !strcmp(snapshot->id, marked);
        free(state);
        jv_free(failure);
        jv_free(record);
        lv_obj_t *row = lv_obj_create(list);
        lv_obj_remove_style_all(row);
        lv_obj_set_size(row, lv_pct(100), LV_SIZE_CONTENT);
        lv_obj_set_style_pad_all(row, 20, 0);
        lv_obj_set_style_pad_column(row, 20, 0);
        lv_obj_set_style_border_color(row, lv_color_hex(0x242424), 0);
        lv_obj_set_style_border_width(row, 1, 0);
        lv_obj_set_style_border_side(row, LV_BORDER_SIDE_BOTTOM, 0);
        lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
        lv_obj_set_flex_align(row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
        lv_obj_t *info = lv_obj_create(row);
        lv_obj_remove_style_all(info);
        lv_obj_set_flex_grow(info, 1);
        lv_obj_set_height(info, LV_SIZE_CONTENT);
        lv_obj_set_flex_flow(info, LV_FLEX_FLOW_COLUMN);
        lv_obj_set_style_pad_row(info, 6, 0);
        label(info, snapshot->date, 0xf0f0f0);
        label(info, snapshot->title, 0xbdbdbd);
        if (!snapshot->ready) {
            label(info, "This snapshot is not ready for recovery.", 0xd6ac70);
        }
        if (snapshot->marked) {
            lv_obj_t *badge = label(row, "Last successful boot", 0x92d4a7);
            lv_obj_set_width(badge, LV_SIZE_CONTENT);
        }
        if (snapshot->ready && !strcmp(snapshot->id, saved)) {
            current_saved = true;
            label(info, "Current state saved in this recovery session", 0x92d4a7);
        }
        lv_obj_t *control = button(row, "Roll Back", confirm, snapshot, true);
        if (!snapshot->ready) {
            lv_obj_add_state(control, LV_STATE_DISABLED);
        }
    }
    jv_free(records);
    free(marked);
    free(saved);
    if (!snapshot_count) {
        label(list, "No system snapshots are available.", 0xbdbdbd);
    }
    jv issues = field(report, "issues");
    if (jv_get_kind(issues) == JV_KIND_ARRAY && jv_array_length(jv_copy(issues))) {
        label(body,
            "Some snapshots could not be read. Only recognized snapshots are listed.",
            0xd6ac70);
    }
    jv_free(issues);
    lv_obj_t *row = footer();
    button(row, "Snapshot Current State", create_snapshot, NULL, true);
    button(row, "Restart", restart, NULL, false);
}

static void information(jv report)
{
    jv schema = field(report, "schema");
    jv groups = field(report, "groups");
    bool valid = jv_get_kind(schema) == JV_KIND_NUMBER && jv_number_value(schema) == 1 &&
                 jv_get_kind(groups) == JV_KIND_ARRAY && jv_array_length(jv_copy(groups)) <= 8;
    jv_free(schema);
    if (!valid) {
        jv_free(groups);
        failed("System information could not be read.");
        return;
    }
    page("System Information",
        "Read-only details. Missing information does not prevent snapshot recovery.");
    lv_obj_t *list = lv_obj_create(body);
    lv_obj_set_width(list, lv_pct(100));
    lv_obj_set_flex_grow(list, 1);
    lv_obj_set_style_bg_color(list, lv_color_hex(0x303030), 0);
    lv_obj_set_style_radius(list, 14, 0);
    lv_obj_set_style_border_width(list, 0, 0);
    lv_obj_set_flex_flow(list, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_scroll_dir(list, LV_DIR_VER);
    lv_group_add_obj(main_group, list);
    for (int i = 0; i < jv_array_length(jv_copy(groups)); i++) {
        jv group = jv_array_get(jv_copy(groups), i);
        jv rows = field(group, "rows");
        char *title = string_field(group, "title");
        label(list, title, 0x92d4a7);
        free(title);
        if (jv_get_kind(rows) == JV_KIND_ARRAY && jv_array_length(jv_copy(rows)) <= 32) {
            for (int n = 0; n < jv_array_length(jv_copy(rows)); n++) {
                jv record = jv_array_get(jv_copy(rows), n);
                char *name = string_field(record, "name");
                char *value = string_field(record, "value");
                lv_obj_t *row = lv_obj_create(list);
                lv_obj_remove_style_all(row);
                lv_obj_set_size(row, lv_pct(100), LV_SIZE_CONTENT);
                lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
                lv_obj_set_style_pad_column(row, 12, 0);
                lv_obj_t *key = label(row, name, 0xbdbdbd);
                lv_obj_set_width(key, lv_pct(40));
                lv_obj_t *text = label(row, *value ? value : "Unavailable", 0xf0f0f0);
                lv_obj_set_width(text, 0);
                lv_obj_set_flex_grow(text, 1);
                free(name);
                free(value);
                jv_free(record);
            }
        }
        if (i == 1) {
            jv percent = field(report, "used_percent");
            if (jv_get_kind(percent) == JV_KIND_NUMBER && jv_number_value(percent) >= 0 &&
                jv_number_value(percent) <= 100) {
                lv_obj_t *bar = lv_bar_create(list);
                lv_group_remove_obj(bar);
                lv_obj_set_size(bar, lv_pct(100), 8);
                lv_bar_set_value(bar, (int)jv_number_value(percent), LV_ANIM_OFF);
            }
            jv_free(percent);
        }
        jv_free(rows);
        jv_free(group);
    }
    jv_free(groups);
    char *notice = string_field(report, "notice");
    label(list, notice, 0xbdbdbd);
    free(notice);
    button(footer(), "Refresh", show_information, NULL, false);
}

static void show_failure(lv_event_t *event);

static void show_error_details(lv_event_t *event)
{
    (void)event;
    page("Failure details",
        "Details of the most recent failed operation. You can photograph this page.");

    lv_obj_t *log = lv_obj_create(body);
    lv_obj_set_width(log, lv_pct(100));
    lv_obj_set_flex_grow(log, 1);
    lv_obj_set_scroll_dir(log, LV_DIR_VER);
    lv_obj_t *text = label(log, failure_details, 0xf0f0f0);
    lv_label_set_long_mode(text, LV_LABEL_LONG_WRAP);
    lv_group_add_obj(main_group, log);
    button(footer(), "Back", show_failure, NULL, false);
    lv_group_focus_obj(log);
}

static void show_failure(lv_event_t *event)
{
    (void)event;
    page(information_page ? "Information unavailable" : "Recovery needs attention",
        information_page ? "You can retry or return to System Recovery."
                         : "The operation could not be completed. You can retry or restart; an "
                           "interrupted rollback can be resumed here.");
    label(body, failure_summary, 0xf0f0f0);
    button(body, "View Details", show_error_details, NULL, false);
    spacer();

    lv_obj_t *row = footer();
    button(row, "Restart", restart, NULL, false);
    button(row, "Try Again", retry, NULL, true);
}

static void failed(const char *message)
{
    /* The summary stays short; the separate viewer retains multiline diagnostics. */
    size_t length = strcspn(message, "\r\n");
    snprintf(failure_summary,
        sizeof(failure_summary),
        "%.*s",
        (int)(length < sizeof(failure_summary) - 1 ? length : sizeof(failure_summary) - 1),
        message);
    if (!*failure_summary) {
        snprintf(failure_summary, sizeof(failure_summary), "The operation could not be completed.");
    }

    /* Both header fields are bounded, leaving ample room for the log. */
    size_t used = (size_t)snprintf(failure_details,
        sizeof(failure_details),
        "Operation: %s\n\n%s\n\nDiagnostic output:\n",
        *operation ? operation : "startup",
        failure_summary);
    size_t available = sizeof(failure_details) - used;
    FILE *errors = action_log_current ? fopen(ERROR_LOG, "r") : NULL;
    bool truncated;
    if (errors) {
        used += fread(failure_details + used, 1, available - 1, errors);
        truncated = fgetc(errors) != EOF;
        fclose(errors);
    } else {
        const char *diagnostic = response && *response ? response : message;
        size_t length = strlen(diagnostic);
        truncated = length >= available;
        size_t copied = truncated ? available - 1 : length;
        memcpy(failure_details + used, diagnostic, copied);
        used += copied;
    }
    failure_details[used] = '\0';
    if (truncated) {
        /* Bound UI memory even when a faulty helper produces an enormous log. */
        const char suffix[] = "\n[Display limited to the first 64 KiB.]";
        memcpy(failure_details + sizeof(failure_details) - sizeof(suffix), suffix, sizeof(suffix));
    }

    show_failure(NULL);
    fprintf(stderr, "Recovery action failed: %s\n", message);
}

static void poll_worker(lv_timer_t *timer)
{
    (void)timer;
    if (!worker) {
        return;
    }
    if (!strcmp(operation, "information") && lv_tick_elaps(information_started) > 15000) {
        /* Only read-only inspection is interruptible. Never cancel a rollback. */
        if (!information_timed_out) {
            kill(-worker, SIGTERM);
            information_timed_out = true;
        } else if (lv_tick_elaps(information_started) > 17000) {
            kill(-worker, SIGKILL);
        }
    }

    /* Drain even oversized replies so the child cannot block on a full pipe. */
    char buffer[4096];
    ssize_t count;
    while ((count = read(worker_fd, buffer, sizeof(buffer))) > 0) {
        if (response_length + (size_t)count > MAX_RESPONSE) {
            response_overflow = true;
            continue;
        }
        char *new_response = realloc(response, response_length + (size_t)count + 1);
        if (!new_response) {
            response_overflow = true;
            continue;
        }
        response = new_response;
        memcpy(response + response_length, buffer, (size_t)count);
        response_length += (size_t)count;
        response[response_length] = 0;
    }

    int status;
    if (waitpid(worker, &status, WNOHANG) <= 0) {
        return;
    }

    /* A child can exit between the last read and waitpid; drain its final bytes. */
    while ((count = read(worker_fd, buffer, sizeof(buffer))) > 0) {
        if (response_length + (size_t)count > MAX_RESPONSE) {
            response_overflow = true;
            continue;
        }
        char *new_response = realloc(response, response_length + (size_t)count + 1);
        if (!new_response) {
            response_overflow = true;
            continue;
        }
        response = new_response;
        memcpy(response + response_length, buffer, (size_t)count);
        response_length += (size_t)count;
        response[response_length] = 0;
    }

    close(worker_fd);
    worker_fd = -1;
    worker = 0;

    fprintf(stderr, "Recovery action %s finished: %d\n", operation, status);

    if (information_timed_out) {
        failed("Reading system information took too long. System Recovery is still available.");
        return;
    }
    if (response_overflow) {
        failed("The recovery response is too large to display.");
        return;
    }

    if (!WIFEXITED(status) || WEXITSTATUS(status)) {
        char details[8192] = {0};
        FILE *errors = action_log_current ? fopen(ERROR_LOG, "r") : NULL;
        if (errors) {
            size_t n = fread(details, 1, sizeof(details) - 1, errors);
            details[n] = 0;
            fclose(errors);
        }
        failed(*details ? details
               : response && *response
                   ? response
                   : "The recovery operation failed without returning details.");
        return;
    }

    if (!strcmp(operation, "list") || !strcmp(operation, "information")) {
        jv report = jv_parse(response ? response : "");
        if (jv_get_kind(report) == JV_KIND_OBJECT) {
            if (information_page) {
                information(report);
            } else {
                listed(report);
            }
        } else {
            failed(information_page
                       ? "System information could not be read."
                       : "The recovery engine returned an unreadable snapshot catalog.");
        }
        jv_free(report);
    } else if (!strcmp(operation, "create")) {
        /* The refreshed catalog verifies the saved marker before hiding warnings. */
        start_action("list", NULL);
    } else if (strcmp(operation, "restart")) {
        page("Your system has been restored",
            "Restart to use the restored system. Your personal files were left unchanged.");
        spacer();
        button(footer(), "Restart", restart, NULL, true);
    }
}

static void start_action(const char *action, const char *identifier)
{
    if (worker) {
        return;
    }
    close_dialog();
    bool info = !strcmp(action, "information");
    if (info || !strcmp(action, "list")) {
        information_page = info;
    }
    bool loading = info || !strcmp(action, "list");
    bool restarting = !strcmp(action, "restart");
    bool creating = !strcmp(action, "create");
    page(info         ? "System Information"
         : loading    ? "AnduinOS Recovery"
         : restarting ? "Restarting…"
         : creating   ? "Saving current system state…"
                      : "Restoring your system…",
        info         ? "Reading system information…"
        : loading    ? "Looking for system snapshots…"
        : restarting ? "Please wait."
                     : "Keep the computer powered on. This may take a few minutes.");
    for (uint32_t i = 0; i < lv_obj_get_child_count(tabs); i++) {
        lv_obj_add_state(lv_obj_get_child(tabs, i), LV_STATE_DISABLED);
    }
    lv_obj_t *spinner = lv_spinner_create(body);
    lv_obj_set_size(spinner, 40, 40);
    snprintf(operation, sizeof(operation), "%s", action);
    information_started = lv_tick_get();
    information_timed_out = false;

    free(response);
    response = NULL;
    response_length = 0;
    response_overflow = false;

    /* Never attach a previous attempt's log to a pipe/fork/startup failure. */
    action_log_current = false;
    failure_summary[0] = '\0';
    failure_details[0] = '\0';
    int errors = open(ERROR_LOG, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (errors < 0) {
        failed(strerror(errno));
        return;
    }
    action_log_current = true;

    int fds[2];
    if (pipe2(fds, O_CLOEXEC)) {
        int error = errno;
        close(errors);
        failed(strerror(error));
        return;
    }

    /* The parent polls without blocking LVGL; the child owns helper execution. */
    worker = fork();
    if (!worker) {
        /* Inspection timeout must also stop subprocesses holding the pipe open. */
        if (setpgid(0, 0)) {
            _exit(126);
        }
        close(fds[0]);
        dup2(fds[1], STDOUT_FILENO);
        close(fds[1]);

        /* Diagnostic output must never become part of the JSON catalog. */
        dup2(errors, STDERR_FILENO);
        close(errors);

        const char *args[] = {"/usr/bin/recovery-action", action, identifier, NULL};
        execv(args[0], (char *const *)args);
        perror("Could not start recovery-action");
        _exit(127);
    }
    int fork_error = errno;
    close(errors);
    close(fds[1]);

    if (worker < 0) {
        close(fds[0]);
        worker = 0;
        failed(strerror(fork_error));
        return;
    }

    worker_fd = fds[0];
    fcntl(worker_fd, F_SETFL, O_NONBLOCK);
}

static void input_added(lv_indev_t *input, lv_evdev_type_t type, void *data)
{
    (void)data;
    if (type == LV_EVDEV_TYPE_KEY) {
        lv_indev_set_group(input, dialog ? dialog_group : main_group);
    } else if (lv_indev_get_type(input) == LV_INDEV_TYPE_POINTER) {
        if (!pointer) {
            pointer = lv_image_create(lv_layer_top());
            lv_image_set_src(pointer, &fluent_cursor);
            lv_obj_set_style_translate_x(pointer, -FLUENT_CURSOR_HOT_X, 0);
            lv_obj_set_style_translate_y(pointer, -FLUENT_CURSOR_HOT_Y, 0);
            lv_obj_set_clickable(pointer, false);
        }
        lv_indev_set_cursor(input, pointer);
    }
}

int main(int argc, char **argv)
{
    terminal = open("/dev/tty0", O_RDWR | O_CLOEXEC);
    if (argc == 2 && !strcmp(argv[1], "text")) {
        restore_terminal();
        return 0;
    }
    if (terminal >= 0) {
        ioctl(terminal, KDGETMODE, &old_mode);
    }

    /* Signal handlers only set a flag; shutdown stays on the UI thread. */
    atexit(restore_terminal);
    signal(SIGTERM, stop);
    signal(SIGINT, stop);
    lv_init();

    /* Use framebuffer only when requested; DRM is the default backend. */
    lv_display_t *display;
    if (argc == 2 && !strcmp(argv[1], "fbdev")) {
        if (access("/dev/fb0", R_OK | W_OK)) {
            return 1;
        }
        display = lv_linux_fbdev_create();
        lv_linux_fbdev_set_file(display, "/dev/fb0");
    } else {
        display = lv_linux_drm_create();
        char *path = lv_linux_drm_find_device_path();
        if (!path) {
            return 1;
        }
        lv_result_t result = lv_linux_drm_set_file(display, path, -1);
        lv_free(path);
        if (result != LV_RESULT_OK) {
            return 1;
        }
    }

    int width = lv_display_get_horizontal_resolution(display);
    int height = lv_display_get_vertical_resolution(display);
    if (width < 640 || height < 480) {
        return 1;
    }
    if (terminal >= 0) {
        ioctl(terminal, KDSETMODE, KD_GRAPHICS);
    }

    /* Snapshot titles may contain CJK text even though the interface is English. */
    body_font = lv_freetype_font_create("/usr/share/fonts/DejaVuSans.ttf",
        LV_FREETYPE_FONT_RENDER_MODE_BITMAP,
        18,
        LV_FREETYPE_FONT_STYLE_NORMAL);
    title_font = lv_freetype_font_create("/usr/share/fonts/DejaVuSans.ttf",
        LV_FREETYPE_FONT_RENDER_MODE_BITMAP,
        28,
        LV_FREETYPE_FONT_STYLE_BOLD);
    lv_font_t *cjk = lv_freetype_font_create("/usr/share/fonts/wqy-microhei.ttc",
        LV_FREETYPE_FONT_RENDER_MODE_BITMAP,
        18,
        LV_FREETYPE_FONT_STYLE_NORMAL);
    if (!body_font || !title_font || !cjk) {
        return 1;
    }
    body_font->fallback = cjk;
    lv_theme_default_init(
        display, lv_color_hex(ACCENT_BLUE), lv_color_hex(0x92d4a7), true, body_font);

    main_group = lv_group_create();
    select_group(main_group);
    lv_obj_set_style_bg_color(lv_screen_active(), lv_color_hex(0x242424), 0);
    body = lv_obj_create(lv_screen_active());
    lv_obj_remove_style_all(body);
    lv_obj_set_size(body, width > 1024 ? 960 : width - 48, height - 64);
    lv_obj_align(body, LV_ALIGN_TOP_MID, 0, 32);
    lv_obj_set_flex_flow(body, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(body, 18, 0);
    lv_obj_set_scrollable(body, false);

    /* Input discovery and helper polling share the LVGL event loop. */
    if (lv_evdev_discovery_start(input_added, NULL) != LV_RESULT_OK) {
        return 1;
    }
    lv_timer_create(poll_worker, 100, NULL);
    start_action("list", NULL);
    while (!stopping) {
        uint32_t delay = lv_timer_handler();
        usleep((delay > 20 ? 20 : delay) * 1000);
    }

    /* Do not abandon a confirmed operation merely because the display stops. */
    while (worker) {
        poll_worker(NULL);
        usleep(100000);
    }

    return 0;
}
