/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave device stub: <stdio.h> for the pi32v2 target.
 *
 * The JieLi toolchain's newlib is a reduced build: it ships string/ctype/stdlib
 * and libm, but no stdio and no allocator at all. femto8 uses both (Lua prints
 * numbers, the parser reads %02x, the engine formats %s/%d). So the device build
 * puts THIS directory first on the include path and this header shadows newlib's.
 *
 * The device has no filesystem: the cart lives in the SLOOP flash store, not a
 * directory tree. So fopen() always fails, and the engine's file paths (the
 * .p8.png encoder, cstore, the cart browser) simply refuse - exactly what the
 * simulator's FILESYSTEM=0 build does. The streams exist for the code that
 * writes to them (Lua's print, the engine's diagnostics): on the device they are
 * discarded, so a cart's print() cannot stall the frame.
 *
 * Only what femto8 and Lua reference is declared here. */
#pragma once

#include <stdarg.h>
#include <stddef.h>

typedef struct pv_file FILE;

extern FILE *stdin;
extern FILE *stdout;
extern FILE *stderr;

#define EOF (-1)
#define BUFSIZ 512
#define SEEK_SET 0
#define SEEK_CUR 1
#define SEEK_END 2

FILE  *fopen(const char *path, const char *mode);
FILE  *freopen(const char *path, const char *mode, FILE *stream);
int    fclose(FILE *stream);
int    fflush(FILE *stream);
size_t fread(void *ptr, size_t size, size_t nmemb, FILE *stream);
size_t fwrite(const void *ptr, size_t size, size_t nmemb, FILE *stream);
int    fseek(FILE *stream, long offset, int whence);
long   ftell(FILE *stream);
void   rewind(FILE *stream);
char  *fgets(char *s, int size, FILE *stream);
int    fputs(const char *s, FILE *stream);
int    fputc(int c, FILE *stream);
int    getc(FILE *stream);
int    ungetc(int c, FILE *stream);
int    putc(int c, FILE *stream);
int    getchar(void);
int    putchar(int c);
int    feof(FILE *stream);
int    ferror(FILE *stream);
void   clearerr(FILE *stream);
int    remove(const char *path);
int    rename(const char *old_path, const char *new_path);

int    printf(const char *format, ...);
int    fprintf(FILE *stream, const char *format, ...);
int    vprintf(const char *format, va_list ap);
int    vfprintf(FILE *stream, const char *format, va_list ap);
int    sprintf(char *str, const char *format, ...);
int    snprintf(char *str, size_t size, const char *format, ...);
int    vsprintf(char *str, const char *format, va_list ap);
int    vsnprintf(char *str, size_t size, const char *format, va_list ap);
int    puts(const char *s);
int    sscanf(const char *str, const char *format, ...);
int    vsscanf(const char *str, const char *format, va_list ap);
