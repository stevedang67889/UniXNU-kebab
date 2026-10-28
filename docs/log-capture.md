# Checkpoint log in RAM

The kernel stops without a console, so the only signal used to be a timed reset:
the patched kernel waits six seconds after a probe point and reboots, and a
reboot means the point was reached. That answers "did we get here" and nothing
else, and it costs a flashing cycle per question.

The checkpoint log keeps 32 answers in one page instead. A probe writes one byte
before it continues, the panel shows the bytes on the next boot, and one flash
answers 32 questions.

## How the ring is written without an identity map

XNU removes the identity map in `arm_vm_init`. The physical aperture it leaves
behind starts at `gPhysBase` and maps upwards. With the 3 GiB window the measured
kernel physical base is `0xC007C000`, and the 4 GiB test window places it higher
still, above the ring. `0xB9000000` therefore has no kernel virtual address and
a direct store faults.

`ml_phys_write_byte` handles exactly this case. `ml_phys_write_data` checks
`pmap_valid_address`; the ring is outside managed memory (below the kernel), so
it takes the CPU copy-window path: the page is mapped with `VM_WIMG_IO`
(uncached), written and unmapped. The write reaches DRAM without a cache clean.
The stub saves x0-x8 and LR, calls the helper, executes `dsb sy`, restores and
tail-calls the callee.

## Where the flags live

| Item | Value |
| --- | --- |
| Base | `0xB9000010` |
| Count | 32 |
| Values | `1` = checkpoint reached |

The 8 MiB ring header at `0xB9000000` is still reserved. Stage 1 only uses the
first 32 bytes of the record area.

## Writer

`scripts/patch-xnu-log-probe.py` patches 32 call sites in
`_kernel_bootstrap_thread` and `_bsd_init`. It replaces `bl callee` with
`bl stub`; the stub writes its byte through `ml_phys_write_byte`, restores the
argument registers and ends with `b callee`, so the return address, the
arguments and the condition flags are unchanged. The stubs live in the zero
padding at the end of `__TEXT_EXEC`, so the Mach-O layout does not change.

The checkpoints are, in execution order:

| id | point | id | point |
| --- | --- | --- | --- |
| 00 | `PE_init_iokit` | 16 | `kernel_do_post` |
| 01 | `PE_init_iokit` returned | 17 | `PE_lockdown_iokit` |
| 02 | startup stage 16 | 18 | `vm_set_restrictions` |
| 03 | interrupts on | 19 | `kperf_init_early` |
| 04 | `initialize_screen` | 20 | `bsd_init` |
| 05 | `vm_commpage_init` | 21 | bsd stall check |
| 06 | `vm_commpage_text_init` | 22 | `throttle_init` |
| 07 | `mac_policy_initmach` | 23 | `kauth_init` |
| 08 | `vnguard_policy_init` | 24 | `set_bsdtask_info` |
| 09 | `dtrace_early_init` | 25 | `kauth_cred_set` |
| 10 | `sdt_early_init` | 26 | `proc_ro_alloc` |
| 11 | startup final stage | 27 | proc name |
| 12 | `OSKextRemoveKextBootstrap` | 28 | proc name copy |
| 13 | `mbuf_tag_init` | 29 | proc lock |
| 14 | `machine_lockdown` | 30 | proc lock 2 |
| 15 | `kernel_list_tests` | 31 | proc spin lock |

The last set byte is the call that was entered. The next byte is the call that
was not reached, which means the previous callee did not return.

## Reader

`KebabBootLog` (a DXE driver in the firmware, `Depex TRUE`) runs on every boot
before the payload is loaded. It prints the 32 bytes in groups of 8, the set
indices and `last`/`next`, clears the flags and holds the panel for five seconds.
A capture is valid only as a prefix: flag 0 must be set and no clear byte may
follow a set one. Random or stale ring bytes therefore print
`BOOTLOG no valid checkpoint prefix` instead of a misleading list.

## Reading a capture

1. Boot the image under test; the kernel writes the flags as it passes the calls.
2. Reboot into fastboot and boot the firmware again.
3. Read the `BOOTLOG` lines on the panel. A warm reset keeps the page.

## 2026-10-28: the ring write is not reset-persistent

The first logprobe image still showed `ff`, so the transport was isolated before
the checkpoint table could be trusted:

- `_PE_init_iokit` replaced by a PSCI reset reboots, and a call site that
  branches to a cave that resets also reboots: the kernel reaches checkpoint 0
  and the cave executes.
- The helper and `Flush_Dcache` return, but the ring stays `ff`.
- A temporary DarwinBoot colour band measured the kernel physical base as
  `0x20007C000` for the 4 GiB window (base A).
- A one-stub direct `strb 1` + `dc cvac` + `dsb` + PSCI reset at a real
  `__DATA` BSS buffer (`0x7B281B0`) reboots, but the next reader still sees no
  valid prefix.

Because the reset follows the store instruction, the store is not faulting; the
flag is simply not there after the boot chain runs again. The working
hypothesis is that normal DRAM is reinitialised and only the pram panic page
survives. The `0xB9000010`, `0xC0001000` and `0x200001000` probes all failed the
same way.

pram (`0x17F00FE0`, the last 32 bytes of the 4 KiB panic page) is the persistent
page. UEFI does not map it by default, so the next reader adds it with
`gDS->AddMemorySpace` and `gDS->SetMemorySpaceAttributes` and reads it uncached.
The one-stub event is `ml_phys_write_byte(0x17F00FE0, 1)` followed by a PSCI
reset; the built image is `out/prammap-boot.img` (SHA-256
`94bdad575f2beabee005027fc2ab54565ab379c0b58c51469b61e0efef6a95a6`). If it
reports `last=0`, the 32 checkpoints move from the ring to `0x17F00FE0 + id`.

The ring and BSS experiments also changed the reader rules: do not clear the
flag page from the firmware (pram may be read-only to the firmware, and the
kernel clears its own BSS), and accept only the value `1`, not any non-zero byte.
