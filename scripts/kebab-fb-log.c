/* Kebab (OnePlus 8T / SM8250) framebuffer log.
 *
 * The kernel has no console: initialize_screen, vcattach and PE_create_console
 * are ret stubs, and printf goes to a UART this board does not have. The GIC
 * stub (scripts/kebab-gic-stub.c) already maps the panel and records the
 * mapping in the scratch block at the end of the writable segment; this stub
 * reuses that mapping and paints every printf message on the screen.
 *
 * Hooks (see scripts/patch-kebab-fb-log.py):
 *   _printf   variadic: anonymous arguments are on the stack; the naked entry
 *             hands the entry SP to the formatter
 *   _vprintf  x1 is the va_list, a pointer to the anonymous slots
 *
 * Compiled freestanding with no writable globals: the cursor lives in the same
 * scratch block, at SCRATCH + 0x20 (the GIC stub uses the first 0x18 bytes).
 */
typedef unsigned long u64;
typedef unsigned int u32;
typedef unsigned char u8;

#ifndef SCRATCH
#error "SCRATCH must be supplied by the patcher"
#endif
#ifndef PE_STATE
#error "PE_STATE must be supplied by the patcher"
#endif

#define GIC_MAGIC 0x4b45424142494331ULL   /* written by kebab_gic_stub */
#define LOG_STATE (SCRATCH + 0x20u)       /* x, y, initialised */

/* PE_state_t: initialized (4 bytes), then PE_Video at offset 8. */
#define PE_VIDEO_BASE 8u
#define PE_VIDEO_ROWBYTES 16u
#define PE_VIDEO_WIDTH 24u
#define PE_VIDEO_HEIGHT 32u

#define SCALE 2
#define CELL (6 * SCALE)
#define LINE (14 * SCALE)

extern const u64 gFont[];

struct ctx {
  u8 *base;
  u32 rb, w, h, x, y;
};

static u32 vread32(u64 address)
{
  return *(volatile u32 *)(u64)address;
}

static u64 vread64(u64 address)
{
  return *(volatile u64 *)(u64)address;
}

static void put(struct ctx *g, u8 c)
{
  u32 row, col, dy, dx;

  if (c == '\n') {
    g->x = 0;
    g->y += LINE;
    return;
  }
  if (c == '\t') {
    g->x += 4 * CELL;
    return;
  }
  if (c < 32 || c > 126) {
    return;
  }
  if (g->x + CELL > g->w) {
    g->x = 0;
    g->y += LINE;
  }
  if (g->y + LINE > g->h) {
    g->y = 0;
  }
  u64 glyph = gFont[c - 32];
  u32 top = (u32)(glyph >> 32), bottom = (u32)glyph;
  for (row = 0; row < 12; row++) {
    u32 data = (row < 6) ? (top >> (row * 5)) : (bottom >> ((row - 6) * 5));
    for (col = 0; col < 5; col++) {
      if ((data >> col) & 1) {
        for (dy = 0; dy < SCALE; dy++) {
          for (dx = 0; dx < SCALE; dx++) {
            *(volatile u32 *)(g->base + (u64)(g->y + row * SCALE + dy) * g->rb
                              + (u64)(g->x + col * SCALE + dx) * 4) = 0xFFFFFFFFu;
          }
        }
      }
    }
  }
  g->x += CELL;
}

static void number(struct ctx *g, u64 value, u32 base, u32 width, int zero)
{
  u8 digits[24];
  u32 n = 0, i;

  do {
    u32 d = (u32)(value % base);
    digits[n++] = (u8)(d < 10 ? '0' + d : 'a' + d - 10);
    value /= base;
  } while (value && n < 24);
  for (i = n; i < width; i++) {
    put(g, zero ? '0' : ' ');
  }
  while (n) {
    put(g, digits[--n]);
  }
}

/* Refuse unmapped or user pointers: a bad string must not fault the logger. */
static int readable(const char *s)
{
  u64 a = (u64)s, par;
  if (a < 0xffff000000000000UL) return 0;
  __asm__ volatile ("at s1e1r, %1; isb; mrs %0, par_el1" : "=r" (par) : "r" (a) : "memory");
  return !(par & 1) && ((par >> 60) != 0);
}

static u32 format(struct ctx *g, const char *fmt, const u64 *args)
{
  u32 i, used = 0;

  for (i = 0; i < 2000 && fmt[i]; i++) {
    u8 c = (u8)fmt[i];
    if (c != '%') {
      put(g, c);
      used++;
      continue;
    }
    u32 width = 0;
    int zero = 0, longs = 0;
    c = (u8)fmt[++i];
    if (c == '0') {
      zero = 1;
      c = (u8)fmt[++i];
    }
    while (c >= '0' && c <= '9') {
      width = width * 10 + (c - '0');
      c = (u8)fmt[++i];
    }
    u32 precision = 4000;
    if (c == '.') {
      precision = 0;
      c = (u8)fmt[++i];
      if (c == '*') {
        precision = (u32)args[used++];
        c = (u8)fmt[++i];
      } else {
        while (c >= '0' && c <= '9') {
          if (precision < 4000) precision = precision * 10 + (c - '0');
          c = (u8)fmt[++i];
        }
      }
      if (precision > 4000) precision = 4000;
    }
    if (width > 200) width = 200;
    while (c == 'l' || c == 'z' || c == 'h') {
      longs += (c != 'h');
      c = (u8)fmt[++i];
    }
    if (c == '%') {
      put(g, '%');
      used++;
      continue;
    }
    if (c == 0) {
      break;
    }
    u64 v = args[used++];
    if (c == 's') {
      const char *s = (const char *)v;
      u32 k;
      for (k = 0; k < precision; k++) {
        if (!readable(s + k)) {
          put(g, '<');
          number(g, (u64)(s + k), 16, 0, 0);
          put(g, '>');
          break;
        }
        u8 ch = (u8)s[k];
        if (!ch) break;
        put(g, ch);
      }
    } else if (c == 'x' || c == 'X') {
      number(g, longs ? v : (v & 0xFFFFFFFFu), 16, width, zero);
    } else if (c == 'u' || c == 'd') {
      number(g, longs ? v : (v & 0xFFFFFFFFu), 10, width, zero);
    } else if (c == 'p') {
      put(g, '0');
      put(g, 'x');
      number(g, v, 16, width, 0);
    }
  }
  return used;
}

/* x0 = fmt as for printf(); x1 is the anonymous-argument pointer. */
__attribute__((used)) int kebab_fb_log_main(const char *fmt, const u64 *args)
{
  volatile u64 *state = (volatile u64 *)(u64)LOG_STATE;
  struct ctx g;
  u64 fb, magic;
  u32 used;

  if (fmt == 0) {
    return 0;
  }
  magic = vread64(SCRATCH + 0x10u);
  if (magic != GIC_MAGIC) {
    return 0;
  }
  fb = vread64(SCRATCH);
  if (fb == 0) {
    return 0;
  }
  g.base = (u8 *)(u64)fb;
  g.rb = vread32(PE_STATE + PE_VIDEO_ROWBYTES);
  g.w = vread32(PE_STATE + PE_VIDEO_WIDTH);
  g.h = vread32(PE_STATE + PE_VIDEO_HEIGHT);
  if (g.rb == 0 || g.w == 0 || g.h == 0 || g.rb < g.w * 4) {
    return 0;
  }
  if (state[2] == 0) {
    u64 total = (u64)g.rb * g.h;
    u64 i;
    for (i = 0; i < total; i += 4) {
      *(volatile u32 *)(g.base + i) = 0;
    }
    state[0] = 0;
    state[1] = 0;
    state[2] = 1;
  }
  g.x = (u32)state[0];
  g.y = (u32)state[1];
  if (g.y + LINE > g.h) {
    g.y = 0;
  }
  used = format(&g, fmt, args);
  put(&g, '\n');
  state[0] = g.x;
  state[1] = g.y;
  __asm__ volatile ("dsb sy" ::: "memory");
  return (int)used;
}

/* Entry from _printf(): the anonymous arguments live on the stack. */
__attribute__((naked, section(".text.a_entry"))) long kebab_fb_log(const char *fmt)
{
  __asm__ volatile ("mov x1, sp\n\tb kebab_fb_log_main");
}

/* Entry from _vprintf(): x1 already points at the anonymous slots. */
__attribute__((naked, section(".text.a_entry"))) long kebab_fb_log_vprintf(const char *fmt)
{
  __asm__ volatile ("b kebab_fb_log_main");
}

#include "Font.h"
__attribute__((section(".text.z_font"))) const u64 gFont[] = GLYPH_FONT;
