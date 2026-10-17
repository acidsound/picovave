/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave seam: the mutex femto8's audio queue uses (p8_audio.c). Both targets
 * call it from one thread / with interrupts off, so it is a no-op. */
#pragma once

typedef struct { volatile int locked; } pthread_mutex_t;

static inline int pthread_mutex_init(pthread_mutex_t *m, const void *attr)
{
    (void)attr;
    m->locked = 0;
    return 0;
}
static inline int pthread_mutex_lock(pthread_mutex_t *m) { (void)m; return 0; }
static inline int pthread_mutex_unlock(pthread_mutex_t *m) { (void)m; return 0; }
