# UniXNU-kebab

UniXNU device repository for the **OnePlus 8T (kebab, Qualcomm SM8250)**.

## Status (2026-11-01)

Tested on a KB2000 (12+256, ColorOS 14, unlocked).

| Stage | State |
| --- | --- |
| Mu-Silicium UEFI, menu, payload volume, DarwinBoot | working |
| old-fw CI kernel build (`407db1a` + build fixes) | working; the uploaded artifact is `kernel-kebab-1` |
| XNU entry, `kernel_bootstrap`, GIC stub | working; the GIC handler is installed and the first timer IRQ fires |
| periodic timer IRQ | **not working**; the heartbeat band never advances, so an early allocator wait can still stop the boot |
| `bsd_init` / `IOFindBSDRoot` | **not reached** on the tested images; the last kernel line is `Unable to find vm tag ?+z for btlog` |
| BSD `kebab_ufs` driver | linked (`kebab_ufs_rootdev`); it will publish `ufsroot0` once root discovery runs |
| UFS root (`rd=ufsroot0`) | blocked by the early kernel stop above, not by the UFS driver source |

The old 2026-10-28 table described the `PE_init_iokit` bisection; that history is
in `docs/bring-up.md`. The measured 2026-11-01 stop is earlier: the kernel
prints the btlog tag warning and never reaches `bsd_init`.

The GIC stub reports handler-installed (magenta) and first-IRQ (cyan) bands, but
no repeated timer tick. Re-arming `CNTV_TVAL_EL0`/`CNTV_CTL_EL0` after
`rtclock_intr`, and even skipping `rtclock_intr`, both leave the screen static.
The old `kernel-novc3` with the same GIC/FB/bring-up patch stops at the same
point, so the next work is the timer/PPI-27 path, not the UFS driver.

## Layout

| Path | Contents |
| --- | --- |
| `board.toml` | board description, donor IPSW, kernel archives, payload and boot image parameters |
| `configs/kebab_defconfig` | minimal kernel configuration (DT platform, no device drivers yet) |
| `scripts/` | archive builders and the kebab ADT generator |
| `package/unixnu.toml` | UniXNU boot menu configuration for the device kit |
| `docs/bring-up.md` | what was measured on hardware and what it took to boot |
| `drivers/KebabRamDisk` | submodule: UEFI driver that exposes the firmware-embedded payload as a RAM disk |
| `unixnu.lock` | pinned source commits written by `unixnu fetch` |

## Build (macOS or the GitHub macOS runner)

```sh
./unixnu defconfig kebab_defconfig -D ../UniXNU-kebab
./unixnu fetch
./unixnu ipsw fetch
./unixnu build kernel
```

Output: `out/kebab/kernel/kernel.macho`.

Without a Mac, fork `UniXNU/UniXNU`, enable Actions and run the **macOS build**
workflow with:

| Input | Value |
| --- | --- |
| `device_repository` | `<your-user>/UniXNU-kebab` |
| `defconfig` | `kebab_defconfig` |
| `steps` | `kernel` |
| `xcode` | default |

## Boot image recipe (what actually boots)

The firmware volume is built from Mu-Silicium and packed into an Android boot
image. Two details decide whether the phone shows anything:

1. **The DTB must be the phone's own live DTB.** `Mu-Silicium`'s
   `Resources/DTBs/kebab.dtb` has no `cont_splash_region@9c000000`, so ABL logs
   `ERROR: Could not get splash memory region node` and never hands the
   framebuffer to the firmware: the screen stays on the stock boot logo. Dump
   the live tree (`adb pull` of the running device tree) and pass that file as
   `--dtb`.
2. **Header v2 / 4096-byte pages**, kernel = BootShim + `SILICIUM_UEFI.fd`,
   `--kernel_offset 0x8000 --tags_offset 0x100 --dtb_offset 0x1f00000`.

`fastboot boot` is rejected by ABL (`Failed to load/authenticate boot image`);
flash to `boot_a` instead and reboot.

## Device values

| Item | Value | Source |
| --- | --- | --- |
| SoC | SM8250 (SD865) | live DTB |
| Debug UART | **`0xA90000`** (QUPv3 SE12, GPIO34/35, 115200 8N1) | live DTB `earlycon=msm_geni_serial,0xa90000` |
| Second console node | `0x00988000` is `status = "disabled"` | live DTB |
| Framebuffer / splash | `0x9C000000`, `0x2300000`, 1080 × 2400 | live DTB `cont_splash_region@9c000000` |
| Panel | `oplus20828samsung_amb655x_1080_2400_cmd` (Samsung AMB655X, command mode) | live DTB |
| Touch | Synaptics S3908, i2c `a94000` addr `0x4B`, IRQ GPIO39, reset GPIO38 | live DTB |
| Volume keys | PM8150 GPIO7 (down) / GPIO6 (up) | mainline kebab DTS |
| UEFI FD | base `0x9FC00000`, size `0x300000` | Mu-Silicium `kebab.toml` |
| `userdata` size | 233,492,557,824 bytes (57,005,019 × 4096) | `fastboot getvar partition-size:userdata` on the 12+256 unit |
| `userdata` first LBA | 4,748,320 (GPT of the KB2000 9008 package) | `gpt_main0.bin` |

## Remaining work

* **Timer / GIC**: the GIC handler and the first PPI-27 IRQ work, but the
  virtual timer does not repeat. `CNTV_TVAL_EL0`/`CNTV_CTL_EL0` re-arms after
  `rtclock_intr`, and a run that skips `rtclock_intr`, both remain static. Fix
  the periodic tick (or route a different timer source) before chasing UFS.
* **UFS**: `bsd/dev/kebab_ufs.c` is linked and exposes the BSD `ufsroot0` hook;
  the ADT has the root `/kebab-ufs` node and the boot args are documented in
  `board.toml`. Root discovery is not reached yet, so the driver has no hardware
  result.
* **Display / touch drivers** for the kernel, and the `userdata` image flash.

## License

BSD-2-Clause-Patent, matching UniXNU and Mu-Silicium.
## Porting record

[`porting.toml`](porting.toml) is the authoritative record: thirteen areas with
the measured facts, completion level and evidence for each. Read it before
changing the port, and update the affected area with every change:

```sh
python3 scripts/porting-record.py check --device ../UniXNU-kebab
```

[`AGENTS.md`](AGENTS.md) carries the build commands and the two firmware facts
that are expensive to rediscover. [`docs/bring-up.md`](docs/bring-up.md) keeps
the chronological measurements.
Inputs that are not in any repository are named in
[`proprietary-files.txt`](proprietary-files.txt); [`local.toml.example`](local.toml.example)
shows the local paths a workstation needs.
