# UniXNU-kebab

UniXNU device repository for the **OnePlus 8T (kebab, Qualcomm SM8250)**.

## Status (2026-10-28)

Tested on a KB2000 (12+256, ColorOS 14, unlocked).

| Stage | State |
| --- | --- |
| Mu-Silicium UEFI from `boot_a` | working, panel log readable, keys work |
| UniXNU menu from the payload volume | working (`KebabRamDisk` publishes the FDF region as BlockIo) |
| DarwinBoot: validate, load, exit boot services | working once the exit-boot-services notifications are skipped (see `docs/patches/mu-basecore-kebab.patch`) |
| XNU entry and MMU enable | working (entry bar, then the six-second reset probe fires) |
| `_machine_startup` → `_kernel_bootstrap` | working (a tail call, so nothing follows it) |
| `kernel_bootstrap` up to `_bsd_init` | working once `PE_create_console` is replaced by a `ret` (`scripts/patch-func-ret.py`) |
| Scheduler start, bootstrap thread, core services | working (the reset probe still fires after the first context switch) |
| `PE_init_iokit` → `_bsd_init` | **current blocker** — with `PE_init_iokit`, `initialize_screen`, `kperf_init_early`, `ml_wait_max_cpus` and `platform_stall_panic_or_spin` skipped, the kernel stops in this gap; `scripts/patch-xnu-log-probe.py` puts 32 checkpoints in one boot; the DRAM flag page does not survive a reset, so the capture is moving to the pram page (see `docs/log-capture.md`) |

Two things were needed to get the kernel this far: a 3 GiB DRAM window that
DarwinBoot loads the kernel into (`dram-base`, `dram-size` and
`darwinboot-memory-size` all `0xC0000000`), and the device tree nodes XNU looks
up by property (`arm-io/gic` with `interrupt-controller = "master"`, a timer
node, `virtual-timer-intid`, `/pram`). See `docs/bring-up.md` for the measured
chain and `docs/log-capture.md` for the 32-checkpoint flag capture.

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

* **UFS**: the vendor `UFSDxe` asserts with `Device Error` at `UFS.c:1004`.
  Setting `EnableUfsIOC = 1` (as `alioth` does) removes the assert, but no block
  device is produced yet, so UniXNU still falls back to its built-in menu.
  The diagnostic driver `FsProbe` (see `docs/bring-up.md`) reports
  *0 block devices, 0 file systems*.
* **Payload volume**: UniXNU scans every `SimpleFileSystem` handle for
  `\EFI\UniXNU\unixnu.toml`. Until UFS exposes a volume, the config embedded in
  the firmware (`Config.c` fallback) is what the menu shows.
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
