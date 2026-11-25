/* SPDX-License-Identifier: LGPL-2.1-or-later */
#include "anduin-resources.h"
#include <glib/gstdio.h>
#include <unistd.h>
#include <errno.h>
#include <string.h>

/* systemd's runtime SetUnitProperties writes one 50-PROPERTY.conf per key.
 * Remember those exact files before taking ownership; never revert a whole
 * unit or touch the administrator's /etc drop-ins. The service's private
 * runtime journal also permits ExecStopPost to recover after a crash. */
static GKeyFile *journal;

static char *
journal_path (void)
{
  const char *directory = g_getenv ("RUNTIME_DIRECTORY");
  return directory ? g_build_filename (directory, "properties", NULL) : NULL;
}

void
anduin_resources_remember (GVariant *parameters)
{
  g_autofree char *record = journal_path ();
  g_autoptr(GVariant) properties = NULL;
  g_autofree char *control = NULL;
  GVariantIter iter;
  const char *unit, *property;
  GVariant *value;
  gboolean runtime;

  if (!record)
    g_error ("Resource policy requires a systemd RuntimeDirectory");
  if (!journal)
    journal = g_key_file_new ();
  g_variant_get (parameters, "(&sb@a(sv))", &unit, &runtime, &properties);
  if (!runtime || strchr (unit, '/') || strchr (unit, '[') || strchr (unit, ']'))
    g_error ("Invalid resource unit");
  control = geteuid () == 0 ? g_strdup ("/run/systemd/system.control")
    : g_build_filename (g_get_user_runtime_dir (), "systemd/user.control", NULL);
  g_variant_iter_init (&iter, properties);
  while (g_variant_iter_next (&iter, "(&sv)", &property, &value))
    {
      g_autoptr(GVariant) current = value;
      g_autofree char *group = g_strdup_printf ("%s/%s", unit, property);
      g_autofree char *relative = g_strdup_printf ("%s.d/50-%s.conf", unit, property);
      g_autofree char *path = g_build_filename (control, relative, NULL);
      g_autoptr(GError) error = NULL;
      if (!g_str_equal (property, "CPUWeight") && !g_str_equal (property, "IOWeight")
          && !g_str_equal (property, "MemoryMin") && !g_str_equal (property, "MemoryLow"))
        g_error ("Unexpected resource property");
      if (!g_key_file_has_group (journal, group))
        {
          g_autofree char *previous = NULL;
          gsize length;
          if (g_file_get_contents (path, &previous, &length, &error))
            {
              g_autofree char *encoded = g_base64_encode ((guchar *) previous, length);
              g_key_file_set_string (journal, group, "Original", encoded);
            }
          else if (g_error_matches (error, G_FILE_ERROR, G_FILE_ERROR_NOENT))
            g_key_file_set_string (journal, group, "Original", "-");
          else
            g_error ("Cannot preserve %s: %s", path, error->message);
        }
      g_key_file_set_uint64 (journal, group, "Last", g_variant_get_uint64 (value));
    }
  g_autofree char *data = g_key_file_to_data (journal, NULL, NULL);
  g_autoptr(GError) error = NULL;
  if (!g_file_set_contents (record, data, -1, &error))
    g_error ("Cannot preserve resource policy: %s", error->message);
  g_chmod (record, 0600);
}

gboolean
anduin_resources_restore (GError **error)
{
  g_autofree char *record = journal_path ();
  g_autoptr(GKeyFile) saved = g_key_file_new ();
  g_autoptr(GDBusConnection) bus = NULL;
  g_autofree char *control = NULL;
  g_auto(GStrv) groups = NULL;
  g_autoptr(GError) local = NULL;
  gboolean changed = FALSE;

  if (!record || !g_key_file_load_from_file (saved, record, G_KEY_FILE_NONE, &local))
    {
      if (!record || g_error_matches (local, G_FILE_ERROR, G_FILE_ERROR_NOENT))
        return TRUE;
      g_propagate_error (error, g_steal_pointer (&local));
      return FALSE;
    }
  control = geteuid () == 0 ? g_strdup ("/run/systemd/system.control")
    : g_build_filename (g_get_user_runtime_dir (), "systemd/user.control", NULL);
  groups = g_key_file_get_groups (saved, NULL);
  for (char **group = groups; *group; group++)
    {
      g_auto(GStrv) parts = g_strsplit (*group, "/", -1);
      g_autofree char *relative = NULL, *path = NULL, *original = NULL;
      g_autoptr(GKeyFile) live = g_key_file_new ();
      g_auto(GStrv) sections = NULL;
      guint64 last;
      if (g_strv_length (parts) != 2 || strchr (parts[0], '[')
          || (!g_str_equal (parts[1], "CPUWeight") && !g_str_equal (parts[1], "IOWeight")
              && !g_str_equal (parts[1], "MemoryMin") && !g_str_equal (parts[1], "MemoryLow")))
        {
          g_set_error_literal (error, G_FILE_ERROR, G_FILE_ERROR_INVAL, "Invalid resource journal");
          return FALSE;
        }
      relative = g_strdup_printf ("%s.d/50-%s.conf", parts[0], parts[1]);
      path = g_build_filename (control, relative, NULL);
      if (!g_key_file_load_from_file (live, path, G_KEY_FILE_NONE, NULL))
        continue;
      sections = g_key_file_get_groups (live, NULL);
      last = g_key_file_get_uint64 (saved, *group, "Last", NULL);
      g_autoptr(GError) parse_error = NULL;
      guint64 current = sections[0]
        ? g_key_file_get_uint64 (live, sections[0], parts[1], &parse_error) : G_MAXUINT64;
      if (parse_error || current != last)
        {
          g_message ("Preserving externally changed resource property: %s", path);
          continue;
        }
      original = g_key_file_get_string (saved, *group, "Original", NULL);
      if (!original)
        {
          g_set_error_literal (error, G_FILE_ERROR, G_FILE_ERROR_INVAL, "Incomplete resource journal");
          return FALSE;
        }
      if (g_strcmp0 (original, "-") == 0)
        {
          if (g_unlink (path) && errno != ENOENT)
            {
              g_set_error (error, G_FILE_ERROR, g_file_error_from_errno (errno),
                           "Cannot remove %s", path);
              return FALSE;
            }
        }
      else
        {
          gsize length;
          g_autofree guchar *data = g_base64_decode (original, &length);
          if (!g_file_set_contents (path, (char *) data, length, error))
            return FALSE;
        }
      changed = TRUE;
    }
  if (changed)
    {
      bus = g_bus_get_sync (geteuid () == 0 ? G_BUS_TYPE_SYSTEM : G_BUS_TYPE_SESSION, NULL, error);
      if (!bus)
        return FALSE;
      g_autoptr(GVariant) reply = g_dbus_connection_call_sync (
        bus, "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
        "org.freedesktop.systemd1.Manager", "Reload", NULL, NULL,
        G_DBUS_CALL_FLAGS_NONE, 30000, NULL, error);
      if (!reply)
        return FALSE;
    }
  g_unlink (record);
  return TRUE;
}
