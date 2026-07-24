// ====================
// File: theme-sync.c
// Daemon: watch GNOME color-scheme, sync Flatpak GTK_THEME override
// Compile: gcc `pkg-config --cflags --libs glib-2.0 gio-2.0` -O2 -o theme-sync theme-sync.c
// Install to /usr/bin/anduinos-theme-sync and run via systemd user service
//
// When the user toggles dark/light in GNOME Settings (or via OOBE), this
// daemon picks up the color-scheme change via GSettings and immediately
// updates the Flatpak user override so that GTK3 Flatpak applications
// receive the matching GTK_THEME environment variable.
//
// Dark  → flatpak override --user --env=GTK_THEME=Adwaita:dark
// Light → flatpak override --user --env=GTK_THEME=Adwaita:light
//
// Startup is gated by a systemd ExecStartPre=sleep 5 to give the D-Bus
// session bus time to settle.  If the daemon still starts too early and
// GSettings can't reach dconf, Restart=on-failure provides a self-healing
// fallback — systemd will retry after 5 seconds.
// ====================

#include <glib.h>
#include <gio/gio.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>

#define FLATPAK_THEME_DARK  "Adwaita:dark"
#define FLATPAK_THEME_LIGHT "Adwaita:light"
#define FLATPAK_BIN         "/usr/bin/flatpak"

static GSettings    *settings  = NULL;
static GMainLoop    *main_loop = NULL;
static volatile sig_atomic_t running = 1;

static void handle_signal(int sig) {
    (void)sig;
    running = 0;
    if (main_loop)
        g_main_loop_quit(main_loop);
}

// ── Check whether the Flatpak user override already matches target ─────
// Avoids unnecessary disk writes on every login when the theme hasn't
// actually changed since last boot.
static gboolean is_theme_already_set(const char *target_theme) {
    g_autofree gchar *path = g_build_filename(
        g_get_home_dir(), ".local", "share", "flatpak",
        "overrides", "global", NULL);

    g_autoptr(GKeyFile) kf = g_key_file_new();
    g_autoptr(GError)   err = NULL;

    if (!g_key_file_load_from_file(kf, path, G_KEY_FILE_NONE, &err)) {
        // File doesn't exist or is unreadable — needs writing.
        g_clear_error(&err);
        return FALSE;
    }

    g_autofree gchar *current = g_key_file_get_string(
        kf, "Environment", "GTK_THEME", &err);
    if (err) {
        // Key not present — needs writing.
        g_clear_error(&err);
        return FALSE;
    }

    return (g_strcmp0(current, target_theme) == 0);
}

// ── Run /usr/bin/flatpak override --user --env=GTK_THEME=<theme> ───────
static void flatpak_set_theme(const char *theme) {
    if (is_theme_already_set(theme)) {
        g_message("GTK_THEME already %s — skipping override.", theme);
        return;
    }

    // Use absolute path; systemd user services can have a minimal $PATH.
    g_autofree gchar *cmd = g_strdup_printf(
        FLATPAK_BIN " override --user --env=GTK_THEME=%s", theme);

    g_autoptr(GError) error = NULL;
    int   exit_status = 0;
    g_autofree gchar *stdout_buf = NULL;
    g_autofree gchar *stderr_buf = NULL;

    if (!g_spawn_command_line_sync(cmd,
                                   &stdout_buf, &stderr_buf,
                                   &exit_status, &error)) {
        g_warning("Failed to spawn " FLATPAK_BIN ": %s", error->message);
        return;
    }

    if (exit_status != 0) {
        g_warning(FLATPAK_BIN " override exited %d: %s", exit_status,
                  stderr_buf ? stderr_buf : "(no stderr)");
        return;
    }

    g_message("Flatpak GTK_THEME set to: %s", theme);
}

// ── GSettings callback: color-scheme changed ─────────────────────────
static void on_color_scheme_changed(GSettings *s, const gchar *key,
                                    gpointer user_data) {
    (void)user_data;

    g_autofree gchar *scheme = g_settings_get_string(s, key);
    const char *theme = (scheme && g_strcmp0(scheme, "prefer-dark") == 0)
                            ? FLATPAK_THEME_DARK
                            : FLATPAK_THEME_LIGHT;

    g_message("color-scheme changed → %s → %s", scheme, theme);
    flatpak_set_theme(theme);
}

// ── Apply the current color-scheme on startup ────────────────────────
static void apply_current(void) {
    g_autofree gchar *scheme = g_settings_get_string(settings,
                                                     "color-scheme");
    const char *theme = (scheme && g_strcmp0(scheme, "prefer-dark") == 0)
                            ? FLATPAK_THEME_DARK
                            : FLATPAK_THEME_LIGHT;

    g_message("Initial color-scheme: %s → %s", scheme, theme);
    flatpak_set_theme(theme);
}

// ── main ─────────────────────────────────────────────────────────────
int main(void) {
    struct sigaction sa = { .sa_handler = handle_signal };
    sigaction(SIGINT,  &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    g_message("anduinos-theme-sync starting…");

    settings = g_settings_new("org.gnome.desktop.interface");
    apply_current();
    g_signal_connect(settings, "changed::color-scheme",
                     G_CALLBACK(on_color_scheme_changed), NULL);

    main_loop = g_main_loop_new(NULL, FALSE);
    g_main_loop_run(main_loop);

    g_message("anduinos-theme-sync shutting down.");
    g_object_unref(settings);
    g_main_loop_unref(main_loop);
    return EXIT_SUCCESS;
}
