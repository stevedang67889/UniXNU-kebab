#!/usr/bin/env python3
"""Kebab (OnePlus 8T / SM8250) framebuffer log: binary patch.

The kernel has no console on this board (initialize_screen, vcattach and
PE_create_console are ret stubs, and there is no UART), so everything the
kernel prints is invisible. This patch redirects the two public print entry
points to a freestanding stub (scripts/kebab-fb-log.c):

  _printf   variadic: the naked entry hands the entry SP to the formatter
            (Darwin arm64 passes anonymous arguments on the stack)
  _vprintf  va_list: x1 already points at the anonymous argument slots

IOLog and kprintf funnel into one of these. The panel mapping is the one the
GIC stub (scripts/kebab-gic-stub.c) recorded in the scratch block at the end of
the writable segment, so patch-kebab-gic.py must run first.

    python3 scripts/patch-kebab-fb-log.py ~/kebabwork/kernel-gic19.macho
    python3 scripts/patch-kebab-fb-log.py --dry-run ~/kebabwork/kernel-gic19.macho

Patch a COPY of the kernel, never the original.
"""
import argparse
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUB = ROOT / 'scripts' / 'kebab-fb-log.c'

BRANCH = 0x14000000
EXECUTE = 0x4
ALIGN = 16
MIN_CAVE = 0x400

SEGMENTS = []
SECTIONS = []


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


def segment_containing(va):
    for segment in SEGMENTS:
        vmaddr, vmsize, _, filesize, _ = segment
        if vmaddr <= va < vmaddr + min(vmsize, filesize):
            return segment
    raise SystemExit(f'{va:#x} is not in a file-backed segment of this kernel')


def word(data, va):
    return struct.unpack_from('<I', data, file_offset(va))[0]


def branch(from_va, to_va):
    delta = (to_va - from_va) // 4
    if (to_va - from_va) % 4 or not -(1 << 25) <= delta < (1 << 25):
        raise SystemExit(f'branch from {from_va:#x} to {to_va:#x} is out of range')
    return BRANCH | (delta & 0x3FFFFFF)


def trailing_cave(data, site):
    vmaddr, vmsize, fileoff, filesize, initprot = segment_containing(site)
    if not initprot & EXECUTE:
        raise SystemExit(f'the segment holding {site:#x} is not executable')
    blob = data[fileoff:fileoff + min(vmsize, filesize)]
    last = len(blob) - 1
    while last >= 0 and blob[last] == 0:
        last -= 1
    start = (last + 1 + ALIGN - 1) & ~(ALIGN - 1)
    cave_start = vmaddr + start
    cave_end = vmaddr + len(blob)
    if cave_end - cave_start < MIN_CAVE:
        raise SystemExit(f'the padding after the GIC stub is only {cave_end - cave_start:#x} bytes')
    if any(addr <= cave_start < addr + size for addr, size in SECTIONS):
        raise SystemExit(f'{cave_start:#x} is inside a section, not padding')
    return cave_start, cave_end


def scratch_address():
    writable = [segment for segment in SEGMENTS if segment[4] & 0x2 and segment[1] > segment[3]]
    if not writable:
        raise SystemExit('no writable segment with a BSS tail')
    vmaddr, vmsize = max(writable, key=lambda segment: segment[0])[:2]
    address = (vmaddr + vmsize - 0x100) & ~0xF
    if any(addr <= address < addr + size for addr, size in SECTIONS):
        raise SystemExit(f'{address:#x} is inside a section; cannot use it as scratch')
    return address


def assemble_c(source, va, workdir, defines, entries, includes):
    clang, lld, objcopy = tool('clang'), tool('ld.lld'), tool('llvm-objcopy')
    nm, size_tool, readelf = tool('llvm-nm'), tool('llvm-size'), tool('llvm-readelf')
    obj, elf, binary = (workdir / name for name in ('stub.o', 'stub.elf', 'stub.bin'))
    include_args = []
    for path in includes:
        include_args.extend(['-I', path])
    run([clang, '--target=aarch64-none-elf', '-ffreestanding', '-fno-builtin',
         '-fno-stack-protector', '-mgeneral-regs-only', '-fno-pic', '-mcmodel=tiny', '-Oz',
         *include_args,
         *[f'-D{name}={value:#x}ULL' for name, value in defines.items()],
         '-c', str(source), '-o', str(obj)])
    undefined = run([nm, '-u', str(obj)]).split()
    if undefined:
        raise SystemExit('the stub needs external symbols: ' + ', '.join(undefined))
    sizes = run([size_tool, '--format=berkeley', str(obj)]).splitlines()[-1].split()
    if len(sizes) < 3 or int(sizes[1], 0) or int(sizes[2], 0):
        raise SystemExit(f'the stub has writable data ({sizes}); keep all state in the scratch block')
    script = workdir / 'stub.ld'
    script.write_text('SECTIONS { . = %#x; .text : { *(.text.a_entry) *(.text.a_common) '
                      '*(.text*) *(.rodata*) } /DISCARD/ : { *(.comment) *(.note*) '
                      '*(.eh_frame*) *(.ARM.*) } }\n' % va)
    run([lld, '-T', str(script), '-e', entries[0], str(obj), '-o', str(elf)])
    run([objcopy, '-O', 'binary', '-j', '.text', str(elf), str(binary)])
    load = None
    for line in run([readelf, '-S', str(elf)]).splitlines():
        parts = line.replace('[', ' ').replace(']', ' ').split()
        if len(parts) >= 4 and parts[1] == '.text':
            load = int(parts[3], 16)
    if load is None or load < va:
        raise SystemExit('cannot find where the linker put .text')
    symbols = {}
    for line in run([nm, str(elf)]).splitlines():
        parts = line.split()
        if len(parts) == 3:
            symbols[parts[2]] = int(parts[0], 16)
    for name in entries:
        if name not in symbols:
            raise SystemExit(f'{name} is not in the linked stub')
    if symbols[entries[0]] != load:
        raise SystemExit(f'{entries[0]} is at {symbols[entries[0]]:#x}, not at the first byte {load:#x}')
    return binary.read_bytes(), load, symbols


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('kernel', type=Path, help='the COPY of the kernel to patch in place')
    parser.add_argument('--font-dir', type=Path,
                        help='directory holding Font.h (default: $UNIXNU_UEFI/.../FrameBufferSerialPortLib)')
    parser.add_argument('--cave', metavar='ADDRESS', type=lambda text: int(text, 0),
                        help='place the stub at this virtual address instead of the trailing padding')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not args.kernel.is_file():
        raise SystemExit(f'{args.kernel}: no such file')
    if not STUB.is_file():
        raise SystemExit(f'{STUB}: the C stub is missing')
    if args.font_dir:
        font_dir = args.font_dir
    else:
        uefi = os.environ.get('UNIXNU_UEFI') or '/home/moemo/Mu-Silicium'
        font_dir = Path(uefi) / 'Silicon/Silicium/SiliciumPkg/Library/FrameBufferSerialPortLib'
    if not (font_dir / 'Font.h').is_file():
        raise SystemExit(f'{font_dir}/Font.h not found; pass --font-dir or set UNIXNU_UEFI')

    data = bytearray(args.kernel.read_bytes())
    load_segments(data)
    table = kernel_symbols(args.kernel)
    by_name = {symbol: address for address, symbol in table.items()}
    for name in ('_printf', '_vprintf', '_PE_state'):
        if name not in by_name:
            raise SystemExit(f'{args.kernel} has no {name}; it is not the kernel this patch targets')

    hooks = {'_printf': 'kebab_fb_log', '_vprintf': 'kebab_fb_log_vprintf'}
    site = by_name['_printf']
    if args.cave is not None:
        vmaddr, vmsize, _, filesize, initprot = segment_containing(args.cave)
        if not initprot & EXECUTE:
            raise SystemExit(f'{args.cave:#x} is not in an executable segment')
        if args.cave % 4:
            raise SystemExit(f'{args.cave:#x} is not 4-byte aligned')
        cave_start, cave_end = args.cave, vmaddr + min(vmsize, filesize)
    else:
        cave_start, cave_end = trailing_cave(data, site)
    defines = {'PE_STATE': by_name['_PE_state'], 'SCRATCH': scratch_address()}
    with tempfile.TemporaryDirectory() as tmp:
        code, load, symbols = assemble_c(STUB, cave_start, Path(tmp), defines,
                                         ['kebab_fb_log', 'kebab_fb_log_vprintf'], [str(font_dir)])
    if load < cave_start or load + len(code) > cave_end:
        raise SystemExit(f'the {len(code)}-byte stub at {load:#x} does not fit '
                         f'{cave_start:#x}..{cave_end:#x}')
    if any(data[file_offset(load):file_offset(load) + len(code)]):
        raise SystemExit(f'the cave bytes at {load:#x} must be empty')

    targets = {}
    for symbol, entry in hooks.items():
        targets[symbol] = branch(by_name[symbol], symbols[entry])

    if args.dry_run:
        for symbol, entry in hooks.items():
            print(f'{args.kernel}: would branch {by_name[symbol]:#x} ({symbol}) '
                  f'to {symbols[entry]:#x}')
        print(f'  {len(code)} bytes at {load:#x}')
        return
    data[file_offset(load):file_offset(load) + len(code)] = code
    for symbol, value in targets.items():
        struct.pack_into('<I', data, file_offset(by_name[symbol]), value)
    args.kernel.write_bytes(data)
    print(f'patched {args.kernel}: {len(code)} bytes at {load:#x}')
    for symbol in hooks:
        print(f'  {symbol} {by_name[symbol]:#x} -> {symbols[hooks[symbol]]:#x}')


if __name__ == '__main__':
    main()
