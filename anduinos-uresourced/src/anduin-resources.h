/* SPDX-License-Identifier: LGPL-2.1-or-later */
#pragma once
#include <gio/gio.h>

void anduin_resources_remember (GVariant *parameters);
gboolean anduin_resources_restore (GError **error);
