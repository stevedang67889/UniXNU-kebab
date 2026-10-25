# Local patches

These patches are what the bring-up currently runs with. They are recorded here
because the trees they belong to are submodules of upstream repositories, which
this device repository must not modify.

| Patch | Applies to | What it does |
| --- | --- | --- |
| `darwinboot-kebab.patch` | `drivers/DarwinBoot` (`UniXNU/DarwinBoot`) | Loads the kernel above `0x40000000` instead of the lowest fitting run (`0x7D000`), and logs each `ExitBootServices` attempt with its status, retrying 16 times |
| `unixnu-bootimg-dtb.patch` | `scripts/unixnu/bootimg.py` in `UniXNU/UniXNU` | Passes `--dtb` for header version 2 images; without it `mkbootimg` produces an image ABL rejects with `DTB image must not be empty` |

Apply with `git apply` from the root of the target repository.

The Mu-Silicium side needs changes that are not patches against a git tree here:
`kebab.dsc`/`kebab.fdf` (components and the payload FDF region),
`Resources/Configs/kebab.toml` (`size = 0x4600000`),
`Include/DXE.inc` (drop `UFSDxe`),
`Silicon/Qualcomm/QcomPkg/Library/PlatformPeiLib/PlatformPeiLib.inf`
(no `SerialPort.c`),
`ConfigurationMapLib.c` (`EnableUfsIOC = 1`),
`Sec/Sec.c` (disable the APSS watchdog at `0x17C10008`), and
`FrameBufferSerialPortLib.c` (handle `\f` as clear-and-home).
## `mu-silicium-kebab.patch`

Applies to Mu-Silicium at `42fe5637`. It carries the platform changes that are
not part of any UniXNU repository:

| File | Change |
| --- | --- |
| `BootShim/BootShim.S` | shim adjustments used by the kebab boot image |
| `kebabPkg/kebab.dsc` | adds `KebabRamDisk`, `DarwinBoot`, the framebuffer PCDs (`1080x2400x32`), the FDT/HOB libraries and the `UniXNUPkg` modules |
| `kebabPkg/kebab.fdf` | FV layout: 3 MiB firmware volume at `0`, raw payload region at `0x00300000` |
| `kebabPkg/Include/DXE.inc` | drops the vendor `UFSDxe`, which asserts on this board |
| `kebabPkg/Library/ConfigurationMapLib` | `EnableUfsIOC = 1`, which removes the UFS assertion but still yields no block device |
| `Resources/Configs/kebab.toml` | FD size `0x4600000` (3 MiB FV + 70 MiB payload region) |
| `SiliconPkg/Sec/Sec.c` | disables the APSS watchdog at `0x17C10008` so a fault leaves the panel readable |
| `SiliconPkg/Library/FrameBufferSerialPortLib` | treats `\f` as clear-and-home so the DarwinBoot log starts at the top |
| `SiliconPkg/SiliciumPkg.dsc.inc` | library class mappings the kebab platform needs |

`Platforms/OnePlus/kebabPkg/Driver/KebabRamDisk/` is untracked in Mu-Silicium and
lives in the [KebabRamDisk](https://github.com/stevedang67889/KebabRamDisk)
repository instead.
## `mu-basecore-kebab.patch`

Applies to the `Mu_Basecore` submodule of Mu-Silicium. The bring-up keeps the
GIC spurious-interrupt mask, the generic-timer lib adjustments, the USB bus
header change and the DxeCore notify guard that lets `ExitBootServices` finish.
The decisive hunk is in `MdeModulePkg/Core/Dxe/Event/Event.c`:

```c
#define SKIP_INDEX   0xFFFF
#define SKIP_ALL     1
...
if (!SKIP_ALL && (Index != SKIP_INDEX)) {
  CoreNotifyEvent (Event);
}
```

Set `SKIP_ALL` to 0 to notify normally and bisect by index with `SKIP_INDEX`.