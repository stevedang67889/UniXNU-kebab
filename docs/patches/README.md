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