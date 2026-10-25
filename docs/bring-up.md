# kebab bring-up (measured on hardware, 2026-10-24)

Unit: OnePlus 8T `KB2000` (kebab, SM8250), 12+256, ColorOS 14, unlocked
(`verifiedbootstate=orange`), TWRP/Magisk available for diagnostics.

Result at the end of the session: **Mu-Silicium boots**, the UniXNU menu is
displayed, the keys work, and the firmware log is visible on the panel. UFS
block storage is still missing.

## Debug channel without UART

The panel is enough:

1. Build the firmware as **DEBUG** (`--target DEBUG`). `SiliciumPkg` then links
   `FrameBufferSerialPortLib`, so every `DEBUG()`/`Print()` call is drawn into
   the splash framebuffer that ABL leaves behind.
2. Flash, boot, photograph or record the screen. Each line is of the form
   `Loading driver at 0x... EntryPoint=0x... <name>.efi`, and a failing driver
   prints its `ASSERT` together with the source file and line.
3. To stop the picture from scrolling away, either paint a colour marker at a
   chosen place (a `for` loop over `0x9C000000` is enough) or spin forever after
   printing.

A UART console also exists (`0xA90000`, GPIO34/35, 115200 8N1) and the 2023
`UARTSerialPortLib` can be ported back into the current tree, but the hardware
adapter was not available; everything below was found through the framebuffer.

## The three fixes that made it boot

### 1. Use the phone's own DTB in the boot image

Symptom: black screen, back to fastboot after ~8 s, no firmware output at all.

Evidence from the ABL log (`abl_log` partition, readable with a root shell):

```
Load Image boot_a total time: 101 ms
avb_slot_verify.c:245: ERROR: boot_a: Hash of data does not match digest in descriptor.
ERROR: Could not get splash memory region node
```

`Mu-Silicium`'s `Resources/DTBs/kebab.dtb` lacks the reserved splash region, so
ABL does not pass the framebuffer on. The live device tree
(`cont_splash_region@9c000000`, alias `cont_splash_memory`) has it. Passing the
live DTB as `--dtb` changes the behaviour immediately: the phone stops falling
back to fastboot.

### 2. Disable the APSS watchdog at SEC entry

ABL arms the APSS watchdog; any later fault resets the phone, which wipes the
framebuffer before it can be read. Writing zero to `APSS_HM + 0x410000 + 0x8`
(`0x17C10008`) at the start of `SecEntry` keeps the last frame on screen.

### 3. UFS driver: assert, then a silent failure

With the current vendor drop the driver prints:

```
Loading driver at 0x... UFSDxe.efi
ufs_error_log: 0x0
ASSERT_EFI_ERROR (Status = Device Error)
ASSERT UFS.c *1004: ...
```

Two things were tried:

* `ConfigurationMapLib.c`: `EnableUfsIOC` changed from `0` to `1` (the value
  `aliothPkg` uses). The assert disappears — the driver now fails quietly.
* The 2023 drop ships `UFSDxe.patched.efi` (8 bytes differ) and its `UFSDxe.inf`
  points at it. With the patched binary the assert is also gone.

Neither produces a block device. `FsProbe` (a small DXE driver that reports
`LocateHandleBuffer` results as coloured bands on the panel) says:

```
block devices: 0
file systems:  0
```

so UniXNU never sees a volume and falls back to its built-in
`Config.c` menu (`Return to firmware`).

## What the firmware does do

* SEC, PEI and DXE run; the graphics console, the key driver and BDS work.
* The UniXNU menu is drawn and accepts input.
* `fastboot oem edl`, `oem reboot-edl`, `oem enter-edl` and `reboot edl` are all
  rejected by ABL. `adb reboot edl` works, but only while Android is running, so
  EDL cannot be reached after a firmware crash and RAM contents cannot be read
  back. The partitions below were dumped with a root shell instead.

## Useful partitions

| Partition | Use |
| --- | --- |
| `abl_log` (16 MiB) | ABL's own boot log; holds the last few boots |
| `misc` (1 MiB) | bootloader control block (slot, boot reason) |
| `kernel_log` (32 MiB) | last kernel log buffer area |
| `vm-system_a/b` (128 MiB) | unused without a VM; used here for payload experiments |
| `oem_cust1_a/b` (1 GiB) | spare OPPO partitions |

## Next steps

1. Make `UFSDxe` produce a block device: compare the UFS PHY/clock setup with a
   tree where kebab works, or check whether the driver needs a different
   `ConfigurationMapLib` entry (`UfsSmmuConfigForOtherBootDev`,
   `SecPagePoolCount`).
2. Once a volume exists, put the payload FAT image on it: UniXNU scans all
   `SimpleFileSystem` handles for `\EFI\UniXNU\unixnu.toml`, so any partition
   with that file works.
3. Then the `xnu` entry in `package/unixnu.toml` can load `Payloads/kernel.macho`
   with its `kernel.macho.adt` sidecar.

## 2026-10-25: payload volume found, kernel hand-off reached

### What works

- Mu-Silicium UEFI boots on the device from `boot_a` with the phone's own live
  device tree. The Mu-shipped `kebab.dtb` has no `cont_splash_region@9c000000`
  node, so ABL refuses to hand the framebuffer over and the screen stays black.
- The UniXNU menu appears and lists the entry generated from `board.toml`
  instead of the built-in fallback menu, which means the payload volume was
  found, partitioned and mounted.
- DarwinBoot exits boot services and prints the hand-off log with the kernel
  virtual base, physical base, boot args, video geometry and CPU state.

### Payload delivery

The payload is a raw FDF region at `0x00300000` (FD base `0x9FC00000`), published
by `drivers/KebabRamDisk` as a `EFI_BLOCK_IO_PROTOCOL` device with a vendor
device path so `PartitionDxe` binds and `FatPkg` mounts it. Two alternatives fail
on this board:

- payload as an FFS file: the FFS size field is 24 bits, a 64 MiB payload cannot
  be expressed;
- `RamDiskDxe` + `EFI_RAM_DISK_PROTOCOL`: `Register` returns
  `EFI_INVALID_PARAMETER` because it requires page-allocated memory.

### Framebuffer console

`FrameBufferSerialPortLib` handled `\n` and `\r` but ignored `\f`. DarwinBoot
starts its hand-off log with a form feed, so the log continued from wherever the
firmware log stopped and the left half of every line was cut off. Handling `\f`
as "clear the screen and home the cursor" makes the hand-off log readable from
the first line, which is how the kernel base and video geometry above were read.

The stride must stay `(FB_WIDTH - FONT_WIDTH * FontScale) * FB_BPP`: that is the
advance from one glyph row to the next same column, not the scanline length.
Using `FB_WIDTH * FB_BPP` there shears every glyph.

### Kernel load address

DarwinBoot used to take the first conventional-memory run that fits, which is
`0x7D000` on this board. The kernel then faulted during `_arm_init` before
`_machine_startup` (the stage bars never appeared and no reset probe fired).
The window is now taken at `0x40000000` and above; the kernel lands at
`0xC007C000`.

### Where it stops now

`DARWINBOOT: hand-off to the kernel` is printed, `entry words 14000BF5` confirms
the patched entry instruction, and then the screen stays black. The green bar
that `DarwinEnter` paints after `ExitBootServices` has never been observed,
which suggests the hand-off itself may not complete rather than the kernel
faulting later. `ExitBootServices` is retried 16 times with each result logged.

### Device repository state

`dram_size` and the `/memory` node in `dts/kebab.dts` are both restricted to the
first contiguous DRAM chunk (`0x80000000`, `0x39900000` bytes). The real map is
three discontiguous ranges and the ADT generator only accepts one.
