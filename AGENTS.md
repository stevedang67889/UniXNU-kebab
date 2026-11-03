# Device agent guide — OnePlus 8T (kebab)

This repository owns the board DTS, peripheral wiring, component selections,
firmware inputs, partition layout and packaging for the OnePlus 8T (KB2000,
SM8250). The authoritative state lives in [`porting.toml`](porting.toml); the
measurements behind it are in [`docs/bring-up.md`](docs/bring-up.md).

## Read first

| Task | Required reading |
| --- | --- |
| Every change | [Porting](https://unixnu.com/porting/), [device repository](https://unixnu.com/porting/device-repository), [record guide](https://unixnu.com/porting/records) |
| Hardware | [Device tree](https://unixnu.com/porting/device-tree), [matching](https://unixnu.com/drivers/device-tree-matching), `dts/kebab.dts`, `UniXNU-sm8250-common` |
| Firmware | [UEFI firmware](https://unixnu.com/porting/uefi-firmware), `docs/patches/mu-basecore-kebab.patch`, `docs/patches/mu-silicium-kebab.patch` |
| Kernel | [Source and patches](https://unixnu.com/xnu/source-and-patches), [boot arguments](https://unixnu.com/xnu/boot-arguments) |
| Images | [Prelink](https://unixnu.com/concepts/prelink), [payload](https://unixnu.com/concepts/payload) |
| Debugging | [Green bar](https://unixnu.com/guide/debugging#green-bar), [log capture](https://unixnu.com/porting/log-capture) |

## Build

Run from a UniXNU checkout with this repository, `UniXNU-sm8250-common` and a
Mu-Silicium tree as siblings:

```sh
./unixnu fetch
./unixnu defconfig kebab_defconfig -D ../UniXNU-kebab
./unixnu status --json
./unixnu build kernel                       # macOS runner, ~17 min
./unixnu build payload -D ../UniXNU-kebab --kernel <kernel.macho> \
    --uefi <Mu-Silicium> --unique-chip-id <hex>
./unixnu build firmware -D ../UniXNU-kebab --uefi <Mu-Silicium>
./unixnu bootimg -D ../UniXNU-kebab --uefi <Mu-Silicium> \
    --fd <SILICIUM_UEFI.fd> --dtb <live.dtb> --ramdisk none
fastboot flash boot_a <image>
```

The boot image **must** carry the phone's own live device tree. ABL refuses to
hand over the framebuffer without `cont_splash_region@9c000000`, which shows up
as a black panel.

## Current stage

Firmware, menu, payload volume and DarwinBoot work. The old-fw CI kernel builds
through a temporary mirror (`stevedang67889/UniXNU-ci`), because Actions on the
fork are disabled.

The active interrupt path is now GFander's source-level GICv3 design:
`HAS_GIC_V3` in the board header, Group-1 setup in `pe_fiq.c`, and the Group-1
dispatch in `sleh_irq`. The patch is
`patches/xnu/10063-kebab-gic-vtimer.patch`; it builds in CI.

The source-GIC hardware images (`srcgic5`, `srcgic6`) still show only colour
bars and no text, so the console/panic transport is unresolved.

The binary-GIC path reaches `bsd_init` after retting
`platform_stall_panic_or_spin`, `mac_policy_initmach` and `PE_lockdown_iokit`.
`ufsprobe2` was flashed and did **not** show the green band 15, so
`IOFindBSDRoot` is not reached. Retting `loopattach`/`inittodr` made the boot
worse (only bands 1-3) and was reverted.

The BSD `kebab_ufs` driver is linked, the ADT contains `/kebab-ufs`, and
`board.toml` carries `rd=ufsroot0 ufsroot=userdata rootrw=0`. UFS root is not
confirmed yet.

Two firmware facts are easy to lose and expensive to rediscover:

- `ExitBootServices` never returns unless its notifications are skipped; see
  `docs/patches/mu-basecore-kebab.patch`.
- The kernel must be loaded above `0x40000000` with a 3 GiB window, otherwise
  `arm_vm_init`'s early I/O page tables do not fit.

## Probes

`board.toml` currently has **no entry probe**; normal boots use the baseline
kernel. For a bisect, add `entry_probe_reset_delay`, `entry_probe_reset_after`
and `entry_probe_reset_in` and remove them afterwards:

```toml
entry_probe_reset_delay = 6
entry_probe_reset_after = "_machine_conf"
entry_probe_reset_in = "_machine_startup"
```

A reboot six seconds after the stage bars means the probe point was reached; a
still screen means the hang is at or before it. Probes can mislead: an
inline-assembly probe hardcoded to an identity map crashes once `arm_vm_init`
closes that mapping and looks exactly like a kernel hang.

## Record

Update the affected [`porting.toml`](porting.toml) area with every change, then:

```sh
python3 scripts/porting-record.py check --device ../UniXNU-kebab
```

Keep the README concise; the record carries the evidence.
