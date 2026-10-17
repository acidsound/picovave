/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave device stub: the rest of the C library newlib-without-an-OS does not bring.
 *
 * The JieLi toolchain's newlib is linked with no startup and no syscalls, so the
 * symbols below are simply absent from libc.a. femto8 (and the Lua inside it) still
 * references them, so the device defines them here. None of them can do anything
 * useful on a keyboard with no filesystem, no environment and no RTC:
 *
 *   getenv/access/mkdir/stat/opendir/readdir  report "nothing there" - the cart is
 *                                             in the SLOOP store, not a file tree
 *   time/localtime                            a monotonic clock since boot (there is
 *                                             no RTC; run_sound/rand only want it to
 *                                             move, and os.date should not read 1970)
 *   abort/__assert_func                       stop; the watchdog reboots
 *   _*_r                                      strtod walks newlib's mprec, which
 *                                             allocates through these reentrant hooks
 *
 * femto8_version is the string p8_emu.c prints in help(); nothing else defines it
 * for the device (the CMake build passes it in for the desktop). */
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/types.h>
#include <sys/reent.h>
#include <sys/stat.h>

#include <dirent.h>

#include "task.h"        /* xTaskGetTickCount: the seam's ms tick */

const char *femto8_version = "femto8 (picovave)";

/* ---------------------------------------------------------------- reentrant alloc --- */
/* Only reachable through strtod's mprec (Lua's tonumber). Same one heap as malloc. */

void *_malloc_r(struct _reent *r, size_t n) { (void)r; return malloc(n); }
void *_calloc_r(struct _reent *r, size_t a, size_t b) { (void)r; return calloc(a, b); }
void *_realloc_r(struct _reent *r, void *p, size_t n) { (void)r; return realloc(p, n); }
void _free_r(struct _reent *r, void *p) { (void)r; free(p); }

/* ---------------------------------------------------------------- traps --- */

void abort(void)
{
    for (;;)
        ;                                  /* the watchdog (fm1_cstart armed it) reboots */
}

void __assert_func(const char *file, int line, const char *fn, const char *expr)
{
    (void)file; (void)line; (void)fn; (void)expr;
    abort();
}

/* ---------------------------------------------------------------- time --- */

/* No RTC. A monotonic second counter keeps os.time()/os.date() moving and makes
 * rand()'s seed differ run to run; it is NOT wall-clock time. */
time_t time(time_t *t)
{
    time_t now = (time_t)(xTaskGetTickCount() / 1000u);
    if (t)
        *t = now;
    return now;
}

struct tm *localtime(const time_t *t)
{
    static struct tm tm;
    long long secs = t ? (long long)*t : 0;
    long long days = secs / 86400;
    long long rem = secs % 86400;
    long long z, era;
    unsigned doe, yoe, doy, mp, d, m;
    long long y;

    if (rem < 0) { rem += 86400; days--; }

    /* civil_from_days (Hinnant), so os.date gives a real calendar date */
    z = days + 719468;
    era = (z >= 0 ? z : z - 146096) / 146097;
    doe = (unsigned)(z - era * 146097);
    yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    y = (long long)yoe + era * 400;
    doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    mp = (5 * doy + 2) / 153;
    d = doy - (153 * mp + 2) / 5 + 1;
    m = mp + (mp < 10 ? 3 : (unsigned)-9);
    y += (m <= 2);

    tm.tm_year = (int)(y - 1900);
    tm.tm_mon = (int)m - 1;
    tm.tm_mday = (int)d;
    tm.tm_hour = (int)(rem / 3600);
    tm.tm_min = (int)((rem % 3600) / 60);
    tm.tm_sec = (int)(rem % 60);
    tm.tm_wday = (int)(((days + 4) % 7 + 7) % 7);   /* 1970-01-01 was a Thursday */
    tm.tm_yday = 0;
    tm.tm_isdst = 0;
    return &tm;
}

/* ---------------------------------------------------------------- "nothing here" --- */

char *getenv(const char *name) { (void)name; return NULL; }
int access(const char *path, int mode) { (void)path; (void)mode; return -1; }
int mkdir(const char *path, mode_t mode) { (void)path; (void)mode; return -1; }
int stat(const char *path, struct stat *buf)
{
    (void)path;
    if (buf)
        memset(buf, 0, sizeof *buf);
    return -1;
}

char *strerror(int errnum)
{
    (void)errnum;
    return (char *)"error";
}

/* ---------------------------------------------------------------- directory --- */
/* femto8's cart browser and Lua's ls() walk a directory. There is none. */

struct __pv_dir { int unused; };

DIR *opendir(const char *name) { (void)name; return NULL; }
struct dirent *readdir(DIR *dirp) { (void)dirp; return NULL; }
int closedir(DIR *dirp) { (void)dirp; return -1; }
