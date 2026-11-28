/*
 * copy_file_path_core.h — pure path resolution and formatting helpers
 * for the AnduinOS Nautilus "Copy Path" extension.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#pragma once

#include <gio/gio.h>
#include <glib.h>

G_BEGIN_DECLS

/* Resolve a local filesystem path for a Nautilus item.
 * Prefers the item's own GFile location (handling local files, directories,
 * symlinks, .desktop files, and GVfs FUSE mounts) and falls back to its
 * activation URI when the location itself has no local path (for example
 * Recent or Starred entries backed by local files).
 * Returns NULL for virtual locations with no local path (e.g. trash:///). */
char *anduinos_copy_file_path_resolve_local_path (GFile      *location,
                                                  const char *activation_uri);

/* Join non-empty paths with '\n' and no surrounding quotes or trailing newline,
 * matching GNOME Files' plain-text clipboard representation.
 * Returns NULL when no non-empty paths are present. */
char *anduinos_copy_file_path_join_paths (const char *const *paths,
                                          gsize              count);

/* Return the untranslated gettext msgid for the context-menu label/tooltip. */
const char *anduinos_copy_file_path_label_msgid (gsize count);
const char *anduinos_copy_file_path_tip_msgid   (gsize count);

G_END_DECLS
