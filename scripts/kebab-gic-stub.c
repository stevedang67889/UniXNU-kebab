/*
 * Kebab (OnePlus 8T / SM8250) GICv3 early bring-up.
 *
 * The board header does not define HAS_GIC_V3, so pe_fiq.c compiles its GICv3
 * setup out and pe_init_fiq() is an empty function. Nothing programs the
 * interrupt controller until the ArmCpu driver matches -- and that matching is
 * what stalls in zalloc because no interrupt controller exists yet.
 *
 * This stub is injected by scripts/patch-kebab-gic.py at the tail of
 * pe_arm_init_interrupts(), before IOKit starts, and gives the kernel a GIC:
 * map the distributor and redistributors, wake this CPU's redistributor, route
 * the virtual-timer PPI to Group 1 non-secure, enable the GICv3
 * system-register CPU interface and install kebab_irq as the IRQ handler.
 * sleh_irq() already dispatches to cdp->interrupt_handler, so no other kernel
 * change is needed.
 *
 * Group 1 non-secure, not Group 0: on this device TrustZone owns Group 0, and
 * a Group 0 interrupt would arrive as FIQ instead of reaching sleh_irq().
 *
 * The code runs from the kernel's read-only text: it has no writable state,
 * no external relocations (kernel symbol addresses arrive as -D defines) and
 * only uses caller-saved registers plus its own stack frame.
 */
typedef unsigned long long u64;
typedef unsigned int u32;
typedef unsigned char u8;

#ifndef GICD_BASE
#error "GICD_BASE must be supplied by the patcher"
#endif
#ifndef GICR_BASE
#error "GICR_BASE must be supplied by the patcher"
#endif
#ifndef GICR_STRIDE
#error "GICR_STRIDE must be supplied by the patcher"
#endif
#ifndef GICR_FRAMES
#error "GICR_FRAMES must be supplied by the patcher"
#endif
#ifndef VIRTUAL_TIMER_INTID
#error "VIRTUAL_TIMER_INTID must be supplied by the patcher"
#endif
#ifndef ML_IO_MAP
#error "ML_IO_MAP must be supplied by the patcher"
#endif
#ifndef ML_INSTALL_INTERRUPT_HANDLER
#error "ML_INSTALL_INTERRUPT_HANDLER must be supplied by the patcher"
#endif
#ifndef RTCLOCK_INTR
#error "RTCLOCK_INTR must be supplied by the patcher"
#endif
#ifndef PE_STATE
#error "PE_STATE must be supplied by the patcher"
#endif
#ifndef SCRATCH
#error "SCRATCH must be supplied by the patcher"
#endif

/* Written by the GIC stub, read by kebab_probe (scripts/patch-kebab-probe.py):
 * the padding at the end of the writable __DATA segment is not part of any
 * section and is never referenced by the kernel. */
#define SCRATCH_MAGIC 0x4b45424142494331ULL   /* "KEBABIC1" */

#define GICD_SIZE 0x10000ULL
#define GICR_SIZE 0x100000ULL

#define GICD_CTLR 0x0000u
#define GICD_TYPER 0x0004u
#define GICD_ICENABLER 0x0180u
#define GICD_ICPENDR 0x0280u
#define GICD_CTLR_ARE_NS (1u << 4)
#define GICD_CTLR_ENABLE_GRP1NS (1u << 1)

#define GICR_TYPER 0x0008u
#define GICR_WAKER 0x0014u
#define GICR_WAKER_PROCESSOR_SLEEP (1u << 1)
#define GICR_WAKER_CHILDREN_ASLEEP (1u << 2)
#define GICR_SGI_OFFSET 0x10000u
#define GICR_IGROUPR0 0x0080u
#define GICR_ISENABLER0 0x0100u
#define GICR_ICENABLER0 0x0180u
#define GICR_ICPENDR0 0x0280u
#define GICR_IPRIORITYR 0x0400u
#define GICR_ICFGR1 0x0c04u

/* Nothing else is enabled; the timer is the only interrupt that must run. */
#define TIMER_PPI_PRIORITY 0xa0u

/* PE_state_t: initialized (4 bytes), then PE_Video at offset 8.  PE_Video is
 * v_baseAddr, v_rowBytes, v_width, v_height, ... at 8-byte steps. */
#define PE_VIDEO_BASE 8u
#define PE_VIDEO_ROWBYTES 16u
#define PE_VIDEO_HEIGHT 32u

/* One sixteenth of the framebuffer, band 3 (bands 1 and 2 are the entry
 * probe's stage bars).  The band is repainted at every step: its final colour
 * is the last step the stub reached, and it survives a hang without needing
 * any RAM hand-over. */
#define PAINT_BAND 3u
#define PAINT_ENTRY 0xffffffffu      /* stub entered, framebuffer mapped (white) */
#define PAINT_MAPPED 0xffffff00u     /* GICD and GICR mapped (yellow) */
#define PAINT_SRE 0xff00ff00u        /* ICC_SRE_EL1 system registers (green) */
#define PAINT_REDIST 0xff0000ffu     /* redistributor found and awake (blue) */
#define PAINT_OK 0xffff00ffu         /* handler installed (magenta) */
#define PAINT_IRQ 0xff00ffffu        /* first virtual-timer IRQ ran (cyan) */
#define PAINT_IRQ_BAND 4u

typedef u64 (*ml_io_map_fn)(u64 phys, u64 size);
typedef void (*ml_install_interrupt_handler_fn)(void *nub, int source, void *target,
    void (*handler)(void *target, void *refCon, void *nub, int source), void *refCon);
typedef void (*rtclock_intr_fn)(u32 is_user_context);

static void kebab_irq(void *target, void *refCon, void *nub, int source);

static inline void barrier_dsb(void)
{
	__asm__ volatile ("dsb sy" ::: "memory");
}

static inline void barrier_isb(void)
{
	__asm__ volatile ("isb" ::: "memory");
}

static u32 mmio_read32(u64 address)
{
	return *(volatile u32 *)(u64)address;
}

static void mmio_write32(u64 address, u32 value)
{
	*(volatile u32 *)(u64)address = value;
}

static void mmio_write8(u64 address, u8 value)
{
	*(volatile u8 *)(u64)address = value;
}

/* Repaint one sixteenth of the screen.  The colour is the progress report. */
static void paint_band(u64 fb, u64 band_bytes, u32 band, u32 color)
{
	volatile u32 *p;
	u64 count, i;

	if (fb == 0 || band_bytes == 0) {
		return;
	}
	p = (volatile u32 *)(u64)(fb + band_bytes * band);
	count = band_bytes & ~7ULL;
	for (i = 0; i < count; i += 4) {
		*p++ = color;
	}
	barrier_dsb();
}

/* PE_state.video.v_baseAddr is the physical framebuffer address copied from
 * the boot args, so map it before drawing. */
static u64 map_framebuffer(u64 *band_bytes)
{
	u64 pe = PE_STATE;
	u64 pa = *(volatile u64 *)(u64)(pe + PE_VIDEO_BASE);
	u64 rowbytes = *(volatile u64 *)(u64)(pe + PE_VIDEO_ROWBYTES);
	u64 height = *(volatile u64 *)(u64)(pe + PE_VIDEO_HEIGHT);
	u64 size, fb;

	*band_bytes = 0;
	if (pa == 0 || rowbytes == 0 || height == 0) {
		return 0;
	}
	size = (rowbytes * height + 0xfff) & ~0xfffULL;
	fb = ((ml_io_map_fn)ML_IO_MAP)(pa, size);
	if (fb != 0) {
		*band_bytes = (rowbytes * height) >> 4;
	}
	return fb;
}

/* Redistributors are per CPU and selected by affinity: match GICR_TYPER's
 * Affinity_Value against MPIDR_EL1 and stop early at the Last frame. */
static u64 find_redistributor(u64 gicr, u64 stride, u32 frames)
{
	u64 mpidr, affinity;
	u32 i;

	__asm__ volatile ("mrs %0, mpidr_el1" : "=r" (mpidr));
	affinity = (mpidr & 0x00ffffffULL) | ((mpidr >> 8) & 0xff000000ULL);

	for (i = 0; i < frames; i++) {
		u64 frame = gicr + (u64)i * stride;
		u64 typer = *(volatile u64 *)(u64)(frame + GICR_TYPER);

		if ((typer >> 32) == affinity) {
			return frame;
		}
		if (typer & (1ULL << 4)) {      /* GICR_TYPER.Last */
			break;
		}
	}
	return 0;
}

/* Program this CPU's redistributor and the distributor for one PPI. */
static int configure_gic(u64 gicd, u64 gicr, u32 intid, u64 fb, u64 band)
{
	u64 rd, sgi;
	u32 bit, group, config, spins, lines, index;
	volatile u32 *waker;

	if (intid < 16 || intid > 31) {
		return 0;
	}

	/* EL2 must have handed the GICv3 system registers to EL1; without SRE
	 * every other ICC_* access would be undefined, so give up before
	 * touching the controller. */
	__builtin_arm_wsr64("ICC_SRE_EL1",
	    __builtin_arm_rsr64("ICC_SRE_EL1") | 0x7);     /* SRE | DFB | DIB */
	barrier_isb();
	if ((__builtin_arm_rsr64("ICC_SRE_EL1") & 0x1) == 0) {
		return 0;
	}
	paint_band(fb, band, PAINT_BAND, PAINT_SRE);

	rd = find_redistributor(gicr, GICR_STRIDE, GICR_FRAMES);
	if (rd == 0) {
		return 0;
	}
	sgi = rd + GICR_SGI_OFFSET;
	bit = 1u << (intid & 31);

	/* Wake the redistributor (it starts asleep after reset). */
	waker = (volatile u32 *)(u64)(rd + GICR_WAKER);
	*waker = *waker & ~GICR_WAKER_PROCESSOR_SLEEP;
	barrier_dsb();
	for (spins = 0; spins < 10000000u; spins++) {
		if ((*waker & GICR_WAKER_CHILDREN_ASLEEP) == 0) {
			break;
		}
	}
	if (*waker & GICR_WAKER_CHILDREN_ASLEEP) {
		return 0;
	}
	paint_band(fb, band, PAINT_BAND, PAINT_REDIST);

	/* Take the PPIs/SGIs over from firmware: drop stale pending state and
	 * enable only the virtual-timer PPI, so an unexpected level interrupt
	 * cannot hold the handler in a loop. */
	mmio_write32(sgi + GICR_ICENABLER0, 0xffffffffu);
	mmio_write32(sgi + GICR_ICPENDR0, 0xffffffffu);

	/* PPI 27: Group 1 non-secure (Group 0 belongs to the secure world). */
	group = mmio_read32(sgi + GICR_IGROUPR0);
	mmio_write32(sgi + GICR_IGROUPR0, group | bit);
	mmio_write8(sgi + GICR_IPRIORITYR + intid, TIMER_PPI_PRIORITY);
	/* PPIs are level-sensitive: clear both configuration bits (ICFGR1). */
	config = mmio_read32(sgi + GICR_ICFGR1);
	mmio_write32(sgi + GICR_ICFGR1, config & ~(3u << (((intid - 16) & 15) * 2)));
	mmio_write32(sgi + GICR_ISENABLER0, bit);

	/* Distributor: disable every SPI firmware may have left enabled (an
	 * unhandled level interrupt would otherwise keep re-entering kebab_irq),
	 * then turn on affinity routing and Group 1 NS. */
	lines = ((mmio_read32(gicd + GICD_TYPER) & 0x1fu) + 1u) * 32u;
	for (index = 32; index < lines; index += 32u) {
		mmio_write32(gicd + GICD_ICENABLER + (index >> 5) * 4u, 0xffffffffu);
		mmio_write32(gicd + GICD_ICPENDR + (index >> 5) * 4u, 0xffffffffu);
	}
	mmio_write32(gicd + GICD_CTLR, mmio_read32(gicd + GICD_CTLR) |
	    GICD_CTLR_ARE_NS | GICD_CTLR_ENABLE_GRP1NS);
	barrier_dsb();

	/* GICv3 system-register CPU interface (EL1). */
	__builtin_arm_wsr64("ICC_PMR_EL1", 0xff);          /* allow every priority */
	__builtin_arm_wsr64("ICC_BPR1_EL1", 0);            /* all 8 priority bits */
	__builtin_arm_wsr64("ICC_CTLR_EL1",
	    __builtin_arm_rsr64("ICC_CTLR_EL1") & ~(1ULL << 1)); /* EOImode = 0 */
	__builtin_arm_wsr64("ICC_IGRPEN1_EL1", 1);         /* enable Group 1 NS */
	barrier_isb();

	return 1;
}

/* sleh_irq() handler ABI: (target, refCon, nub, source). Interrupts are
 * masked here. Acknowledge every Group 1 interrupt, feed the virtual timer to
 * rtclock_intr(), and drop priority/deactivate through ICC_EOIR1_EL1. */
static void kebab_irq(void *target, void *refCon, void *nub, int source)
{
	u64 fb = (u64)refCon;
	u32 loops = 0;

	(void)target;
	(void)nub;
	(void)source;

	for (;;) {
		u64 iar = __builtin_arm_rsr64("ICC_IAR1_EL1");
		u32 intid = (u32)(iar & 0xffffffULL);

		if (intid >= 1020 || ++loops > 1024) {
			break;
		}
		if (intid == VIRTUAL_TIMER_INTID) {
			/* Heartbeat: repaint the last band when the phase changes, so a
			 * frozen screen means no timer interrupt is being delivered. */
			{
				volatile u32 *ticks = (volatile u32 *)(u64)(SCRATCH + 0x18);
				volatile u32 *phase_slot = (volatile u32 *)(u64)(SCRATCH + 0x1c);
				u32 n = *ticks + 1;
				*ticks = n;
				if (fb != 0) {
					u32 colors[2] = { 0xffff0000u, 0xff00ff00u };
					u64 pe = PE_STATE;
					u64 band = (*(volatile u64 *)(u64)(pe + PE_VIDEO_ROWBYTES) *
					    *(volatile u64 *)(u64)(pe + PE_VIDEO_HEIGHT)) >> 4;
					if (band != 0) {
						*phase_slot = n;
						paint_band(fb, band, 15, colors[n & 1]);
					}
				}
			}
			if (fb != 0) {
				u64 pe = PE_STATE;
				u64 band = (*(volatile u64 *)(u64)(pe + PE_VIDEO_ROWBYTES) *
				    *(volatile u64 *)(u64)(pe + PE_VIDEO_HEIGHT)) >> 4;
				if (band != 0 &&
				    *(volatile u32 *)(u64)(fb + band * PAINT_IRQ_BAND) != PAINT_IRQ) {
					paint_band(fb, band, PAINT_IRQ_BAND, PAINT_IRQ);
				}
			}
			((rtclock_intr_fn)RTCLOCK_INTR)(1);
		}
		__builtin_arm_wsr64("ICC_EOIR1_EL1", iar);
		barrier_isb();
	}
}

/*
 * Entry from pe_arm_init_interrupts(). The caller has already restored its
 * frame and x0 holds its return value, so hand that value back unchanged.
 */
__attribute__((section(".text.a_entry"), used))
u64 kebab_gic_stub(u64 ret)
{
	ml_io_map_fn io_map = (ml_io_map_fn)ML_IO_MAP;
	u64 band = 0, fb, gicd, gicr;

	fb = map_framebuffer(&band);
	paint_band(fb, band, PAINT_BAND, PAINT_ENTRY);
	if (fb != 0) {
		*(volatile u64 *)(u64)(SCRATCH + 0) = fb;
		*(volatile u64 *)(u64)(SCRATCH + 8) = band;
		*(volatile u64 *)(u64)(SCRATCH + 16) = SCRATCH_MAGIC;
		__asm__ volatile ("dsb sy" ::: "memory");
#ifdef FB_ONLY
		/* Source-GIC builds only need the framebuffer hand-over here. */
		return ret;
#endif
	}
	gicd = io_map(GICD_BASE, GICD_SIZE);
	gicr = io_map(GICR_BASE, GICR_SIZE);
	if (gicd == 0 || gicr == 0) {
		return ret;              /* band stays white: maps failed */
	}
	paint_band(fb, band, PAINT_BAND, PAINT_MAPPED);
	if (configure_gic(gicd, gicr, VIRTUAL_TIMER_INTID, fb, band)) {
		((ml_install_interrupt_handler_fn)ML_INSTALL_INTERRUPT_HANDLER)
		    (0, 0, 0, kebab_irq, (void *)fb);
		paint_band(fb, band, PAINT_BAND, PAINT_OK);
	}
	return ret;
}
