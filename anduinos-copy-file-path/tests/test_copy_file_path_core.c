/*
 * test_copy_file_path_core.c — unit tests for anduinos-copy-file-path core helpers.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "../src/copy_file_path_core.h"

#include <glib.h>

static void
test_resolve_local_file_and_spaces (void)
{
    GFile *file = g_file_new_for_path ("/home/user/Pictures/Screenshot 2026-11-28.png");
    char *resolved = anduinos_copy_file_path_resolve_local_path (
        file,
        "file:///home/user/Pictures/Screenshot%202026-11-28.png");
    g_assert_cmpstr (resolved, ==, "/home/user/Pictures/Screenshot 2026-11-28.png");
    g_free (resolved);
    g_object_unref (file);
}

static void
test_resolve_unicode_path (void)
{
    GFile *file = g_file_new_for_path ("/home/user/桌面/测试文档.md");
    char *resolved = anduinos_copy_file_path_resolve_local_path (file, NULL);
    g_assert_cmpstr (resolved, ==, "/home/user/桌面/测试文档.md");
    g_free (resolved);
    g_object_unref (file);
}

static void
test_resolve_recent_fallback_to_activation_uri (void)
{
    GFile *recent = g_file_new_for_uri ("recent:///screenshot.png");
    char *resolved = anduinos_copy_file_path_resolve_local_path (
        recent,
        "file:///home/user/Downloads/screenshot.png");
    g_assert_cmpstr (resolved, ==, "/home/user/Downloads/screenshot.png");
    g_free (resolved);
    g_object_unref (recent);
}

static void
test_resolve_virtual_locations_return_null (void)
{
    GFile *trash = g_file_new_for_uri ("trash:///deleted.txt");
    char *resolved_trash = anduinos_copy_file_path_resolve_local_path (
        trash,
        "trash:///deleted.txt");
    g_assert_null (resolved_trash);
    g_object_unref (trash);

    GFile *network = g_file_new_for_uri ("network:///");
    char *resolved_net = anduinos_copy_file_path_resolve_local_path (network, NULL);
    g_assert_null (resolved_net);
    g_object_unref (network);

    g_assert_null (anduinos_copy_file_path_resolve_local_path (NULL, NULL));
}

static void
test_join_single_and_multiple_paths (void)
{
    const char *single[] = { "/home/user/file with spaces.txt" };
    char *joined_single = anduinos_copy_file_path_join_paths (single, 1);
    g_assert_cmpstr (joined_single, ==, "/home/user/file with spaces.txt");
    g_free (joined_single);

    const char *multiple[] = {
        "/home/user/first.txt",
        "/home/user/second dir",
        "/home/user/third.png",
    };
    char *joined_multi = anduinos_copy_file_path_join_paths (multiple, 3);
    g_assert_cmpstr (
        joined_multi,
        ==,
        "/home/user/first.txt\n/home/user/second dir\n/home/user/third.png");
    g_free (joined_multi);

    const char *mixed[] = { NULL, "", "/home/user/only.txt", "" };
    char *joined_mixed = anduinos_copy_file_path_join_paths (mixed, 4);
    g_assert_cmpstr (joined_mixed, ==, "/home/user/only.txt");
    g_free (joined_mixed);

    const char *empty[] = { NULL, "" };
    g_assert_null (anduinos_copy_file_path_join_paths (empty, 2));
    g_assert_null (anduinos_copy_file_path_join_paths (NULL, 0));
}

static void
test_label_and_tip_msgids (void)
{
    g_assert_cmpstr (anduinos_copy_file_path_label_msgid (1), ==, "Copy Path");
    g_assert_cmpstr (anduinos_copy_file_path_label_msgid (2), ==, "Copy Paths");
    g_assert_cmpstr (
        anduinos_copy_file_path_tip_msgid (1),
        ==,
        "Copy the full path of the selected item to the clipboard");
    g_assert_cmpstr (
        anduinos_copy_file_path_tip_msgid (3),
        ==,
        "Copy the full paths of the selected items to the clipboard");
}

int
main (int argc, char **argv)
{
    g_test_init (&argc, &argv, NULL);

    g_test_add_func ("/copy-file-path/resolve-local-and-spaces", test_resolve_local_file_and_spaces);
    g_test_add_func ("/copy-file-path/resolve-unicode", test_resolve_unicode_path);
    g_test_add_func ("/copy-file-path/resolve-recent-fallback", test_resolve_recent_fallback_to_activation_uri);
    g_test_add_func ("/copy-file-path/resolve-virtual-null", test_resolve_virtual_locations_return_null);
    g_test_add_func ("/copy-file-path/join-paths", test_join_single_and_multiple_paths);
    g_test_add_func ("/copy-file-path/msgids", test_label_and_tip_msgids);

    return g_test_run ();
}
