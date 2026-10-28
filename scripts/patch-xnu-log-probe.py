#!/usr/bin/env python3
"""Put 32 byte checkpoints into a COPY of the XNU kernel for the kebab bring-up.

This is stage 1 of the checkpoint log: every probe writes one byte to
0xB9000010 + id, waits for the write to reach memory and continues. The UEFI
KebabBootLog driver prints which bytes are set on the next boot. There is no
record ring, no timestamp and no reset, so a single flash answers 32 questions.

A probe sits on a call site, not at the callee entry. Replacing `bl callee`
with `bl stub`, where the stub ends in `b callee`, keeps the return address,
the arguments and the condition flags exactly as the original call did. The
stub saves x0-x8 and LR, writes the flag through ml_phys_write_byte, restores
and tail-calls the callee.

Why a helper and not a direct store? XNU removes its identity map in
arm_vm_init and the physical aperture starts at gPhysBase. The 0xB9000000 ring
is below the kernel physical base in both window configurations, so it has no
kernel VA. ml_phys_write_data checks pmap_valid_address; for this unmanaged
page it maps the page through a CPU copy window with VM_WIMG_IO (uncached),
writes and unmaps. The write reaches DRAM without a cache clean.

The stubs live in the zero padding at the end of __TEXT_EXEC, so the Mach-O
layout, the segment table and the load address do not change.

Usage:
  cp kernel.macho kernel-probe.macho
  scripts/patch-xnu-log-probe.py kernel-probe.macho
  scripts/patch-xnu-log-probe.py kernel-probe.macho --list
"""

import argparse
import re
import shutil
import struct
import subprocess
from collections import namedtuple
from pathlib import Path

PROBE_BASE = 0xB9000010
PROBE_COUNT = 32
STUB_SIZE = 20 * 4

# id, container function, callee, occurrence of the call inside the container
Checkpoint = namedtuple("Checkpoint", "label function callee occurrence")

# Execution order. The window starts at the PE_init_iokit call in
# _kernel_bootstrap_thread and ends in the first few calls of _bsd_init.
CHECKPOINTS = [
    Checkpoint("PE_init_iokit", "_kernel_bootstrap_thread", "_PE_init_iokit", 1),
    Checkpoint("PE_init_iokit returned", "_kernel_bootstrap_thread", "_ml_get_interrupts_enabled", 1),
    Checkpoint("startup stage 16", "_kernel_bootstrap_thread", "_kernel_startup_initialize_upto", 3),
    Checkpoint("interrupts on", "_kernel_bootstrap_thread", "_ml_set_interrupts_enabled", 1),
    Checkpoint("initialize_screen", "_kernel_bootstrap_thread", "_initialize_screen", 1),
    Checkpoint("vm_commpage_init", "_kernel_bootstrap_thread", "_vm_commpage_init", 1),
    Checkpoint("vm_commpage_text_init", "_kernel_bootstrap_thread", "_vm_commpage_text_init", 1),
    Checkpoint("mac_policy_initmach", "_kernel_bootstrap_thread", "_mac_policy_initmach", 1),
    Checkpoint("vnguard_policy_init", "_kernel_bootstrap_thread", "_vnguard_policy_init", 1),
    Checkpoint("dtrace_early_init", "_kernel_bootstrap_thread", "_dtrace_early_init", 1),
    Checkpoint("sdt_early_init", "_kernel_bootstrap_thread", "_sdt_early_init", 1),
    Checkpoint("startup final stage", "_kernel_bootstrap_thread", "_kernel_startup_initialize_upto", 4),
    Checkpoint("OSKextRemoveKextBootstrap", "_kernel_bootstrap_thread", "_OSKextRemoveKextBootstrap", 1),
    Checkpoint("mbuf_tag_init", "_kernel_bootstrap_thread", "_mbuf_tag_init", 1),
    Checkpoint("machine_lockdown", "_kernel_bootstrap_thread", "_machine_lockdown", 1),
    Checkpoint("kernel_list_tests", "_kernel_bootstrap_thread", "_kernel_list_tests", 1),
    Checkpoint("kernel_do_post", "_kernel_bootstrap_thread", "_kernel_do_post", 1),
    Checkpoint("PE_lockdown_iokit", "_kernel_bootstrap_thread", "_PE_lockdown_iokit", 1),
    Checkpoint("vm_set_restrictions", "_kernel_bootstrap_thread", "_vm_set_restrictions", 1),
    Checkpoint("kperf_init_early", "_kernel_bootstrap_thread", "_kperf_init_early", 1),
    Checkpoint("bsd_init", "_kernel_bootstrap_thread", "_bsd_init", 1),
    Checkpoint("bsd stall check", "_bsd_init", "_platform_stall_panic_or_spin", 1),
    Checkpoint("throttle_init", "_bsd_init", "_throttle_init", 1),
    Checkpoint("kauth_init", "_bsd_init", "_kauth_init", 1),
    Checkpoint("set_bsdtask_info", "_bsd_init", "_set_bsdtask_info", 1),
    Checkpoint("kauth_cred_set", "_bsd_init", "_kauth_cred_set", 1),
    Checkpoint("proc_ro_alloc", "_bsd_init", "_proc_ro_alloc", 1),
    Checkpoint("proc name", "_bsd_init", "_strlcpy", 1),
    Checkpoint("proc name copy", "_bsd_init", "_strlcpy", 2),
    Checkpoint("proc lock", "_bsd_init", "_lck_mtx_init", 1),
    Checkpoint("proc lock 2", "_bsd_init", "_lck_mtx_init", 2),
    Checkpoint("proc spin lock", "_bsd_init", "_lck_spin_init", 1),
]

# (name, vmaddr, vmsize, fileoff, filesize, initprot)
SEGMENTS = []


def load_segments(data):
    magic, _, _, _, ncmds = struct.unpack_from("<IiiII", data, 0)
    if magic != 0xFEEDFACF:
        raise SystemExit("not a thin 64-bit Mach-O")
    offset = 32
    SEGMENTS.clear()
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, offset)
        if cmd == 0x19:
            name = data[offset + 8:offset + 24].rstrip(b"\0").decode()
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", data, offset + 24)
            initprot = struct.unpack_from("<i", data, offset + 60)[0]
            if filesize:
                SEGMENTS.append((name, vmaddr, vmsize, fileoff, filesize, initprot))
        offset += size


def file_offset(va):
    for _, vmaddr, vmsize, fileoff, filesize, _ in SEGMENTS:
        if vmaddr <= va < vmaddr + min(vmsize, filesize):
            return fileoff + (va - vmaddr)
    raise SystemExit(f"{va:#x} is not in a file-backed segment of this kernel")


def executable_segment(va):
    for segment in SEGMENTS:
        name, vmaddr, vmsize, fileoff, filesize, initprot = segment
        if initprot & 4 and vmaddr <= va < vmaddr + min(vmsize, filesize):
            return segment
    raise SystemExit(f"{va:#x} is not in an executable file-backed segment")


def kernel_symbols(kernel):
    """Address -> name and name -> address, from llvm-nm."""
    nm = shutil.which("llvm-nm") or "/opt/homebrew/opt/llvm/bin/llvm-nm"
    by_address = {}
    by_name = {}
    for line in subprocess.run([nm, kernel], capture_output=True, text=True, check=True).stdout.splitlines():
        parts = line.split()
        if len(parts) == 3:
            address = int(parts[0], 16)
            by_address.setdefault(address, parts[2])
            by_name.setdefault(parts[2], address)
    return by_address, by_name


def function_range(by_address, name):
    starts = sorted(by_address)
    for address, symbol in by_address.items():
        if symbol == name:
            return address, next(start for start in starts if start > address)
    raise SystemExit(f"no symbol {name} in the kernel")


def find_call(data, low, high, callee, occurrence):
    """VA of the Nth `bl callee` in [low, high)."""
    hits = []
    for va in range(low, high, 4):
        word = struct.unpack_from("<I", data, file_offset(va))[0]
        if word >> 26 == 0b100101:
            delta = word & 0x3FFFFFF
            delta -= (1 << 26) if delta & (1 << 25) else 0
            if va + delta * 4 == callee:
                hits.append(va)
    if len(hits) < occurrence:
        raise SystemExit(f"{low:#x}..{high:#x} has {len(hits)} call(s), not {occurrence}")
    return hits[occurrence - 1]


def branch(from_va, to_va):
    delta = (to_va - from_va) // 4
    if (to_va - from_va) % 4 or not -(1 << 25) <= delta < (1 << 25):
        raise SystemExit(f"branch {from_va:#x} -> {to_va:#x} is out of range")
    return 0x14000000 | (delta & 0x3FFFFFF)


def branch_link(from_va, to_va):
    delta = (to_va - from_va) // 4
    if (to_va - from_va) % 4 or not -(1 << 25) <= delta < (1 << 25):
        raise SystemExit(f"bl {from_va:#x} -> {to_va:#x} is out of range")
    return 0x94000000 | (delta & 0x3FFFFFF)


def movz(register, immediate):
    return 0x52800000 | ((immediate & 0xFFFF) << 5) | register


def stub(va, callee, write_byte_va, index):
    """Save the call's argument registers, write one flag byte, tail-call callee."""
    words = [
        0xD10183FF,                                    # sub sp, sp, #0x60
        0xA90007E0,                                    # stp x0, x1, [sp]
        0xA9010FE2,                                    # stp x2, x3, [sp, #0x10]
        0xA90217E4,                                    # stp x4, x5, [sp, #0x20]
        0xA9031FE6,                                    # stp x6, x7, [sp, #0x30]
        0xF90023E8,                                    # str x8, [sp, #0x40]
        0xF90027FE,                                    # str x30, [sp, #0x48]
        movz(0, (PROBE_BASE & 0xFFFF) + index),        # low half of 0xB9000010 + id
        0x72B72000,                                    # movk w0, #0xB900, lsl #16
        movz(1, 1),                                    # data = 1
        branch_link(va + 10 * 4, write_byte_va),
        0xD5033F9F,                                    # dsb sy
        0xF94027FE,                                    # ldr x30, [sp, #0x48]
        0xA94007E0,                                    # ldp x0, x1, [sp]
        0xA9410FE2,                                    # ldp x2, x3, [sp, #0x10]
        0xA94217E4,                                    # ldp x4, x5, [sp, #0x20]
        0xA9431FE6,                                    # ldp x6, x7, [sp, #0x30]
        0xF94023E8,                                    # ldr x8, [sp, #0x40]
        0x910183FF,                                    # add sp, sp, #0x60
        branch(va + 19 * 4, callee),
    ]
    return b"".join(struct.pack("<I", word) for word in words)


def find_cave(data, by_address, need):
    """Zero padding after the last __TEXT_EXEC symbol; fall back to any hole."""
    for name, vmaddr, vmsize, fileoff, filesize, initprot in SEGMENTS:
        if name != "__TEXT_EXEC" or not initprot & 4:
            continue
        # The last symbol inside this segment, not necessarily a function.
        inside = [a for a in by_address if vmaddr <= a < vmaddr + filesize]
        if not inside:
            raise SystemExit("__TEXT_EXEC has no symbols")
        last = max(inside)
        blob = data[fileoff:fileoff + filesize]
        fallback = None
        for match in re.finditer(rb"\x00{%d,}" % need, blob):
            va = vmaddr + match.start()
            run_end = vmaddr + match.end()
            aligned = (va + 15) & ~15
            if aligned + need > run_end:
                continue
            if any(aligned <= symbol < aligned + need for symbol in by_address):
                continue
            if aligned > last:
                return aligned
            if fallback is None:
                fallback = aligned
        if fallback is not None:
            return fallback
    raise SystemExit(f"no {need:#x}-byte zero cave in __TEXT_EXEC; pass --cave")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kernel", type=Path, help="the COPY of the kernel to patch")
    parser.add_argument("--output", type=Path, help="write here instead of patching the input in place")
    parser.add_argument("--list", action="store_true", help="print the resolved checkpoint table and exit")
    parser.add_argument("--cave", type=lambda value: int(value, 0), help="address of a zero code cave")
    args = parser.parse_args()

    data = bytearray(args.kernel.read_bytes())
    load_segments(data)
    by_address, by_name = kernel_symbols(args.kernel)

    if "_ml_phys_write_byte" not in by_name:
        raise SystemExit("missing symbol _ml_phys_write_byte")
    write_byte_va = by_name["_ml_phys_write_byte"]

    sites = []
    for index, checkpoint in enumerate(CHECKPOINTS):
        if checkpoint.callee not in by_name:
            raise SystemExit(f"checkpoint {index}: no symbol {checkpoint.callee}")
        low, high = function_range(by_address, checkpoint.function)
        callee = by_name[checkpoint.callee]
        site = find_call(data, low, high, callee, checkpoint.occurrence)
        if struct.unpack_from("<I", data, file_offset(site))[0] >> 26 != 0b100101:
            raise SystemExit(f"checkpoint {index}: {site:#x} is not a bl")
        sites.append((index, checkpoint, site, callee))

    if args.list:
        print(f"probe bytes at {PROBE_BASE:#x}..{PROBE_BASE + PROBE_COUNT - 1:#x} (ring)")
        print(f"write helper {write_byte_va:#x}")
        for index, checkpoint, site, callee in sites:
            offset = site - by_name[checkpoint.function]
            print(f"{index:02d}  {site:#x}  {checkpoint.label:<28} "
                  f"{checkpoint.function}+{offset:#x} -> {checkpoint.callee} ({callee:#x})")
        return

    need = PROBE_COUNT * STUB_SIZE
    cave = args.cave or find_cave(data, by_address, need)
    segment = executable_segment(cave)
    if cave + need > segment[1] + min(segment[2], segment[4]):
        raise SystemExit(f"cave {cave:#x} + {need:#x} leaves its segment")
    if any(data[file_offset(cave):file_offset(cave) + need]):
        raise SystemExit(f"cave {cave:#x} is not zero for {need:#x} bytes")

    for index, checkpoint, site, callee in sites:
        at = cave + index * STUB_SIZE
        code = stub(at, callee, write_byte_va, index)
        if len(code) != STUB_SIZE:
            raise SystemExit(f"stub {index} is {len(code)} bytes, expected {STUB_SIZE}")
        offset = file_offset(at)
        if any(data[offset:offset + len(code)]):
            raise SystemExit(f"stub {index} at {at:#x} is not zero")
        data[offset:offset + len(code)] = code
        struct.pack_into("<I", data, file_offset(site), branch_link(site, at))

    out = args.output or args.kernel
    out.write_bytes(data)
    print(f"patched {args.kernel.name}: {len(sites)} checkpoints, stubs at {cave:#x} ({need:#x} bytes)")
    print(f"probe bytes {PROBE_BASE:#x}..{PROBE_BASE + PROBE_COUNT - 1:#x}")


if __name__ == "__main__":
    main()
