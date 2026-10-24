# UniXNU-kebab

UniXNU device repository for the **OnePlus 8T (kebab, Qualcomm SM8250)**.

Status: **bring-up, kernel first**. The UEFI side is not usable yet on this phone
(see "Blocker" below), so this repository currently targets the `kernel` step of
the macOS workflow: produce `out/kebab/kernel/kernel.macho` from the pinned donor
IPSW.

## Layout

| Path | Contents |
| --- | --- |
| `board.toml` | board description, donor IPSW, kernel archives, payload and boot image parameters |
| `configs/kebab_defconfig` | minimal kernel configuration (DT platform, no device drivers yet) |
| `scripts/` | archive builders and the kebab ADT generator |
| `package/unixnu.toml` | UniXNU boot menu configuration for the device kit |
| `unixnu.lock` | pinned source commits written by `unixnu fetch` |

## Local changes against the previous WIP state

* `package/` was pruned: the 92 sheng (SM8550) Adreno/Metal probe files were not
  applicable to this SoC and broke the firmware build. They are kept out of the
  tree; only `package/unixnu.toml` remains.
* `[board] kit` is `kebab` (was `sheng`).
* `[firmware] packages` includes `{tree}/package` so EDK II can resolve
  `UniXNUPkg`; `defines` supplies `ENABLE_SECUREBOOT=0` / `DEVICE_MODEL=0`.
* `package/unixnu.toml` no longer points at a non-existent
  `Payloads/ShengPreflight.efi`.

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

## Blocker: the UEFI firmware does not start on this phone

Measured on a KB2000 (12+256, ColorOS 14, unlocked) on 2026-10-24:

* `fastboot boot` of any unsigned image fails with
  `Failed to load/authenticate boot image: Load Error`; a signed LineageOS
  recovery boots, so flashing works but ABL authenticates RAM-boot images.
* Flashing `Mu-Silicium`'s own `Mu-kebab.img` (and our repacked variants:
  header v1/2048, v2/4096, gzip, raw, with/without AVB footer) to `boot_a`
  always ends back in fastboot after ~8 s, with a black screen.
* A 72-byte `wfi` stub flashed to `boot_a` keeps the phone off fastboot for
  ~6 s, so ABL *does* execute our images; the firmware itself then dies.
* A red-fill marker at the start of `SiliciumPkg/Sec/Sec.c` is never visible,
  and a spin at the same place still resets at ~8 s, so the SEC entry looks
  unreachable (or the display path is dead in a way that also kills the
  framebuffer debug output).

`Mu-Silicium` has no public release image for kebab and marks the device
`Inactive`, so this port appears to have never been validated on hardware.
A UART console (QUPv3 SE2 at `0x00988000`, `qcom,msi-geni-console`, 115200 8N1)
is the next debugging step; the firmware does not route `DEBUG()` there by
default.

## Device values

| Item | Value | Source |
| --- | --- | --- |
| SoC | SM8250 (SD865) | stock DTB |
| Debug UART | `0x00988000`, 115200 8N1 | stock DTB `/chosen/stdout-path` |
| Framebuffer / splash | `0x9C000000`, `0x2300000` | mainline kebab DTS |
| Panel | 1080 × 2400, stride 4320, a8r8g8b8 | mainline kebab DTS |
| Volume keys | PM8150 GPIO7 (down) / GPIO6 (up) | mainline kebab DTS |
| UEFI FD | base `0x9FC00000`, size `0x300000` | Mu-Silicium `kebab.toml` |
| `userdata` size | 233,492,557,824 bytes (57,005,019 × 4096) | `fastboot getvar partition-size:userdata` on the 12+256 unit |
| `userdata` first LBA | **TODO: needs GPT read (root or UEFI)** | — |

## License

BSD-2-Clause-Patent, matching UniXNU and Mu-Silicium.
