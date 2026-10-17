/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave device stub: the printf family, sscanf, and the stdio entry points.
 *
 * The JieLi toolchain's newlib has no stdio, so this is femto8's and Lua's only
 * formatter. It covers what they use: %d %i %u %o %x %X %c %s %p %n %%
 * (with -, +, space, #, 0, width, precision, * and the h/hh/l/ll/z lengths) and
 * the float forms %f %e %g (with E/G, and Lua's %.14g).
 *
 * The float digits come from pv_digits(): a double, scaled with log10/pow and
 * emitted one decimal at a time, rounded at the requested significant digit. That
 * is exact for the values a cart prints (integers, halves, the results of PICO-8
 * arithmetic) and correct to within a unit in the last printed digit elsewhere -
 * the toolchain ships no correctly-rounded dtoa (its _dtoa_r needs the allocator
 * the SDK would have provided), and the device cannot print a diagnostic to argue
 * with. A cart that compares tostring() output byte for byte should still agree
 * for every value it can express.
 *
 * There is no filesystem on the device: fopen() refuses and the streams are sinks,
 * as the simulator's -sFILESYSTEM=0 build does. */
#include <math.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "stdio.h"

struct pv_file {
    int sink;
};

static struct pv_file pv_stdin_s  = { 1 };
static struct pv_file pv_stdout_s = { 1 };
static struct pv_file pv_stderr_s = { 1 };
FILE *stdin  = &pv_stdin_s;
FILE *stdout = &pv_stdout_s;
FILE *stderr = &pv_stderr_s;

/* ---- the sink ------------------------------------------------------------ */
typedef struct {
    char  *buf;
    size_t cap;
    size_t n;
} pv_out;

static void pv_emit(pv_out *o, char c)
{
    if (o->buf && o->n < o->cap)
        o->buf[o->n] = c;
    o->n++;
}

static void pv_emits(pv_out *o, const char *s, size_t l)
{
    while (l--)
        pv_emit(o, *s++);
}

#define F_ALT   1
#define F_ZERO  2
#define F_LEFT  4
#define F_PLUS  8
#define F_SPACE 16

/* value = 0.d[0]d[1]... * 10^decpt ; at most `sig` digits, rounded. */
static int pv_digits(double av, char *d, int sig)
{
    int decpt, i, need;
    double s, p10;

    decpt = (int)floor(log10(av)) + 1;
    p10 = pow(10.0, (double)(decpt - 1));
    s = av / p10;
    if (!(s >= 1.0) && !(s < 10.0))               /* inf/nan: give up gracefully */
        s = 1.0;
    while (s >= 10.0) { s /= 10.0; decpt++; }
    while (s < 1.0)   { s *= 10.0; decpt--; }

    need = sig < 0 ? 1 : sig;
    for (i = 0; i < need; i++) {
        int c = (int)s;
        if (c < 0) c = 0;
        if (c > 9) c = 9;
        d[i] = (char)('0' + c);
        s = (s - (double)c) * 10.0;
    }
    if (s >= 5.0) {                               /* round at the last digit */
        i = need - 1;
        while (i >= 0) {
            if (d[i] < '9') { d[i]++; break; }
            d[i] = '0';
            i--;
        }
        if (i < 0) {                              /* 999 -> 1000 */
            for (i = need - 1; i > 0; i--)
                d[i] = d[i - 1];
            d[0] = '1';
            decpt++;
        }
    }
    return decpt;
}

static char pv_digit(const char *d, int sig, int i)
{
    return (i >= 0 && i < sig) ? d[i] : '0';
}

/* +width/-width applied around an already-built body, with the sign in front. */
static void pv_pad(pv_out *o, int flags, int width, int zero_len, const char *body, int blen)
{
    int pad = width > blen ? width - blen : 0;

    if (flags & F_LEFT) {
        pv_emits(o, body, (size_t)blen);
        while (pad-- > 0)
            pv_emit(o, ' ');
        return;
    }
    if ((flags & F_ZERO) && zero_len >= 0) {
        pv_emits(o, body, (size_t)zero_len);       /* the sign / 0x comes first */
        while (pad-- > 0)
            pv_emit(o, '0');
        pv_emits(o, body + zero_len, (size_t)(blen - zero_len));
        return;
    }
    while (pad-- > 0)
        pv_emit(o, ' ');
    pv_emits(o, body, (size_t)blen);
}

static void pv_int(pv_out *o, unsigned long long uv, int neg, int base, int upper,
                   int alt, int prec, int width, int flags)
{
    char body[160], rev[24];
    const char *digits = upper ? "0123456789ABCDEF" : "0123456789abcdef";
    int k = 0, n = 0, blen = 0, zero_len, pflags;

    if (!(uv == 0 && prec == 0))
        rev[n++] = '0';                            /* at least one digit unless %.0d of 0 */
    while (uv) {
        rev[n++] = digits[uv % (unsigned)base];
        uv /= (unsigned)base;
    }
    while (n < prec)
        rev[n++] = '0';

    if (neg)
        body[k++] = '-';
    else if (flags & F_PLUS)
        body[k++] = '+';
    else if (flags & F_SPACE)
        body[k++] = ' ';
    if (alt && base == 16) {
        body[k++] = '0';
        body[k++] = upper ? 'X' : 'x';
    } else if (alt && base == 8 && rev[n - 1] != '0') {
        rev[n++] = '0';
    }
    zero_len = k;
    while (n)
        body[k++] = rev[--n];
    blen = k;
    pflags = (prec >= 0) ? (flags & ~F_ZERO) : flags;   /* a precision turns off the 0 flag */
    pv_pad(o, pflags, width, zero_len, body, blen);
}

/* round(av * 10^prec) as decimal digits, no separators: the exact fixed-point path
 * for %f and the fixed style of %g. 1 on success, 0 when the value is too large to
 * hold in 64 bits (then the caller falls back). */
static int pv_fixed(double av, int prec, char *out, int *outlen, int *intdigits)
{
    double x = av * pow(10.0, (double)prec);
    unsigned long long xi;
    char rev[24];
    int n = 0, i;

    if (!(x < 1.8e19))
        return 0;
    xi = (unsigned long long)(x + 0.5);
    if (xi == 0) {
        out[0] = '0';
        for (i = 0; i < prec; i++)
            out[1 + i] = '0';
        *outlen = 1 + prec;
        *intdigits = 1;
        return 1;
    }
    do {
        rev[n++] = (char)('0' + (int)(xi % 10u));
        xi /= 10u;
    } while (xi);
    if (n <= prec) {
        int pad = prec - n;
        out[0] = '0';
        for (i = 0; i < pad; i++)
            out[1 + i] = '0';
        for (i = 0; i < n; i++)
            out[1 + pad + i] = rev[n - 1 - i];
        *outlen = 1 + prec;
        *intdigits = 1;
    } else {
        for (i = 0; i < n; i++)
            out[i] = rev[n - 1 - i];
        *outlen = n;
        *intdigits = n - prec;
    }
    return 1;
}

static void pv_float(pv_out *o, double v, char spec, int prec, int width, int flags)
{
    char body[512], d[24], fd[64], sign = 0;
    int k = 0, blen, i, sig, decpt;
    int upper = (spec >= 'A' && spec <= 'Z');
    double av;

    if (v != v || (v - v) != 0.0) {               /* nan or inf */
        const char *t = (v != v) ? "nan" : "inf";
        if (v < 0.0)
            sign = '-';
        else if (flags & F_PLUS)
            sign = '+';
        else if (flags & F_SPACE)
            sign = ' ';
        if (sign)
            body[k++] = sign;
        for (i = 0; i < 3; i++)
            body[k++] = upper ? (char)(t[i] - 32) : t[i];
        blen = k;
        pv_pad(o, flags & ~F_ZERO, width, sign ? 1 : 0, body, blen);
        return;
    }

    av = (v < 0.0) ? -v : v;
    if (v < 0.0)
        sign = '-';
    else if (flags & F_PLUS)
        sign = '+';
    else if (flags & F_SPACE)
        sign = ' ';
    if (sign)
        body[k++] = sign;

    if (spec == 'f' || spec == 'F') {
        int olen = 0, idig = 0;
        if (prec < 0)
            prec = 6;
        if (prec > 30)
            prec = 30;
        if (!pv_fixed(av, prec, fd, &olen, &idig)) {
            body[k++] = '0';
            olen = idig = 0;
        }
        for (i = 0; i < idig; i++)
            body[k++] = fd[i];
        if (prec > 0 || (flags & F_ALT)) {
            body[k++] = '.';
            for (i = idig; i < olen && i < idig + prec; i++)
                body[k++] = fd[i];
        }
    } else if (spec == 'e' || spec == 'E') {
        int x2;
        char eb[8];
        int en = 0;
        if (prec < 0)
            prec = 6;
        sig = prec + 1;
        if (sig > 17)
            sig = 17;
        decpt = pv_digits(av, d, sig);
        body[k++] = pv_digit(d, sig, 0);
        if (prec > 0 || (flags & F_ALT))
            body[k++] = '.';
        for (i = 0; i < prec; i++)
            body[k++] = pv_digit(d, sig, i + 1);
        body[k++] = upper ? 'E' : 'e';
        x2 = decpt - 1;
        if (x2 < 0) { body[k++] = '-'; x2 = -x2; } else body[k++] = '+';
        do { eb[en++] = (char)('0' + x2 % 10); x2 /= 10; } while (x2);
        while (en < 2) eb[en++] = '0';
        while (en) body[k++] = eb[--en];
    } else {                                      /* g / G */
        int P = (prec < 0) ? 6 : prec;
        if (P == 0)
            P = 1;
        if (P > 17)
            P = 17;
        decpt = pv_digits(av, d, P);
        if (decpt - 1 < -4 || decpt - 1 >= P) {   /* style e: P-1 fraction digits */
            int eprec = P - 1, x2;
            char eb[8];
            int en = 0;
            body[k++] = pv_digit(d, P, 0);
            if (eprec > 0 || (flags & F_ALT))
                body[k++] = '.';
            for (i = 0; i < eprec; i++)
                body[k++] = pv_digit(d, P, i + 1);
            body[k++] = upper ? 'E' : 'e';
            x2 = decpt - 1;
            if (x2 < 0) { body[k++] = '-'; x2 = -x2; } else body[k++] = '+';
            do { eb[en++] = (char)('0' + x2 % 10); x2 /= 10; } while (x2);
            while (en < 2) eb[en++] = '0';
            while (en) body[k++] = eb[--en];
        } else {                                  /* style f: P-decpt fraction digits */
            int gprec = P - decpt;
            int olen = 0, idig = 0;
            if (gprec < 0)
                gprec = 0;
            if (!pv_fixed(av, gprec, fd, &olen, &idig)) {
                body[k++] = '0';
                olen = idig = 0;
            }
            for (i = 0; i < idig; i++)
                body[k++] = fd[i];
            if (gprec > 0 || (flags & F_ALT)) {
                body[k++] = '.';
                for (i = idig; i < olen && i < idig + gprec; i++)
                    body[k++] = fd[i];
            }
        }
        if ((flags & F_ALT) == 0) {               /* %g drops trailing zeros */
            int dot = -1, last;
            for (i = 0; i < k; i++)
                if (body[i] == '.')
                    dot = i;
            if (dot >= 0) {
                last = k - 1;
                while (last > dot + 1 && body[last] == '0')
                    last--;
                if (last == dot)
                    last--;
                k = last + 1;
            }
        }
    }

    blen = k;
    pv_pad(o, flags, width, sign ? 1 : 0, body, blen);
}

static int pv_format(char *buf, size_t cap, const char *fmt, va_list ap)
{
    pv_out o;
    int count;

    o.buf = buf;
    o.cap = (cap && buf) ? cap - 1u : 0u;
    o.n = 0;

    while (*fmt) {
        int flags = 0, width = 0, prec = -1, len = 0;
        char spec;

        if (*fmt != '%') {
            pv_emit(&o, *fmt++);
            continue;
        }
        fmt++;
        if (*fmt == '%') {
            pv_emit(&o, '%');
            fmt++;
            continue;
        }
        for (;; fmt++) {
            if (*fmt == '-') flags |= F_LEFT;
            else if (*fmt == '0') flags |= F_ZERO;
            else if (*fmt == '+') flags |= F_PLUS;
            else if (*fmt == ' ') flags |= F_SPACE;
            else if (*fmt == '#') flags |= F_ALT;
            else if (*fmt == '\'') ;
            else break;
        }
        if (*fmt == '*') {
            width = va_arg(ap, int);
            if (width < 0) { flags |= F_LEFT; width = -width; }
            fmt++;
        } else {
            while (*fmt >= '0' && *fmt <= '9')
                width = width * 10 + (*fmt++ - '0');
        }
        if (*fmt == '.') {
            fmt++;
            if (*fmt == '*') {
                prec = va_arg(ap, int);
                fmt++;
            } else {
                prec = 0;
                while (*fmt >= '0' && *fmt <= '9')
                    prec = prec * 10 + (*fmt++ - '0');
            }
            if (prec < 0) prec = 0;
        }
        for (;; fmt++) {
            if (*fmt == 'l') len++;
            else if (*fmt == 'h') len--;
            else if (*fmt == 'z' || *fmt == 't' || *fmt == 'j') len += 2;
            else break;
        }
        spec = *fmt ? *fmt++ : 0;

        if (width > 120)
            width = 120;
        if (prec > 120)
            prec = 120;

        switch (spec) {
        case 'd': case 'i': {
            long long sv = (len >= 2) ? va_arg(ap, long long) : (long long)va_arg(ap, int);
            unsigned long long uv = (unsigned long long)sv;
            int neg = 0;
            if (sv < 0) { neg = 1; uv = 0u - uv; }
            pv_int(&o, uv, neg, 10, 0, 0, prec, width, flags);
            break;
        }
        case 'u': case 'o': case 'x': case 'X': {
            unsigned long long uv = (len >= 2) ? va_arg(ap, unsigned long long)
                                               : (unsigned long long)va_arg(ap, unsigned int);
            int base = spec == 'u' ? 10 : (spec == 'o' ? 8 : 16);
            pv_int(&o, uv, 0, base, spec == 'X', flags & F_ALT, prec, width, flags);
            break;
        }
        case 'p': {
            void *p = va_arg(ap, void *);
            pv_int(&o, (unsigned long long)(uintptr_t)p, 0, 16, 0, 1, -1, width, flags);
            break;
        }
        case 'c': {
            char c = (char)va_arg(ap, int);
            pv_pad(&o, flags, width, -1, &c, 1);
            break;
        }
        case 's': {
            const char *s = va_arg(ap, const char *);
            int l;
            if (!s) s = "(null)";
            l = (int)strlen(s);
            if (prec >= 0 && prec < l) l = prec;
            pv_pad(&o, flags, width, -1, s, l);
            break;
        }
        case 'n': {
            int *ip = va_arg(ap, int *);
            if (ip) *ip = (int)o.n;
            break;
        }
        case 'f': case 'F': case 'e': case 'E': case 'g': case 'G': {
            double dv = va_arg(ap, double);
            pv_float(&o, dv, spec, prec, width, flags);
            break;
        }
        case 0:
            break;
        default:
            pv_emit(&o, '%');
            pv_emit(&o, spec);
            break;
        }
    }

    count = (int)o.n;
    if (o.buf && cap)
        o.buf[o.n < cap - 1u ? o.n : cap - 1u] = 0;
    return count;
}

int vsnprintf(char *str, size_t size, const char *format, va_list ap)
{
    return pv_format(str, size, format, ap);
}

int snprintf(char *str, size_t size, const char *format, ...)
{
    va_list ap;
    int r;
    va_start(ap, format);
    r = pv_format(str, size, format, ap);
    va_end(ap);
    return r;
}

int vsprintf(char *str, const char *format, va_list ap)
{
    return pv_format(str, (size_t)-1, format, ap);
}

int sprintf(char *str, const char *format, ...)
{
    va_list ap;
    int r;
    va_start(ap, format);
    r = pv_format(str, (size_t)-1, format, ap);
    va_end(ap);
    return r;
}

int vfprintf(FILE *stream, const char *format, va_list ap)
{
    char scratch[384];
    (void)stream;
    return pv_format(scratch, sizeof scratch, format, ap);
}

int fprintf(FILE *stream, const char *format, ...)
{
    va_list ap;
    int r;
    va_start(ap, format);
    r = vfprintf(stream, format, ap);
    va_end(ap);
    return r;
}

int printf(const char *format, ...)
{
    char scratch[384];
    va_list ap;
    int r;
    va_start(ap, format);
    r = pv_format(scratch, sizeof scratch, format, ap);
    va_end(ap);
    return r;
}

int vprintf(const char *format, va_list ap)
{
    return vfprintf(stdout, format, ap);
}

int puts(const char *s)
{
    (void)s;
    return 0;
}

/* ---- scanf: only the conversions the engine uses (%2x, %d, %f, %s) --------- */
static int pv_scan(const char *s, const char *fmt, va_list ap)
{
    int assigned = 0;
    const char *p = s;

    while (*fmt) {
        if (*fmt == ' ' || *fmt == '\t' || *fmt == '\n') {
            while (*fmt == ' ' || *fmt == '\t' || *fmt == '\n') fmt++;
            while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;
            continue;
        }
        if (*fmt != '%') {
            if (*p != *fmt)
                return assigned;
            fmt++; p++;
            continue;
        }
        fmt++;
        if (*fmt == '%') {
            if (*p != '%') return assigned;
            fmt++; p++;
            continue;
        }
        {
            int width = 0, base = 10, is_signed = 1, is_float = 0, is_string = 0, is_char = 0;
            char spec;
            int neg = 0;
            long long iv = 0;
            int got = 0;

            while (*fmt >= '0' && *fmt <= '9')
                width = width * 10 + (*fmt++ - '0');
            while (*fmt == 'l' || *fmt == 'h' || *fmt == 'z' || *fmt == 'j')
                fmt++;
            spec = *fmt++;
            if (spec == 'd' || spec == 'i') { base = 10; is_signed = 1; }
            else if (spec == 'u') { base = 10; is_signed = 0; }
            else if (spec == 'x' || spec == 'X') { base = 16; is_signed = 0; }
            else if (spec == 'o') { base = 8; is_signed = 0; }
            else if (spec == 'f' || spec == 'e' || spec == 'g') { is_float = 1; }
            else if (spec == 's') { is_string = 1; }
            else if (spec == 'c') { is_char = 1; }
            else return assigned;

            if (!is_string && !is_char)
                while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;

            if (is_string) {
                char *out = va_arg(ap, char *);
                int n = 0;
                while (*p && !(*p == ' ' || *p == '\t' || *p == '\n') && (width == 0 || n < width)) {
                    out[n++] = *p++;
                    got = 1;
                }
                out[n] = 0;
                if (got) assigned++;
                continue;
            }
            if (is_char) {
                char *out = va_arg(ap, char *);
                if (*p) { *out = *p++; assigned++; }
                continue;
            }
            if (is_float) {
                double dv = 0.0, scale = 0.1;
                int seen = 0;
                if (*p == '-') { neg = 1; p++; }
                while (*p >= '0' && *p <= '9') { dv = dv * 10.0 + (*p++ - '0'); seen = 1; }
                if (*p == '.') {
                    p++;
                    while (*p >= '0' && *p <= '9') { dv += (*p++ - '0') * scale; scale *= 0.1; seen = 1; }
                }
                if (seen) {
                    double *out = va_arg(ap, double *);
                    *out = neg ? -dv : dv;
                    assigned++;
                }
                continue;
            }
            /* integer */
            if (*p == '-') { neg = 1; p++; }
            else if (*p == '+') { p++; }
            if (base == 16 && p[0] == '0' && (p[1] == 'x' || p[1] == 'X'))
                p += 2;
            while (*p) {
                int dv;
                if (*p >= '0' && *p <= '9') dv = *p - '0';
                else if (*p >= 'a' && *p <= 'f') dv = *p - 'a' + 10;
                else if (*p >= 'A' && *p <= 'F') dv = *p - 'A' + 10;
                else break;
                if (dv >= base) break;
                iv = iv * base + dv;
                got = 1;
                p++;
                if (width && --width == 0) break;
            }
            if (got) {
                unsigned int *out = va_arg(ap, unsigned int *);
                *out = (unsigned int)(neg ? -iv : iv);
                assigned++;
            } else {
                return assigned;
            }
        }
    }
    return assigned;
}

int vsscanf(const char *str, const char *format, va_list ap)
{
    return pv_scan(str, format, ap);
}

int sscanf(const char *str, const char *format, ...)
{
    va_list ap;
    int r;
    va_start(ap, format);
    r = pv_scan(str, format, ap);
    va_end(ap);
    return r;
}

/* ---- the stdio entry points: no filesystem, so these refuse or discard ----- */
FILE *fopen(const char *path, const char *mode) { (void)path; (void)mode; return NULL; }
FILE *freopen(const char *path, const char *mode, FILE *stream) { (void)path; (void)mode; (void)stream; return NULL; }
int   fclose(FILE *stream) { (void)stream; return 0; }
int   fflush(FILE *stream) { (void)stream; return 0; }
size_t fread(void *ptr, size_t size, size_t nmemb, FILE *stream) { (void)ptr; (void)size; (void)nmemb; (void)stream; return 0; }
size_t fwrite(const void *ptr, size_t size, size_t nmemb, FILE *stream) { (void)ptr; (void)stream; return size ? nmemb : 0; }
int   fseek(FILE *stream, long offset, int whence) { (void)stream; (void)offset; (void)whence; return -1; }
long  ftell(FILE *stream) { (void)stream; return -1; }
void  rewind(FILE *stream) { (void)stream; }
char *fgets(char *s, int size, FILE *stream) { (void)s; (void)size; (void)stream; return NULL; }
int   fputs(const char *s, FILE *stream) { (void)stream; return s ? (int)strlen(s) : 0; }
int   fputc(int c, FILE *stream) { (void)stream; return c; }
int   getc(FILE *stream) { (void)stream; return EOF; }
int   ungetc(int c, FILE *stream) { (void)stream; return c; }
int   putc(int c, FILE *stream) { (void)stream; return c; }
int   getchar(void) { return EOF; }
int   putchar(int c) { return c; }
int   feof(FILE *stream) { (void)stream; return 1; }
int   ferror(FILE *stream) { (void)stream; return 0; }
void  clearerr(FILE *stream) { (void)stream; }
int   remove(const char *path) { (void)path; return -1; }
int   rename(const char *old_path, const char *new_path) { (void)old_path; (void)new_path; return -1; }
