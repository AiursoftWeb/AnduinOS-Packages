// SPDX-License-Identifier: GPL-3.0-or-later
//
// AnduinOS GTK3 Flatpak theme bridge
//
// Watches org.gnome.desktop.interface::gtk-theme and publishes the selected
// host GTK3 theme as a user-local, unmaintained Flatpak theme extension:
//
//   $XDG_DATA_HOME/flatpak/extension/
//     org.gtk.Gtk3theme.<theme>/<arch>/3.22/
//
// At startup, this removes GTK_THEME from the global user override only when
// its value exactly matches one of the two values written by the old daemon.
// It never resets the override, so unrelated user settings remain intact.
// GTK4 and libadwaita applications continue to follow the standard Settings
// portal and are not themed by this service.

#include <errno.h>
#include <gio/gio.h>
#include <glib-unix.h>
#include <glib.h>
#include <glib/gstdio.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define FLATPAK_BIN "/usr/bin/flatpak"
#define GTK3_EXTENSION_BRANCH "3.22"
#define LEGACY_THEME_DARK "Adwaita:dark"
#define LEGACY_THEME_LIGHT "Adwaita:light"
#define MARKER_FILE ".anduinos-theme-sync"
#define MARKER_GROUP "AnduinOS Theme Sync"
#define MARKER_VERSION 1

static GSettings *settings = NULL;
static GMainLoop *main_loop = NULL;
static gchar *flatpak_arch = NULL;
static guint retry_source_id = 0;

static gint compare_strings(gconstpointer left, gconstpointer right) {
    const gchar *left_string = *(gchar *const *)left;
    const gchar *right_string = *(gchar *const *)right;
    return g_strcmp0(left_string, right_string);
}

static gboolean is_safe_component(const gchar *value) {
    if (!value || !*value || strlen(value) > 200 ||
        g_str_equal(value, ".") || g_str_equal(value, "..")) {
        return FALSE;
    }

    for (const guchar *cursor = (const guchar *)value; *cursor; cursor++) {
        if (!(g_ascii_isalnum(*cursor) || *cursor == '-' || *cursor == '_' ||
              *cursor == '.' || *cursor == '+')) {
            return FALSE;
        }
    }

    return TRUE;
}

static gboolean cleanup_legacy_gtk_theme_override(void) {
    g_autofree gchar *override_path = g_build_filename(
        g_get_user_data_dir(), "flatpak", "overrides", "global", NULL);
    g_autoptr(GKeyFile) override = g_key_file_new();
    g_autoptr(GError) error = NULL;

    if (!g_key_file_load_from_file(override, override_path, G_KEY_FILE_NONE,
                                   &error)) {
        if (!g_error_matches(error, G_FILE_ERROR, G_FILE_ERROR_NOENT)) {
            g_warning("Cannot inspect the Flatpak global override '%s'; "
                      "leaving it unchanged: %s", override_path,
                      error->message);
        }
        return TRUE;
    }

    g_clear_error(&error);
    g_autofree gchar *gtk_theme = g_key_file_get_string(
        override, "Environment", "GTK_THEME", &error);
    if (!gtk_theme) {
        g_clear_error(&error);
        return TRUE;
    }
    if (!gtk_theme[0])
        return TRUE;

    if (!g_str_equal(gtk_theme, LEGACY_THEME_DARK) &&
        !g_str_equal(gtk_theme, LEGACY_THEME_LIGHT)) {
        g_message("Flatpak GTK_THEME override is user-defined; leaving it "
                  "unchanged: %s", gtk_theme);
        return TRUE;
    }

    const gchar *argv[] = {
        FLATPAK_BIN,
        "override",
        "--user",
        "--unset-env=GTK_THEME",
        NULL,
    };
    g_autofree gchar *standard_error = NULL;
    gint wait_status = 0;
    if (!g_spawn_sync(NULL, (gchar **)argv, NULL, G_SPAWN_STDOUT_TO_DEV_NULL,
                      NULL, NULL, NULL, &standard_error, &wait_status, &error)) {
        g_warning("Failed to remove the legacy Flatpak GTK_THEME override: %s",
                  error->message);
        return FALSE;
    }
    if (!g_spawn_check_wait_status(wait_status, &error)) {
        if (standard_error)
            g_strstrip(standard_error);
        g_warning("Failed to remove the legacy Flatpak GTK_THEME override: "
                  "%s%s%s", error->message,
                  standard_error && standard_error[0] ? ": " : "",
                  standard_error ? standard_error : "");
        return FALSE;
    }

    g_message("Removed legacy Flatpak GTK_THEME override: %s", gtk_theme);
    return TRUE;
}

static gboolean remove_tree(GFile *file, GError **error) {
    g_autoptr(GFileInfo) info = g_file_query_info(
        file, G_FILE_ATTRIBUTE_STANDARD_TYPE,
        G_FILE_QUERY_INFO_NOFOLLOW_SYMLINKS, NULL, error);

    if (!info) {
        if (error && *error &&
            g_error_matches(*error, G_IO_ERROR, G_IO_ERROR_NOT_FOUND)) {
            g_clear_error(error);
            return TRUE;
        }
        return FALSE;
    }

    if (g_file_info_get_file_type(info) == G_FILE_TYPE_DIRECTORY) {
        g_autoptr(GFileEnumerator) enumerator = g_file_enumerate_children(
            file, G_FILE_ATTRIBUTE_STANDARD_NAME,
            G_FILE_QUERY_INFO_NOFOLLOW_SYMLINKS, NULL, error);
        if (!enumerator)
            return FALSE;

        while (TRUE) {
            g_autoptr(GFileInfo) child_info =
                g_file_enumerator_next_file(enumerator, NULL, error);
            if (!child_info) {
                if (error && *error)
                    return FALSE;
                break;
            }

            g_autoptr(GFile) child = g_file_get_child(
                file, g_file_info_get_name(child_info));
            if (!remove_tree(child, error))
                return FALSE;
        }
    }

    return g_file_delete(file, NULL, error);
}

static gboolean copy_tree(GFile *source, GFile *destination, GError **error) {
    g_autoptr(GFileInfo) info = g_file_query_info(
        source,
        G_FILE_ATTRIBUTE_STANDARD_TYPE "," G_FILE_ATTRIBUTE_STANDARD_IS_SYMLINK,
        G_FILE_QUERY_INFO_NOFOLLOW_SYMLINKS, NULL, error);
    if (!info)
        return FALSE;

    GFileType type = g_file_info_get_file_type(info);
    if (type != G_FILE_TYPE_DIRECTORY) {
        return g_file_copy(source, destination,
                           G_FILE_COPY_OVERWRITE |
                               G_FILE_COPY_NOFOLLOW_SYMLINKS,
                           NULL, NULL, NULL, error);
    }

    if (!g_file_make_directory_with_parents(destination, NULL, error)) {
        if (!error || !*error ||
            !g_error_matches(*error, G_IO_ERROR, G_IO_ERROR_EXISTS)) {
            return FALSE;
        }
        g_clear_error(error);
    }

    g_autoptr(GFileEnumerator) enumerator = g_file_enumerate_children(
        source, G_FILE_ATTRIBUTE_STANDARD_NAME,
        G_FILE_QUERY_INFO_NOFOLLOW_SYMLINKS, NULL, error);
    if (!enumerator)
        return FALSE;

    while (TRUE) {
        g_autoptr(GFileInfo) child_info =
            g_file_enumerator_next_file(enumerator, NULL, error);
        if (!child_info) {
            if (error && *error)
                return FALSE;
            break;
        }

        const gchar *name = g_file_info_get_name(child_info);
        g_autoptr(GFile) source_child = g_file_get_child(source, name);
        g_autoptr(GFile) destination_child =
            g_file_get_child(destination, name);
        if (!copy_tree(source_child, destination_child, error))
            return FALSE;
    }

    return TRUE;
}

static gboolean checksum_tree_entry(GChecksum *checksum, GFile *file,
                                    const gchar *relative_path,
                                    GError **error) {
    g_autoptr(GFileInfo) info = g_file_query_info(
        file,
        G_FILE_ATTRIBUTE_STANDARD_TYPE ","
        G_FILE_ATTRIBUTE_STANDARD_SYMLINK_TARGET,
        G_FILE_QUERY_INFO_NOFOLLOW_SYMLINKS, NULL, error);
    if (!info)
        return FALSE;

    GFileType type = g_file_info_get_file_type(info);
    gchar type_byte = (gchar)type;
    g_checksum_update(checksum, (const guchar *)&type_byte, 1);
    g_checksum_update(checksum, (const guchar *)relative_path,
                      strlen(relative_path) + 1);

    if (type == G_FILE_TYPE_SYMBOLIC_LINK) {
        const gchar *target = g_file_info_get_symlink_target(info);
        if (target)
            g_checksum_update(checksum, (const guchar *)target,
                              strlen(target) + 1);
        return TRUE;
    }

    if (type == G_FILE_TYPE_REGULAR) {
        g_autoptr(GFileInputStream) stream =
            g_file_read(file, NULL, error);
        if (!stream)
            return FALSE;

        guchar buffer[64 * 1024];
        while (TRUE) {
            gssize bytes_read = g_input_stream_read(
                G_INPUT_STREAM(stream), buffer, sizeof(buffer), NULL, error);
            if (bytes_read < 0)
                return FALSE;
            if (bytes_read == 0)
                return TRUE;
            g_checksum_update(checksum, buffer, (gsize)bytes_read);
        }
    }

    if (type != G_FILE_TYPE_DIRECTORY) {
        g_set_error(error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED,
                    "Unsupported file type in GTK theme: %s", relative_path);
        return FALSE;
    }

    g_autoptr(GFileEnumerator) enumerator = g_file_enumerate_children(
        file, G_FILE_ATTRIBUTE_STANDARD_NAME,
        G_FILE_QUERY_INFO_NOFOLLOW_SYMLINKS, NULL, error);
    if (!enumerator)
        return FALSE;

    g_autoptr(GPtrArray) names = g_ptr_array_new_with_free_func(g_free);
    while (TRUE) {
        g_autoptr(GFileInfo) child_info =
            g_file_enumerator_next_file(enumerator, NULL, error);
        if (!child_info) {
            if (error && *error)
                return FALSE;
            break;
        }
        g_ptr_array_add(names, g_strdup(g_file_info_get_name(child_info)));
    }
    g_ptr_array_sort(names, compare_strings);

    for (guint i = 0; i < names->len; i++) {
        const gchar *name = g_ptr_array_index(names, i);
        g_autoptr(GFile) child = g_file_get_child(file, name);
        g_autofree gchar *child_relative = relative_path[0]
            ? g_build_filename(relative_path, name, NULL)
            : g_strdup(name);
        if (!checksum_tree_entry(checksum, child, child_relative, error))
            return FALSE;
    }

    return TRUE;
}

static gchar *checksum_tree(const gchar *path, GError **error) {
    g_autoptr(GChecksum) checksum = g_checksum_new(G_CHECKSUM_SHA256);
    g_autoptr(GFile) root = g_file_new_for_path(path);
    if (!checksum_tree_entry(checksum, root, "", error))
        return NULL;
    return g_strdup(g_checksum_get_string(checksum));
}

static gchar *find_theme_source(const gchar *theme_name) {
    g_autofree gchar *user_theme = g_build_filename(
        g_get_user_data_dir(), "themes", theme_name, "gtk-3.0", NULL);
    g_autofree gchar *legacy_user_theme = g_build_filename(
        g_get_home_dir(), ".themes", theme_name, "gtk-3.0", NULL);
    g_autofree gchar *local_theme = g_build_filename(
        "/usr/local/share/themes", theme_name, "gtk-3.0", NULL);
    g_autofree gchar *system_theme = g_build_filename(
        "/usr/share/themes", theme_name, "gtk-3.0", NULL);

    const gchar *candidates[] = {
        user_theme,
        legacy_user_theme,
        local_theme,
        system_theme,
        NULL,
    };

    for (guint i = 0; candidates[i]; i++) {
        g_autofree gchar *gtk_css =
            g_build_filename(candidates[i], "gtk.css", NULL);
        if (g_file_test(candidates[i], G_FILE_TEST_IS_DIR) &&
            g_file_test(gtk_css, G_FILE_TEST_IS_REGULAR)) {
            return g_strdup(candidates[i]);
        }
    }

    return NULL;
}

static gboolean read_marker(const gchar *target, gchar **theme_name,
                            gchar **source_path, gchar **checksum) {
    g_autofree gchar *marker_path =
        g_build_filename(target, MARKER_FILE, NULL);
    g_autoptr(GKeyFile) marker = g_key_file_new();
    g_autoptr(GError) error = NULL;
    if (!g_key_file_load_from_file(marker, marker_path, G_KEY_FILE_NONE,
                                   &error)) {
        return FALSE;
    }

    gint version = g_key_file_get_integer(marker, MARKER_GROUP, "Version",
                                          &error);
    if (error || version != MARKER_VERSION)
        return FALSE;

    *theme_name =
        g_key_file_get_string(marker, MARKER_GROUP, "Theme", &error);
    if (error)
        return FALSE;
    *source_path =
        g_key_file_get_string(marker, MARKER_GROUP, "Source", &error);
    if (error)
        return FALSE;
    *checksum =
        g_key_file_get_string(marker, MARKER_GROUP, "Checksum", &error);
    return error == NULL;
}

static gboolean write_marker(const gchar *target, const gchar *theme_name,
                             const gchar *source_path, const gchar *checksum,
                             GError **error) {
    g_autoptr(GKeyFile) marker = g_key_file_new();
    g_key_file_set_integer(marker, MARKER_GROUP, "Version", MARKER_VERSION);
    g_key_file_set_string(marker, MARKER_GROUP, "Theme", theme_name);
    g_key_file_set_string(marker, MARKER_GROUP, "Source", source_path);
    g_key_file_set_string(marker, MARKER_GROUP, "Checksum", checksum);

    gsize length = 0;
    g_autofree gchar *contents = g_key_file_to_data(marker, &length, error);
    if (!contents)
        return FALSE;

    g_autofree gchar *marker_path =
        g_build_filename(target, MARKER_FILE, NULL);
    return g_file_set_contents(marker_path, contents, (gssize)length, error);
}

static gboolean sync_theme(const gchar *theme_name) {
    if (!is_safe_component(theme_name)) {
        g_warning("Cannot expose unsafe GTK theme name: %s",
                  theme_name ? theme_name : "(null)");
        return TRUE;
    }

    g_autofree gchar *source = find_theme_source(theme_name);
    if (!source) {
        g_message("GTK3 files for theme '%s' were not found; leaving Flatpak "
                  "to use its fallback theme.", theme_name);
        return TRUE;
    }

    g_autoptr(GError) error = NULL;
    g_autofree gchar *source_checksum = checksum_tree(source, &error);
    if (!source_checksum) {
        g_warning("Failed to checksum GTK3 theme '%s': %s", theme_name,
                  error->message);
        return FALSE;
    }

    g_autofree gchar *extension_id =
        g_strdup_printf("org.gtk.Gtk3theme.%s", theme_name);
    g_autofree gchar *extension_parent = g_build_filename(
        g_get_user_data_dir(), "flatpak", "extension", extension_id,
        flatpak_arch, NULL);
    g_autofree gchar *target = g_build_filename(
        extension_parent, GTK3_EXTENSION_BRANCH, NULL);

    if (g_file_test(target, G_FILE_TEST_EXISTS)) {
        g_autofree gchar *managed_theme = NULL;
        g_autofree gchar *managed_source = NULL;
        g_autofree gchar *managed_checksum = NULL;
        if (!read_marker(target, &managed_theme, &managed_source,
                         &managed_checksum)) {
            g_warning("Flatpak GTK3 theme extension already exists and is not "
                      "managed by anduinos-theme-sync; leaving it unchanged: %s",
                      target);
            return TRUE;
        }

        if (g_str_equal(managed_theme, theme_name) &&
            g_str_equal(managed_source, source) &&
            g_str_equal(managed_checksum, source_checksum)) {
            g_message("Flatpak GTK3 theme extension is current: %s", theme_name);
            return TRUE;
        }
    }

    if (g_mkdir_with_parents(extension_parent, 0755) != 0) {
        g_warning("Failed to create Flatpak extension directory '%s': %s",
                  extension_parent, g_strerror(errno));
        return FALSE;
    }

    g_autofree gchar *staging = g_strdup_printf(
        "%s/.%s.tmp-%ld", extension_parent, GTK3_EXTENSION_BRANCH,
        (long)getpid());
    g_autofree gchar *backup = g_strdup_printf(
        "%s/.%s.old-%ld", extension_parent, GTK3_EXTENSION_BRANCH,
        (long)getpid());
    g_autoptr(GFile) staging_file = g_file_new_for_path(staging);
    g_autoptr(GFile) backup_file = g_file_new_for_path(backup);

    if (!remove_tree(staging_file, &error)) {
        g_warning("Failed to remove stale staging directory '%s': %s", staging,
                  error->message);
        return FALSE;
    }
    g_clear_error(&error);
    if (!remove_tree(backup_file, &error)) {
        g_warning("Failed to remove stale backup directory '%s': %s", backup,
                  error->message);
        return FALSE;
    }
    g_clear_error(&error);

    g_autoptr(GFile) source_file = g_file_new_for_path(source);
    if (!copy_tree(source_file, staging_file, &error) ||
        !write_marker(staging, theme_name, source, source_checksum, &error)) {
        g_warning("Failed to stage GTK3 theme '%s': %s", theme_name,
                  error->message);
        g_clear_error(&error);
        if (!remove_tree(staging_file, &error))
            g_warning("Failed to clean staging directory '%s': %s", staging,
                      error->message);
        return FALSE;
    }

    gboolean had_target = g_file_test(target, G_FILE_TEST_EXISTS);
    if (had_target && g_rename(target, backup) != 0) {
        g_warning("Failed to move existing extension '%s': %s", target,
                  g_strerror(errno));
        g_clear_error(&error);
        if (!remove_tree(staging_file, &error))
            g_warning("Failed to clean staging directory '%s': %s", staging,
                      error->message);
        return FALSE;
    }

    if (g_rename(staging, target) != 0) {
        gint saved_errno = errno;
        if (had_target && g_rename(backup, target) != 0) {
            g_critical("Failed to restore previous extension '%s': %s", target,
                       g_strerror(errno));
        }
        g_warning("Failed to publish extension '%s': %s", target,
                  g_strerror(saved_errno));
        return FALSE;
    }

    if (had_target) {
        g_clear_error(&error);
        if (!remove_tree(backup_file, &error))
            g_warning("Published theme but failed to remove backup '%s': %s",
                      backup, error->message);
    }

    g_message("Published GTK3 Flatpak theme extension: %s (%s)", theme_name,
              extension_id);
    g_message("Already-running GTK3 Flatpak applications must be restarted "
              "to use the new theme.");
    return TRUE;
}

static gchar *get_flatpak_arch(GError **error) {
    const gchar *argv[] = {FLATPAK_BIN, "--default-arch", NULL};
    g_autofree gchar *standard_output = NULL;
    gint wait_status = 0;

    if (!g_spawn_sync(NULL, (gchar **)argv, NULL,
                      G_SPAWN_STDERR_TO_DEV_NULL, NULL, NULL,
                      &standard_output, NULL, &wait_status, error)) {
        return NULL;
    }
    if (!g_spawn_check_wait_status(wait_status, error))
        return NULL;

    g_strstrip(standard_output);
    if (!is_safe_component(standard_output)) {
        g_set_error(error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA,
                    "Flatpak returned an invalid architecture: '%s'",
                    standard_output);
        return NULL;
    }

    return g_steal_pointer(&standard_output);
}

static gboolean apply_current_theme(void) {
    g_autofree gchar *theme_name =
        g_settings_get_string(settings, "gtk-theme");
    g_message("Selected host GTK theme: %s", theme_name);
    return sync_theme(theme_name);
}

static gboolean retry_current_theme(gpointer user_data) {
    (void)user_data;
    retry_source_id = 0;
    if (!apply_current_theme()) {
        g_message("GTK3 theme synchronization will be retried in 5 seconds");
        retry_source_id = g_timeout_add_seconds(5, retry_current_theme, NULL);
    }
    return G_SOURCE_REMOVE;
}

static void sync_or_schedule_retry(void) {
    if (retry_source_id) {
        g_source_remove(retry_source_id);
        retry_source_id = 0;
    }

    if (!apply_current_theme()) {
        g_message("GTK3 theme synchronization will be retried in 5 seconds");
        retry_source_id = g_timeout_add_seconds(5, retry_current_theme, NULL);
    }
}

static void on_gtk_theme_changed(GSettings *changed_settings,
                                 const gchar *key, gpointer user_data) {
    (void)changed_settings;
    (void)key;
    (void)user_data;
    sync_or_schedule_retry();
}

static gboolean on_shutdown_signal(gpointer user_data) {
    (void)user_data;
    if (main_loop)
        g_main_loop_quit(main_loop);
    return G_SOURCE_REMOVE;
}

int main(void) {
    g_message("anduinos-theme-sync starting as a GTK3 Flatpak theme bridge");

    g_autoptr(GError) error = NULL;
    flatpak_arch = get_flatpak_arch(&error);
    if (!flatpak_arch) {
        g_warning("Failed to determine Flatpak architecture: %s",
                  error->message);
        return EXIT_FAILURE;
    }

    if (!cleanup_legacy_gtk_theme_override())
        return EXIT_FAILURE;

    settings = g_settings_new("org.gnome.desktop.interface");
    g_signal_connect(settings, "changed::gtk-theme",
                     G_CALLBACK(on_gtk_theme_changed), NULL);

    main_loop = g_main_loop_new(NULL, FALSE);
    g_unix_signal_add(SIGINT, on_shutdown_signal, NULL);
    g_unix_signal_add(SIGTERM, on_shutdown_signal, NULL);
    sync_or_schedule_retry();
    g_main_loop_run(main_loop);

    g_message("anduinos-theme-sync shutting down");
    if (retry_source_id)
        g_source_remove(retry_source_id);
    g_clear_pointer(&flatpak_arch, g_free);
    g_clear_object(&settings);
    g_clear_pointer(&main_loop, g_main_loop_unref);
    return EXIT_SUCCESS;
}
