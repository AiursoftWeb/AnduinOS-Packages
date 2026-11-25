/* SPDX-License-Identifier: LGPL-2.1-or-later */
#include "anduin-config.h"
#include "uresourced-config.h"
#include <errno.h>
#include <string.h>

G_DEFINE_AUTOPTR_CLEANUP_FUNC (gchar, g_free)

#define SOURCES "AnduinOSSources"

static gboolean
merge_file (GKeyFile *result, const char *path, gboolean required,
            gboolean app_only, GError **error)
{
  g_autoptr(GKeyFile) input = g_key_file_new ();
  g_autoptr(GError) local = NULL;
  g_auto(GStrv) groups = NULL;

  if (!g_key_file_load_from_file (input, path, G_KEY_FILE_NONE, &local))
    {
      if (!required && g_error_matches (local, G_FILE_ERROR, G_FILE_ERROR_NOENT))
        return TRUE;
      g_propagate_prefixed_error (error, g_steal_pointer (&local), "%s: ", path);
      return FALSE;
    }
  groups = g_key_file_get_groups (input, NULL);
  for (char **group = groups; *group; group++)
    {
      g_auto(GStrv) keys = NULL;
      if (g_str_equal (*group, SOURCES) || (app_only && !g_str_equal (*group, "AppBoost")))
        continue;
      keys = g_key_file_get_keys (input, *group, NULL, NULL);
      for (char **key = keys; *key; key++)
        {
          g_autofree char *value = g_key_file_get_value (input, *group, *key, NULL);
          g_autofree char *origin = g_strdup_printf ("%s.%s", *group, *key);
          g_key_file_set_value (result, *group, *key, value);
          g_key_file_set_string (result, SOURCES, origin, path);
        }
    }
  return TRUE;
}

static gboolean
validate (GKeyFile *file, GError **error)
{
  const char *groups[] = { "Global", "ActiveUser", "SessionSlice", "AppBoost", NULL };
  g_key_file_get_boolean (file, "AnduinOS", "Enabled", error);
  if (*error)
    return FALSE;
  for (const char **group = groups; *group; group++)
    {
      g_auto(GStrv) keys = g_key_file_get_keys (file, *group, NULL, NULL);
      if (!keys)
        continue;
      for (char **key = keys; *key; key++)
        {
          if (g_str_has_suffix (*key, "Weight") || g_str_has_suffix (*key, "WeightInc"))
            {
              gint64 value = g_key_file_get_int64 (file, *group, *key, error);
              gint64 minimum = g_str_has_suffix (*key, "Inc") ? 0 : 1;
              if (*error)
                return FALSE;
              if (value < minimum || value > 10000)
                goto invalid;
            }
          else if (g_str_has_suffix (*key, "MemoryMin") || g_str_has_suffix (*key, "MemoryLow"))
            {
              g_autofree char *text = g_key_file_get_string (file, *group, *key, NULL);
              char *end;
              guint64 value;
              errno = 0;
              value = g_ascii_strtoull (text, &end, 10);
              if (!g_ascii_isdigit (*text) || errno || end == text)
                goto invalid;
              if (*end && (!strchr ("KMGT%", *end) || end[1]))
                goto invalid;
              if (*end == '%' && value > 100)
                goto invalid;
              if (*end && *end != '%')
                {
                  const char *units = "KMGT";
                  guint shift = 10 * (strchr (units, *end) - units + 1);
                  if (value > (G_MAXUINT64 >> shift))
                    goto invalid;
                }
            }
          continue;
invalid:
          g_set_error (error, G_KEY_FILE_ERROR, G_KEY_FILE_ERROR_INVALID_VALUE,
                       "Invalid value for [%s] %s", *group, *key);
          return FALSE;
        }
    }
  const char *types[] = { "CPU", "IO", NULL };
  for (const char **type = types; *type; type++)
    {
      g_autofree char *active = g_strdup_printf ("Active%sWeight", *type);
      g_autofree char *boost = g_strdup_printf ("Boost%sWeightInc", *type);
      if (g_key_file_get_integer (file, "AppBoost", active, NULL)
          + g_key_file_get_integer (file, "AppBoost", boost, NULL) > 10000)
        {
          g_set_error (error, G_KEY_FILE_ERROR, G_KEY_FILE_ERROR_INVALID_VALUE,
                       "[AppBoost] %s plus %s exceeds 10000", active, boost);
          return FALSE;
        }
    }
  return TRUE;
}

GKeyFile *
anduin_config_load (gboolean user, GError **error)
{
  g_autoptr(GKeyFile) result = g_key_file_new ();
  g_autoptr(GDir) directory = NULL;
  g_autoptr(GError) local = NULL;
  g_autolist(gchar) names = NULL;
  const char *name;
  const char *dropins = SYSCONFDIR "/uresourced.conf.d";

  if (!merge_file (result, "/usr/share/anduinos-uresourced/defaults.conf", TRUE, FALSE, error)
      || !merge_file (result, SYSCONFDIR "/uresourced.conf", FALSE, FALSE, error))
    return NULL;
  directory = g_dir_open (dropins, 0, &local);
  if (!directory && !g_error_matches (local, G_FILE_ERROR, G_FILE_ERROR_NOENT))
    {
      g_propagate_error (error, g_steal_pointer (&local));
      return NULL;
    }
  while (directory && (name = g_dir_read_name (directory)))
    if (g_str_has_suffix (name, ".conf"))
      names = g_list_prepend (names, g_strdup (name));
  names = g_list_sort (names, (GCompareFunc) g_strcmp0);
  for (GList *item = names; item; item = item->next)
    {
      g_autofree char *path = g_build_filename (dropins, item->data, NULL);
      if (!merge_file (result, path, TRUE, FALSE, error))
        return NULL;
    }
  if (user)
    {
      g_autofree char *path = g_build_filename (g_get_user_config_dir (), "uresourced.conf", NULL);
      if (!merge_file (result, path, FALSE, TRUE, error))
        return NULL;
    }
  if (!validate (result, error))
    return NULL;
  return g_steal_pointer (&result);
}
