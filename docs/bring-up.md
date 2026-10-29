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
## 2026-10-25 (evening): the kernel runs — ExitBootServices was the wall

The kernel had never executed. `DarwinEnter` never ran, so every kernel-side
probe (stage bars, panic screen, timed resets) was inert. The wall was
`ExitBootServices`.

### Finding

`CoreExitBootServices` notified 17 exit-boot-services callbacks and then never
returned. A frame buffer marker placed immediately after the notification and
before the memory-protection teardown never appeared, and a marker drawn after
`gBS->ExitBootServices` returned never appeared either, so the hang was inside
the notification phase.

Skipping the callbacks one index at a time did not help; skipping **all** of them
did. With `SKIP_ALL` in `CoreNotifySignalList` the sequence completes:

```
ExitBootServices ✅ → DarwinEnter (green band) ✅ → XNU entry (blue bar) ✅
→ MMU enabled ✅ (proved by the 6 s timed reset) → _machine_startup ✅
→ _machine_conf ⏳ hangs here
```

The boards that already boot (umi, lemonades, 13T) do not need this, so one of
the 17 callbacks is specific to this firmware build. XNU does not need them:
capsule locking, console teardown and the like are boot-service concerns.

### Bisect results (each with a build and a flash)

| Probe | Result |
| --- | --- |
| 17 EBS callbacks, unchanged | hang, no marker after the notifications |
| skip callback 17 of 17 | hang |
| `CapsuleRuntimeDxe` EBS handler returns immediately | hang |
| remove `RamDiskDxe` | hang |
| remove the whole USB stack (9 drivers) | hang |
| remove `SimpleTextInOutSerial` | hang |
| GIC spurious-interrupt mask patch | hang |
| **skip all 17 callbacks** | **passes** |

### Where it stops now

`_machine_conf` in `_machine_startup` hangs. That function builds the platform
expert, which walks the ADT. Our generated ADT has an **empty `arm-io` node** —
no uart, pmgr or interrupt-controller children — which is the next thing to fix.

### Firmware changes that made the kernel reachable

- `CoreNotifySignalList`: `SKIP_ALL` guard (see `docs/patches/mu-basecore-kebab.patch`)
- `DarwinEnter`: the green bar is cleaned out of the data cache before the branch
- DarwinBoot: the kernel window moves to DRAM (>= 0x40000000) and the request is
  3 GiB so `arm_vm_init`'s early I/O page tables fit
- ADT: `dram-base`, `dram-size` and `darwinboot-memory-size` all `0xC0000000`
## Kernel progress probes

`board.toml` can install a reset probe in the kernel instead of a screen bar:

```toml
entry_probe_reset_delay = 6
entry_probe_reset_after = "_machine_conf"    # symbol that must return
entry_probe_reset_in = "_machine_startup"    # function that calls it
```

The patched kernel waits six seconds after that call returns and resets with
PSCI SYSTEM_RESET. A reboot therefore means the probe point was reached; a still
screen means the hang is at or before it. Combined with the stage bars (blue at
entry, magenta before the MMU is on) this is the only signal available before
XNU has a console.

Measured chain with the current image:

| Probe | Result |
| --- | --- |
| stage 3 (just after the MMU enable) | reboot |
| after `_machine_startup` in `_arm_init` | still |
| after `_machine_conf` in `_machine_startup` | still |

So the kernel reaches `_machine_startup` and stops inside it, at or before
`_machine_conf` returns.
## The ADT only exports nodes marked `unixnu,adt-name`

`arm-io` stayed empty because the ADT generator exports **only** device tree nodes
carrying a `unixnu,adt-name` property; every other node is dropped. The working
umi port needs exactly one child there: the interrupt controller.

```dts
interrupt-controller@17a00000 {
    unixnu,adt-name = "intc";
    compatible = "arm,gic-v3";
    ...
};
```

The generated ADT then carries:

```
arm-io  ranges = base 0x17000000 size 0x1000000
  intc  compatible = "arm,gic-v3"
        reg = 0xa0000 + 0x10000 (distributor) and 0xa60000 + 0x100000 (redistributor)
        redistributor-stride = 0x20000
        #interrupt-cells = 3
```

Registers are emitted as offsets from the `arm-io` window, so `0xa0000` resolves
to the GIC distributor at `0x17a00000` and `0xa60000` to the redistributor at
`0x17a60000`. Add `unixnu,adt-name` to any further node the kernel has to see.
## XNU's device tree lookups

`arm-io/intc` alone was not enough. The ports that reach userspace name the
interrupt controller by property, not by node name:

```c
SecureDTFindEntry("interrupt-controller", "master")   /* finds the PIC */
pe_arm_map_interrupt_controller()                     /* also wants a timer node */
```

So the generated ADT now carries `arm-io/gic` with
`interrupt-controller = "master"`, `virtual-timer-intid = 27`, an
`arm-io/timer` node, and `/pram` plus `/chosen/embedded-panic-log-size` for the
panic log. Without them `ml_init_timebase` never runs and the kernel stops in
`_machine_conf` before it can print anything.

The builder is patched rather than the payload: `docs/patches/unixnu-adt-nodes.patch`
adds `_inject_xnu_nodes` to `scripts/unixnu/devicetree.py`, so every payload
build produces the complete tree.
## 2026-10-26: the kernel reaches the console initialisation

The probe chain now walks the kernel one call at a time. Each step below is a
device measurement: the patched kernel waits six seconds after the probe point
and resets, so a reboot means the point was reached and a still screen means the
hang is at or before it.

| Probe point (in `_arm_init` unless stated) | Result |
| --- | --- |
| after the MMU enable | reboot |
| after `arm_vm_init` | reboot |
| after `kernel_startup_initialize_upto` | reboot |
| inside the second `PE_init_platform` — after `pe_arm_init_interrupts` | still |
| inside `pe_arm_init_interrupts` — before `ml_io_map` | still |
| after `PE_create_console` | still |
| after `PE_create_console`, with the function itself patched to `ret` | **reboot** |

So the kernel gets through the early VM and the first platform initialisation and
stops in `PE_create_console` → `ml_io_map_wcomb`, which maps the framebuffer for
the text console. Replacing that function with a single `ret` (see
`scripts/patch-func-ret.py`) lets the kernel continue, so the hang is confined to
console creation and not to the boot path itself.

### Two pitfalls that cost several builds

- `_arm_init` calls `PE_init_platform` **twice**. A probe without
  `entry_probe_reset_occurrence` patches the first call only, so "the probe after
  `PE_init_platform` fired" and "the probe after the later call did not" are both
  true. Set the occurrence when bisecting a function with several call sites.
- The kernel clears the screen when it creates the console, so a black panel after
  the stage bars is expected from that point on and does not mean the kernel died.

### Device tree details XNU actually reads

The generated ADT now carries, beyond the tagged hardware:

| Property | Value | Why |
| --- | --- | --- |
| `arm-io/gic` `interrupt-controller` | `"master"` | `SecureDTFindEntry("interrupt-controller", "master")` |
| `arm-io/gic` `reg` | GIC distributor and redistributor, four 64-bit cells | mapped by `pe_arm_init_interrupts` |
| `arm-io/gic` `redistributor-stride` | one 32-bit cell (`0x20000`) | read as u32; an eight-byte property does not parse |
| `arm-io/gic` `virtual-timer-intid` | 27 | virtual timer interrupt |
| `arm-io/timer` | `device_type = timer`, `reg` inside the window | `pe_arm_map_interrupt_controller()` requires it |
| `/pram` and `/chosen/embedded-panic-log-size` | 1 MiB at `0x17F00000` | panic log |
| `cpus/cpu@N` `die-cluster-id` | 0 for the first four, 1 for the rest | topology |
| `/chosen` `dram-base`, `dram-size`, `darwinboot-memory-size` | equal values | kernel window |

The window is 4 GiB at `0x200000000` while the console hang is being cleared; the
earlier 3 GiB window at `0xC0000000` behaved the same, so the size is not the
cause.

### Next

1. Either restore a working console or keep the `ret` patch and continue the chain
   from `PE_init_printf`.
2. Add the kernel-side early console (the approach other Qualcomm ports use) so
   kernel output stops depending on the panel geometry.
## 2026-10-27: the kernel reaches the BSD layer

Continuing the same probe method, with one new tool: `scripts/patch-func-ret.py`
rewrites a single kernel function to `ret`, so a call that hangs can be skipped
without a console and without rebuilding the kernel.

### The chain, measured

| Point | Result |
| --- | --- |
| `PE_create_console` replaced by `ret` | kernel continues (reboot at the next probe) |
| `PE_init_printf`, `PE_init_cpu` | reboot |
| `_machine_conf` | reboot |
| `_vm_mem_bootstrap`, `_console_init`, `_sched_init` | reboot |
| `_kernel_thread_create` (first context switch follows) | reboot |
| `_device_service_create` | reboot |
| `_bootprofile_init`, second `_kernel_startup_initialize_upto` | reboot |
| any call inside `_bsd_init` | still |

So the kernel finishes the whole of `kernel_bootstrap` and enters `bsd_init`.

### Why the `_machine_startup` probe could never work

```
_machine_startup:
    bl  _machine_conf
    b   _kernel_bootstrap      ; tail call, no return
```

`_machine_startup` is four instructions and then branches into
`_kernel_bootstrap`, so a probe placed after that call is unreachable. Earlier
readings that treated `_machine_startup` as "returned or not" were measuring
nothing.

### The current stop: a `wfe` stall, not a fault

`_bsd_init` begins with `_platform_stall_panic_or_spin(4)`. That function tests a
platform stall mask; when the bit is set it appends one panic log record and then
loops on `wfe` forever:

```
ldr  x8, [stall mask]
tst  x8, x9
b.eq return
paniclog_append_noflush("...")
wfe
b    -4
```

The screen simply stops, which is exactly what was recorded. Every "stuck in
`_bsd_init`" reading is this single loop at the entry, which is also why skipping
`throttle_init` changed nothing: the stall happens before it.

Next: find what sets that mask bit (panic log availability is the first
candidate, i.e. the `pram` node and `/chosen/embedded-panic-log-size`), or skip
the stall function with the ret tool and see whether BSD initialisation proceeds.
## 2026-10-28: 32 checkpoints in one page

The reset probe answers one question per flash. The five blind rets
(`PE_init_iokit`, `kperf_init_early`, `ml_wait_max_cpus`,
`platform_stall_panic_or_spin`, `initialize_screen`) move the stop but do not
name it, so the next image carries all 32 questions at once.

`scripts/patch-xnu-log-probe.py` replaces 32 `bl` call sites in
`_kernel_bootstrap_thread` and `_bsd_init` with a stub that saves x0-x8 and LR,
writes one flag byte and tail-calls the original callee. The return address and
the argument registers are restored before the tail call, so the callee sees the
original call state. The stubs sit in the zero padding at the end of
`__TEXT_EXEC`, so the Mach-O layout and load address stay the same.

The probe writes `0xB9000010 + id` through `ml_phys_write_byte`. XNU closes its
identity map in `arm_vm_init`; the measured 3 GiB build loads the kernel at
physical `0xC007C000`, and the 4 GiB test window can only place it higher, so the
ring is below `gPhysBase` and outside managed memory. `ml_phys_write_byte` maps
the page through the uncached CPU copy window, writes it and unmaps, so the byte
reaches DRAM without a cache clean. `KebabBootLog` reads the verified ring
address, prints the set bytes and `last`/`next`, and clears the flags. The 8 MiB
ring header at `0xB9000000` remains reserved; stage 2 can add records at
`0xB9000010` when it replaces the flags.

The checkpoint table is in [`log-capture.md`](log-capture.md). The image built
for the next run is `out/logprobe-boot.img`; the device result is the next
measurement.

## 2026-10-28 late: the DRAM flag page does not survive the reset

The first logprobe image showed no flags, so the transport itself had to be
isolated before the checkpoint table could answer anything:

- `reset0`: `_PE_init_iokit` itself replaced by a PSCI reset -> the phone
  reboots, so the kernel really reaches checkpoint 0.
- `resetcave`: the checkpoint-0 call site branches to a cave that resets ->
  reboots, so `bl cave` and the cave fetch work.
- `helperreset` / `helperflush`: the helper and `Flush_Dcache` return, but the
  ring still reads `ff`.
- A temporary DarwinBoot colour band reports the actual `Address`: with the
  4 GiB window the kernel physical base is **A = `0x20007C000`**, higher than
  the 3 GiB build's `0xC007C000`.
- A one-stub BSS probe at `0x7B281B0` (a real `__DATA` BSS buffer) does a direct
  `strb 1`, `dc cvac`, `dsb`, then PSCI reset. It reboots, but the next reader
  still sees no prefix.

The store and reset execute (the reset follows them); the most consistent
explanation is that the boot chain reinitialises normal DRAM, so the flag page
does not carry across the reset. The `0xB9000010` ring, the `0xC0001000`/
`0x200001000` DRAM probes and the BSS page all fail the same way.

The **pram** page at `0x17F00FE0` is the panic-log page that is designed to
survive the reset. UEFI leaves it unmapped; a reader that adds the page to the
GCD and sets it uncached (`gDS->AddMemorySpace`,
`gDS->SetMemorySpaceAttributes`) plus a one-stub
`ml_phys_write_byte(0x17F00FE0, 1)` + reset image were built as
`out/prammap-boot.img` (SHA-256
`94bdad575f2beabee005027fc2ab54565ab379c0b58c51469b61e0efef6a95a6`). The device
result is pending. If the pram one-stub reports `last=0`, move the 32 stubs from
the ring to the pram bytes.

Separately, the current firmware has **no UFS block device**: `UFSDxe` is
disabled (vendor assert), `ScmDxeLA` is present, `TzDxeLA` is disabled and
`SdccDxe` is present. SDCC does not replace UFS, so there is still no
userdata/root volume.

## 2026-10-29: IOKit bring-up, the device tree read and the probe chain

### Where the kernel stops

The kernel runs `kernel_bootstrap`, the scheduler, the bootstrap thread and the
core services, and enters `PE_init_iokit`. With `PE_create_console`, `vcattach`,
`vc_progress_initialize` and `PE_get_default` replaced by a bare `ret` it reaches
`_InitIOKit` and stops inside `IOPlatformExpertDevice` startup. The last proven
checkpoint is the allocation of the root nub, the first unproven one is its
constructor.

### The bring-up kernel is reproducible

The kernel used for every probe below is the CI Mach-O with exactly four
functions replaced by a `ret`:

| Function | Why it is skipped |
| --- | --- |
| `PE_create_console` | blocks in `ml_io_map_wcomb` while mapping the framebuffer at `0x9C000000` |
| `vcattach` | the virtual console attaches to the same unmapped framebuffer |
| `vc_progress_initialize` | the progress bar needs that mapping too |
| `PE_get_default` | reads platform defaults from `/options` before the tree is usable |

`tools/patch-func-ret.py` writes one `ret` per symbol. The clean CI kernel plus
those four patches is
`sha256 db666dcf2b930fac1ed5da95accf2a4ed5f9e6c780ab7445284bbcbeedcc7d96`,
the `kernel-novc3.macho` this session builds from. The entry probe has to be
applied to that un-probed kernel: `patch-xnu-entry-probe.py` refuses a kernel
whose cave padding is not empty, so every experiment starts from the same four
patches.

### The constructor is not the stall

`__ZN22IOPlatformExpertDeviceC1Ev` (`0xfffffff0078dcb2c`, `0x44` bytes) contains
no loop, no indirect call and no lock. It chains the base constructors, stores
the vtable and bumps the metaclass instance count:

```
mov  x19, x0
adrp x20, 0xfffffff007af02d0          ; &gMetaClass
bl   IOService::IOService(OSMetaClass const*)   ; -> IORegistryEntry -> OSObject
add  x8, 0xfffffff0071bd408           ; __ZTV22IOPlatformExpertDevice + 0x10
str  x8, [x0]                         ; vptr
bl   OSMetaClass::instanceConstructed()         ; atomic increments, meta chain
mov  x0, x19
ret
```

The `IOService`, `IORegistryEntry` and `OSObject` constructors are the same shape
(store a vtable, set the retain count to one), so a stall in the constructor
would have to be a fault.

`_InitIOKit` then stores the object in `gRootNub` and calls the virtual
`IOPlatformExpertDevice::init(void *)` through vtable slot `+0x540`
(`0xfffffff0071bd948` -> `0xfffffff0078dcbb8`). That function is the first code
that reads the device tree:

```
IOPlatformExpertDevice::init(void *dtRoot)          ; 0xfffffff0078dcbb8
  cbz  x1, super::init()                            ; no tree at all
  bl   IODeviceTreeAlloc                            ; 0xfffffff00788b370
  cbz  x0, super::init()                            ; tree could not be built
  br   IOService::init(from, plane)                 ; 0xfffffff0078960b8, tail call
```

`IODeviceTreeAlloc` makes the device-tree plane, interns the property symbols,
looks up `/chosen/memory-map`, builds an `IORegistryEntry` for every node,
attaches them and then walks the new plane for interrupt sharing.

### The ADT handed to the kernel

The payload carries `kernel.macho.adt`, an Apple device tree built by
`scripts/make-kebab-adt.py`. The one delivered for these runs is 13,980 bytes
with 21 nodes, 122 properties and a maximum depth of two:

```
device-tree
├ chosen (memory-map, manifest-properties)
├ cpus (cpu0..cpu7)
├ psci
├ arm-io (gic, timer)
├ defaults
├ options
├ product
└ pram
```

A tree this small cannot make a walk loop, so a stall inside
`IODeviceTreeAlloc` would be a lock or a fault rather than an endless loop.

### Checkpoints proven on hardware

Each run installs one checkpoint and reads the panel: after the entry bars a
reboot at the configured delay means the call site was reached and the callee
returned. Checkpoints that pass today:

| Checkpoint | What it proves |
| --- | --- |
| `IOLibInit` | the IOKit pageable submap and lock group exist |
| `OSlibkernInit` | the C++ constructors ran, including `iokit_post_constructor_init` |
| `IOMachPortInitialize` | the mach port layer is up |
| `OSSymbol::withCStringNoCopy` | symbol interning works |
| `OSSet::withObjects` | container allocation works |
| `interruptAccountingInit` | the interrupt accounting lock exists |
| `IOPlatformExpertDevice::new` | the root nub allocation returned |

### The probe recipe

`board.toml [payload]` drives it:

```toml
entry_probe_reset_delay = 6
entry_probe_reset_after = "__Z17IODeviceTreeAllocPv"       # symbol whose call is watched
entry_probe_reset_in = "__ZN22IOPlatformExpertDevice4initEPv"  # function that makes the call
```

The patcher replaces the selected `bl` with a branch to a cave that performs the
call, counts six seconds of the 19.2 MHz counter and issues PSCI
`SYSTEM_RESET`. One flash therefore answers exactly one question, and the answer
is the presence or absence of a reboot, not a colour.

### Two traps found the hard way

- `debug=0x144` enables a debug branch that calls `SecureDTLookupEntry` on
  `/chosen/iBoot`; the phone stalls there before any IOKit code. `debug` must
  stay out of `kernel_args`.
- `cpus=1`, and `ret` patches on `ml_wait_max_cpus`, `kperf_init_early` and
  `platform_stall_panic_or_spin`, do not change the current stall.

### Build and flash chain

```
./unixnu build payload --uefi ~/Mu-Silicium --kernel kernel-novc3.macho \
    --unique-chip-id 0123456789abcdef
python3 scripts/pack-payload-mbr.py out/.../payload/payload.img <name>-payload-mbr.img
# kebab.fdf: FILE = <name>-payload-mbr.img at 0x00300000
./unixnu build firmware --uefi ~/Mu-Silicium
./unixnu bootimg --uefi ~/Mu-Silicium --dtb desktop-kebab.dtb --ramdisk none -o <name>-boot.img
fastboot flash boot_a <name>-boot.img
```

The payload is a 64 MiB FAT32 volume that must be wrapped in an MBR disk: the
firmware publishes the raw region at `0x00300000` and `PartitionDxe` only
creates a partition child when a partition table is present, so the FAT volume
sits behind a type `0x0c` partition at LBA 2048
(`scripts/pack-payload-mbr.py`). The boot image must carry the phone's live
device tree; the images here use `desktop-kebab.dtb`
(`sha256 8ba6ed34b8396102eac4d95769c56317aece50cf95b69cd3d98aa8353b27f293`),
because the 11 MiB raw dump has trailing data and fails the totalsize check.

### Where it stops now

The next checkpoint is placed after the `IODeviceTreeAlloc` call inside
`IOPlatformExpertDevice::init`. A reboot means the constructor returned and the
device tree read completed, which moves the stall into `IOService::init`
(`attachToParent` and the registry lock) or into `ConfigureIOKit` and
`configureDefaults`. A stop means the stall is inside `IODeviceTreeAlloc`.
