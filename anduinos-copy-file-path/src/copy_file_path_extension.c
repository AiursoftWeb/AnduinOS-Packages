/*
 * copy_file_path_extension.c — GNOME Files (Nautilus 4) menu extension
 * adding "Copy Path" / "Copy Paths" to the file selection context menu.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "copy_file_path_core.h"

#include <gdk/gdk.h>
#include <glib/gi18n-lib.h>
#include <libintl.h>
#include <nautilus-extension.h>

#ifndef GETTEXT_PACKAGE
#define GETTEXT_PACKAGE "anduinos-copy-file-path"
#endif

#ifndef LOCALEDIR
#define LOCALEDIR "/usr/share/locale"
#endif

#define ANDUINOS_COPY_PATH_DATA_KEY "anduinos-copy-file-path-text"

#define ANDUINOS_TYPE_COPY_FILE_PATH_PROVIDER (anduinos_copy_file_path_provider_get_type ())
G_DECLARE_FINAL_TYPE (AnduinosCopyFilePathProvider,
                      anduinos_copy_file_path_provider,
                      ANDUINOS, COPY_FILE_PATH_PROVIDER,
                      GObject)

struct _AnduinosCopyFilePathProvider
{
    GObject parent_instance;
};

static void anduinos_copy_file_path_menu_provider_iface_init (NautilusMenuProviderInterface *iface);

G_DEFINE_DYNAMIC_TYPE_EXTENDED (
    AnduinosCopyFilePathProvider,
    anduinos_copy_file_path_provider,
    G_TYPE_OBJECT,
    0,
    G_IMPLEMENT_INTERFACE_DYNAMIC (
        NAUTILUS_TYPE_MENU_PROVIDER,
        anduinos_copy_file_path_menu_provider_iface_init))

static void
on_copy_path_activate (NautilusMenuItem *item,
                       gpointer          user_data)
{
    (void) user_data;

    const char *text = g_object_get_data (G_OBJECT (item), ANDUINOS_COPY_PATH_DATA_KEY);
    if (text == NULL || text[0] == '\0')
    {
        return;
    }

    GdkDisplay *display = gdk_display_get_default ();
    if (display == NULL)
    {
        return;
    }

    GdkClipboard *clipboard = gdk_display_get_clipboard (display);
    if (clipboard == NULL)
    {
        return;
    }

    gdk_clipboard_set_text (clipboard, text);
}

static GList *
anduinos_copy_file_path_get_file_items (NautilusMenuProvider *provider,
                                        GList                *files)
{
    (void) provider;

    if (files == NULL)
    {
        return NULL;
    }

    GPtrArray *paths = g_ptr_array_new_with_free_func (g_free);

    for (GList *node = files; node != NULL; node = node->next)
    {
        NautilusFileInfo *file_info = NAUTILUS_FILE_INFO (node->data);
        GFile *location = nautilus_file_info_get_location (file_info);
        char *activation_uri = nautilus_file_info_get_activation_uri (file_info);

        char *resolved = anduinos_copy_file_path_resolve_local_path (location, activation_uri);
        if (resolved != NULL)
        {
            g_ptr_array_add (paths, resolved);
        }

        g_free (activation_uri);
        g_clear_object (&location);
    }

    if (paths->len == 0)
    {
        g_ptr_array_unref (paths);
        return NULL;
    }

    char *joined = anduinos_copy_file_path_join_paths (
        (const char *const *) paths->pdata,
        paths->len);
    gsize count = paths->len;
    g_ptr_array_unref (paths);

    if (joined == NULL)
    {
        return NULL;
    }

    const char *label = dgettext (
        GETTEXT_PACKAGE,
        anduinos_copy_file_path_label_msgid (count));
    const char *tip = dgettext (
        GETTEXT_PACKAGE,
        anduinos_copy_file_path_tip_msgid (count));

    NautilusMenuItem *item = nautilus_menu_item_new (
        "AnduinosCopyFilePath::copy_path",
        label,
        tip,
        "edit-copy-symbolic");

    g_object_set_data_full (
        G_OBJECT (item),
        ANDUINOS_COPY_PATH_DATA_KEY,
        joined,
        g_free);

    g_signal_connect (
        item,
        "activate",
        G_CALLBACK (on_copy_path_activate),
        NULL);

    return g_list_append (NULL, item);
}

static void
anduinos_copy_file_path_menu_provider_iface_init (NautilusMenuProviderInterface *iface)
{
    iface->get_file_items = anduinos_copy_file_path_get_file_items;
    /* Do not override get_background_items: Nautilus already provides
     * "Copy Location" in the current-folder background context menu. */
}

static void
anduinos_copy_file_path_provider_init (AnduinosCopyFilePathProvider *self)
{
    (void) self;
}

static void
anduinos_copy_file_path_provider_class_init (AnduinosCopyFilePathProviderClass *klass)
{
    (void) klass;
}

static void
anduinos_copy_file_path_provider_class_finalize (AnduinosCopyFilePathProviderClass *klass)
{
    (void) klass;
}

static GType provider_types[1];

G_MODULE_EXPORT void
nautilus_module_initialize (GTypeModule *module)
{
    bindtextdomain (GETTEXT_PACKAGE, LOCALEDIR);
    bind_textdomain_codeset (GETTEXT_PACKAGE, "UTF-8");

    anduinos_copy_file_path_provider_register_type (module);
    provider_types[0] = ANDUINOS_TYPE_COPY_FILE_PATH_PROVIDER;
}

G_MODULE_EXPORT void
nautilus_module_shutdown (void)
{
}

G_MODULE_EXPORT void
nautilus_module_list_types (const GType **types,
                            int          *num_types)
{
    *types = provider_types;
    *num_types = G_N_ELEMENTS (provider_types);
}
