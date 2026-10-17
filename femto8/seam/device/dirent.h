/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave seam (device-only): <dirent.h> for the pi32v2 target.
 *
 * femto8 lists carts through <dirent.h> (its cart browser and Lua's `ls()`). newlib as
 * shipped for pi32v2 has no dirent at all (`sys/dirent.h` is "#error not supported"), so
 * p8_lua.c does not compile for the device without it. The device has no directory tree -
 * the cart lives in the SLOOP store, not a filesystem - so opendir() reports no directory
 * and the browser simply finds nothing.
 *
 * This file is reached only from the device build (`-Ifemto8/seam/device`); the simulator
 * and the desktop build keep the platform's real <dirent.h>, which is why it does not live
 * in femto8/seam/ next to the other headers. */
#pragma once

#include <stddef.h>

typedef struct __pv_dir DIR;

struct dirent {
  char d_name[256];
};

DIR *opendir(const char *name);
struct dirent *readdir(DIR *dirp);
int closedir(DIR *dirp);
