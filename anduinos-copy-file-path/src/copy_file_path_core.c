/*
 * copy_file_path_core.c — pure path resolution and formatting helpers
 * for the AnduinOS Nautilus "Copy Path" extension.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "copy_file_path_core.h"

char *
anduinos_copy_file_path_resolve_local_path (GFile      *location,
                                            const char *activation_uri)
{
    if (location != NULL)
    {
        char *path = g_file_get_path (location);
        if (path != NULL && path[0] != '\0')
        {
            return path;
        }
        g_free (path);
    }

    if (activation_uri != NULL && activation_uri[0] != '\0')
    {
        GFile *target = g_file_new_for_uri (activation_uri);
        if (target != NULL)
        {
            char *path = g_file_get_path (target);
            g_object_unref (target);
            if (path != NULL && path[0] != '\0')
            {
                return path;
            }
            g_free (path);
        }
    }

    return NULL;
}

char *
anduinos_copy_file_path_join_paths (const char *const *paths,
                                    gsize              count)
{
    if (paths == NULL || count == 0)
    {
        return NULL;
    }

    GString *buffer = g_string_new (NULL);
    gsize written = 0;

    for (gsize i = 0; i < count; i++)
    {
        if (paths[i] == NULL || paths[i][0] == '\0')
        {
            continue;
        }

        if (written > 0)
        {
            g_string_append_c (buffer, '\n');
        }
        g_string_append (buffer, paths[i]);
        written++;
    }

    if (written == 0)
    {
        g_string_free (buffer, TRUE);
        return NULL;
    }

    return g_string_free (buffer, FALSE);
}

const char *
anduinos_copy_file_path_label_msgid (gsize count)
{
    return (count <= 1) ? "Copy Path" : "Copy Paths";
}

const char *
anduinos_copy_file_path_tip_msgid (gsize count)
{
    return (count <= 1)
        ? "Copy the full path of the selected item to the clipboard"
        : "Copy the full paths of the selected items to the clipboard";
}
