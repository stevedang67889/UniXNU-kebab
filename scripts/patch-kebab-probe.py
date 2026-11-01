#!/usr/bin/env python3
"""Paint-band checkpoints after the GIC stub (kebab, step A diagnostics).

The kernel has no console (initialize_screen is a ret stub), so the only
observable progress is what a stub paints on the framebuffer.  The GIC stub
stores its framebuffer mapping in unused padding at the end of __DATA
(scratch); this tool injects one small helper and replaces three call sites
with `bl <paint stub>`:

  bl _initialize_screen   in _kernel_bootstrap_thread     band 5, red
  bl startIOServiceMatching in _StartIOKitMatching          band 6, orange
  bl IOWorkLoop::workLoop in startIOServiceMatching         band 7, yellow

Each stub saves x0-x8 and LR, calls the helper (which only writes the already
mapped framebuffer: no allocation, no locks), restores and tail-calls the
original callee.  So it can run right after spllo without touching the zone
allocator that the boot is stuck in.

Apply after scripts/patch-kebab-gic.py: the helper goes in the zero padding
that follows the GIC stub at the end of __TEXT_EXEC.

    python3 scripts/patch-kebab-probe.py KERNEL
"""
import argparse
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXECUTE = 0x4
WRITE = 0x2

# (container function, callee, occurrence of the call, band, BGRX colour)
PROBES = [
    ('_bsd_init', '_vfs_mountroot', 1, 5, 0xFFFF0000),
    ('_bsd_init', '_IOSecureBSDRoot', 1, 6, 0xFFFF8000),
    ('_bsd_init', '_bsd_rooted_ramdisk', 1, 7, 0xFFFFFF00),
    ('_bsd_init', '_imageboot_needed', 1, 8, 0xFF80FF00),
    ('_bsd_init', '_devfs_kernel_mount', 1, 9, 0xFF00FF00),
    ('_bsd_init', '_siginit', 1, 10, 0xFF00FF80),
    ('_bsd_init', '_bsd_utaskbootstrap', 1, 11, 0xFF00FFFF),
    ('_bsd_init', '_machine_timeout_bsd_init', 1, 12, 0xFF0080FF),
]

# (container, callee, occurrence, function to call first, argument)
PRECALL_PROBES = [
    ('_skmem_cache_pre_init', '_ml_wait_max_cpus', 1, '_ml_set_max_cpus', 1),
]

# (function, band, BGRX colour) painted when the function returns
RETURN_PROBES = []

HELPER = r"""
typedef unsigned long long u64;
typedef unsigned int u32;

#ifndef SCRATCH
#error "SCRATCH must be supplied by the patcher"
#endif

#define SCRATCH_MAGIC 0x4b45424142494331ULL   /* "KEBABIC1" */

__attribute__((section(".text.a_entry"), used))
void kebab_probe(u32 band, u32 color)
{
	u64 fb = *(volatile u64 *)(u64)(SCRATCH + 0);
	u64 band_bytes = *(volatile u64 *)(u64)(SCRATCH + 8);
	u64 magic = *(volatile u64 *)(u64)(SCRATCH + 16);
	volatile u32 *p;
	u64 i, count;

	if (magic != SCRATCH_MAGIC || fb == 0 || band_bytes == 0) {
		return;
	}
	p = (volatile u32 *)(u64)(fb + band_bytes * (u64)band);
	if (*p == color) {
		return;
	}
	count = band_bytes & ~7ULL;
	for (i = 0; i < count; i += 4) {
		*p++ = color;
	}
	__asm__ volatile ("dsb sy" ::: "memory");
}
"""

SEGMENTS = []   # (vmaddr, vmsize, fileoff, filesize, initprot)
SECTIONS = []   # (vmaddr, vmsize)


def tool(name):
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f'{name} is required; install the LLVM toolchain')
    return path


def run(argv):
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f'{Path(argv[0]).name} failed:\n{result.stderr.strip()}')
    return result.stdout


def kernel_symbols(kernel):
    table = {}
    for line in run([tool('llvm-nm'), '-n', str(kernel)]).splitlines():
        parts = line.split()
        if len(parts) == 3:
            table.setdefault(int(parts[0], 16), parts[2])
    if not table:
        raise SystemExit(f'{kernel} has no symbol table')
    return table


def load_segments(data):
    magic, _, _, _, ncmds = struct.unpack_from('<IiiII', data, 0)
    if magic != 0xFEEDFACF:
        raise SystemExit('not a 64-bit Mach-O kernel')
    SEGMENTS.clear()
    SECTIONS.clear()
    offset = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, offset)
        if cmd == 0x19:
            (_, vmaddr, vmsize, fileoff, filesize, _, initprot, nsects,
             _) = struct.unpack_from('<16sQQQQiiII', data, offset + 8)
            if filesize:
                SEGMENTS.append((vmaddr, vmsize, fileoff, filesize, initprot))
            section = offset + 72
            for _ in range(nsects):
                _, _, addr, section_size = struct.unpack_from('<16s16sQQ', data, section)
                if section_size:
                    SECTIONS.append((addr, section_size))
                section += 80
        offset += size
    if not SEGMENTS:
        raise SystemExit('the kernel has no file-backed segments')


def file_offset(va):
    for vmaddr, vmsize, fileoff, filesize, _ in SEGMENTS:
        if vmaddr <= va < vmaddr + min(vmsize, filesize):
            return fileoff + (va - vmaddr)
    raise SystemExit(f'{va:#x} is not in a file-backed segment of this kernel')


def word(data, va):
    return struct.unpack_from('<I', data, file_offset(va))[0]


def segment_containing(va):
    for segment in SEGMENTS:
        vmaddr, vmsize, _, filesize, _ = segment
        if vmaddr <= va < vmaddr + min(vmsize, filesize):
            return segment
    raise SystemExit(f'{va:#x} is not in a file-backed segment of this kernel')


def branch(from_va, to_va):
    delta = (to_va - from_va) // 4
    if (to_va - from_va) % 4 or not -(1 << 25) <= delta < (1 << 25):
        raise SystemExit(f'branch from {from_va:#x} to {to_va:#x} is out of range')
    return 0x14000000 | (delta & 0x3FFFFFF)


def branch_link(from_va, to_va):
    delta = (to_va - from_va) // 4
    if (to_va - from_va) % 4 or not -(1 << 25) <= delta < (1 << 25):
        raise SystemExit(f'bl from {from_va:#x} to {to_va:#x} is out of range')
    return 0x94000000 | (delta & 0x3FFFFFF)


def function_range(table, name):
    starts = sorted(table)
    for start, symbol in table.items():
        if symbol == name:
            end = next((address for address in starts if address > start), None)
            if end is None:
                raise SystemExit(f'{name} is the last symbol; its end is unknown')
            return start, end
    raise SystemExit(f'no symbol {name} in the kernel')


def find_call(data, table, function, callee, occurrence):
    low, high = function_range(table, function)
    target = next((address for address, name in table.items() if name == callee), None)
    if target is None:
        raise SystemExit(f'no symbol {callee} in the kernel')
    hits = []
    for va in range(low, high, 4):
        value = word(data, va)
        if value >> 26 != 0b100101:
            continue
        delta = value & 0x3FFFFFF
        delta -= (1 << 26) if delta & (1 << 25) else 0
        if va + delta * 4 == target:
            hits.append(va)
    if len(hits) < occurrence:
        raise SystemExit(f'{function} has {len(hits)} call(s) to {callee}, not {occurrence}')
    return hits[occurrence - 1]


def scratch_address():
    writable = [segment for segment in SEGMENTS if segment[4] & WRITE and segment[1] > segment[3]]
    if not writable:
        raise SystemExit('no writable segment with a BSS tail')
    vmaddr, vmsize = max(writable, key=lambda segment: segment[0])[:2]
    address = (vmaddr + vmsize - 0x100) & ~0xF
    if any(addr <= address < addr + size for addr, size in SECTIONS):
        raise SystemExit(f'{address:#x} is inside a section; cannot use it as scratch')
    return address


def trailing_cave(data, site):
    vmaddr, vmsize, fileoff, filesize, initprot = segment_containing(site)
    if not initprot & EXECUTE:
        raise SystemExit(f'the segment holding {site:#x} is not executable')
    blob = data[fileoff:fileoff + min(vmsize, filesize)]
    last = len(blob) - 1
    while last >= 0 and blob[last] == 0:
        last -= 1
    start = (last + 1 + 15) & ~15
    return vmaddr + start, vmaddr + len(blob)


def assemble_c(source, va, workdir, defines, entry):
    clang, lld, objcopy = tool('clang'), tool('ld.lld'), tool('llvm-objcopy')
    nm, size_tool, readelf = tool('llvm-nm'), tool('llvm-size'), tool('llvm-readelf')
    src = workdir / 'probe.c'
    src.write_text(source)
    obj, elf, binary = (workdir / name for name in ('probe.o', 'probe.elf', 'probe.bin'))
    run([clang, '--target=aarch64-none-elf', '-ffreestanding', '-fno-builtin',
         '-fno-stack-protector', '-mgeneral-regs-only', '-fno-pic', '-mcmodel=tiny', '-Oz',
         *[f'-D{name}={value:#x}ULL' for name, value in defines.items()],
         '-c', str(src), '-o', str(obj)])
    undefined = run([nm, '-u', str(obj)]).split()
    if undefined:
        raise SystemExit('the helper needs external symbols: ' + ', '.join(undefined))
    sizes = run([size_tool, '--format=berkeley', str(obj)]).splitlines()[-1].split()
    if len(sizes) < 3 or int(sizes[1], 0) or int(sizes[2], 0):
        raise SystemExit(f'the helper has writable data ({sizes})')
    script = workdir / 'probe.ld'
    script.write_text('SECTIONS { . = %#x; .text : { *(.text.a_entry) *(.text.a_common) '
                      '*(.text*) *(.rodata*) } /DISCARD/ : { *(.comment) *(.note*) '
                      '*(.eh_frame*) *(.ARM.*) } }\n' % va)
    run([lld, '-T', str(script), '-e', entry, str(obj), '-o', str(elf)])
    run([objcopy, '-O', 'binary', '-j', '.text', str(elf), str(binary)])
    load = None
    for line in run([readelf, '-S', str(elf)]).splitlines():
        parts = line.replace('[', ' ').replace(']', ' ').split()
        if len(parts) >= 4 and parts[1] == '.text':
            load = int(parts[3], 16)
    if load is None or load < va:
        raise SystemExit('cannot find where the linker put .text')
    entry_address = None
    for line in run([nm, str(elf)]).splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == entry:
            entry_address = int(parts[0], 16)
    if entry_address != load:
        raise SystemExit(f'{entry} is at {entry_address:#x}, not at the first byte {load:#x}')
    return binary.read_bytes(), load, entry_address


def movz(register, immediate):
    return 0x52800000 | ((immediate & 0xFFFF) << 5) | register


def movk(register, immediate, shift):
    return 0x72800000 | ((shift // 16) << 21) | ((immediate & 0xFFFF) << 5) | register


def stub(va, callee, helper, band, color):
    words = [
        0xD10183FF,                          # sub sp, sp, #0x60
        0xA90007E0,                          # stp x0, x1, [sp]
        0xA9010FE2,                          # stp x2, x3, [sp, #0x10]
        0xA90217E4,                          # stp x4, x5, [sp, #0x20]
        0xA9031FE6,                          # stp x6, x7, [sp, #0x30]
        0xF90023E8,                          # str x8, [sp, #0x40]
        0xF90027FE,                          # str x30, [sp, #0x48]
        movz(0, band),                       # w0 = band
        movz(1, color & 0xFFFF),             # w1 = colour (low half)
        movk(1, color >> 16, 16),            # w1 = colour (high half)
        branch_link(va + 10 * 4, helper),    # bl helper
        0xF94027FE,                          # ldr x30, [sp, #0x48]
        0xA94007E0,                          # ldp x0, x1, [sp]
        0xA9410FE2,                          # ldp x2, x3, [sp, #0x10]
        0xA94217E4,                          # ldp x4, x5, [sp, #0x20]
        0xA9431FE6,                          # ldp x6, x7, [sp, #0x30]
        0xF94023E8,                          # ldr x8, [sp, #0x40]
        0x910183FF,                          # add sp, sp, #0x60
        branch(va + 18 * 4, callee),         # b callee
    ]
    return b''.join(struct.pack('<I', value) for value in words)


RET = 0xD65F03C0
STUB_SIZE = 0x60


def movz64(reg, imm):
    return 0xD2800000 | ((imm & 0xFFFF) << 5) | reg


def movk64(reg, imm, shift):
    return 0xF2800000 | ((shift // 16) << 21) | ((imm & 0xFFFF) << 5) | reg


def return_stub(va, helper, band, color):
    words = [
        0xA9BF7BE0,                          # stp x0, x30, [sp, #-0x10]!
        movz(0, band),                       # w0 = band
        movz(1, color & 0xFFFF),             # w1 = colour (low half)
        movk(1, color >> 16, 16),            # w1 = colour (high half)
        branch_link(va + 4 * 4, helper),     # bl helper
        0xA8C17BE0,                          # ldp x0, x30, [sp], #0x10
        0xD65F03C0,                          # ret
    ]
    return b''.join(struct.pack('<I', value) for value in words)


def precall_stub(va, callee, func, argument, lockdown):
    words = [
        0xD10183FF,                          # sub sp, sp, #0x60
        0xA90007E0,                          # stp x0, x1, [sp]
        0xA9010FE2,                          # stp x2, x3, [sp, #0x10]
        0xA90217E4,                          # stp x4, x5, [sp, #0x20]
        0xA9031FE6,                          # stp x6, x7, [sp, #0x30]
        0xF90023E8,                          # str x8, [sp, #0x40]
        0xF90027FE,                          # str x30, [sp, #0x48]
        movz64(8, lockdown & 0xFFFF),        # x8 = &lockdown_done
        movk64(8, (lockdown >> 16) & 0xFFFF, 16),
        movk64(8, (lockdown >> 32) & 0xFFFF, 32),
        movk64(8, (lockdown >> 48) & 0xFFFF, 48),
        0x52800029,                          # mov w9, #1
        0xB9000109,                          # str w9, [x8]
        movz(0, argument),                   # w0 = argument
        branch_link(va + 14 * 4, func),      # bl func
        0xF94027FE,                          # ldr x30, [sp, #0x48]
        0xA94007E0,                          # ldp x0, x1, [sp]
        0xA9410FE2,                          # ldp x2, x3, [sp, #0x10]
        0xA94217E4,                          # ldp x4, x5, [sp, #0x20]
        0xA9431FE6,                          # ldp x6, x7, [sp, #0x30]
        0xF94023E8,                          # ldr x8, [sp, #0x40]
        0x910183FF,                          # add sp, sp, #0x60
        branch(va + 22 * 4, callee),         # b callee
    ]
    return b''.join(struct.pack('<I', value) for value in words)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('kernel', type=Path, help='the kernel COPY already patched by patch-kebab-gic.py')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    data = bytearray(args.kernel.read_bytes())
    load_segments(data)
    table = kernel_symbols(args.kernel)
    by_name = {symbol: address for address, symbol in table.items()}
    for name in ('_pe_arm_init_interrupts', '_PE_state'):
        if name not in by_name:
            raise SystemExit(f'{args.kernel} has no {name}')

    scratch = scratch_address()
    cave_start, cave_end = trailing_cave(data, by_name['_pe_arm_init_interrupts'])
    sites = []
    for container, callee, occurrence, band, color in PROBES:
        site = find_call(data, table, container, callee, occurrence)
        sites.append({'kind': 'call', 'site': site, 'callee': by_name[callee],
                      'band': band, 'color': color, 'name': callee})
    for function, band, color in RETURN_PROBES:
        start, end = function_range(table, function)
        found = [va for va in range(start, end, 4) if word(data, va) == RET]
        if len(found) != 1:
            raise SystemExit(f'expected one ret in {function}, found '
                             + (', '.join(f'{va:#x}' for va in found) or 'none'))
        sites.append({'kind': 'return', 'site': found[0], 'callee': -1,
                      'band': band, 'color': color, 'name': function})
    for container, callee, occurrence, func, argument in PRECALL_PROBES:
        site = find_call(data, table, container, callee, occurrence)
        if '_lockdown_done' not in by_name:
            raise SystemExit('no symbol _lockdown_done in the kernel')
        sites.append({'kind': 'precall', 'site': site, 'callee': by_name[callee],
                      'func': by_name[func], 'argument': argument,
                      'lockdown': by_name['_lockdown_done'], 'band': 0,
                      'color': 0, 'name': f'{func}({argument}) before {callee}'})

    with tempfile.TemporaryDirectory() as tmp:
        helper, helper_load, helper_entry = assemble_c(
            HELPER, cave_start, Path(tmp), {'SCRATCH': scratch}, 'kebab_probe')
    stub_base = (helper_load + len(helper) + 15) & ~15
    if any(data[file_offset(helper_load):file_offset(helper_load) + len(helper)]):
        raise SystemExit(f'the cave at {helper_load:#x} is not empty')
    if stub_base + len(sites) * STUB_SIZE > cave_end:
        raise SystemExit('not enough room after the GIC stub for the paint probes')

    for index, site in enumerate(sites):
        va = stub_base + index * STUB_SIZE
        if site['kind'] == 'call':
            code = stub(va, site['callee'], helper_entry, site['band'], site['color'])
            patch = branch_link(site['site'], va)
        elif site['kind'] == 'return':
            code = return_stub(va, helper_entry, site['band'], site['color'])
            patch = branch(site['site'], va)
        else:
            code = precall_stub(va, site['callee'], site['func'], site['argument'],
                                site['lockdown'])
            patch = branch_link(site['site'], va)
        if any(data[file_offset(va):file_offset(va) + len(code)]):
            raise SystemExit(f'the cave at {va:#x} is not empty')
        if args.dry_run:
            print(f"{site['site']:#x} ({site['kind']} {site['name']}) at {va:#x}")
            continue
        data[file_offset(va):file_offset(va) + len(code)] = code
        struct.pack_into('<I', data, file_offset(site['site']), patch)
    if args.dry_run:
        print(f'helper {helper_entry:#x} ({len(helper)} bytes), stubs {stub_base:#x}, scratch {scratch:#x}')
        return
    data[file_offset(helper_load):file_offset(helper_load) + len(helper)] = helper
    args.kernel.write_bytes(data)
    print(f'patched {args.kernel}: helper at {helper_entry:#x} ({len(helper)} bytes), '
          f'{len(sites)} stubs at {stub_base:#x}, scratch {scratch:#x}')
    for site in sites:
        print(f"  {site['site']:#x} {site['kind']} {site['name']}")


if __name__ == '__main__':
    main()
