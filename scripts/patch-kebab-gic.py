#!/usr/bin/env python3
"""Kebab (OnePlus 8T / SM8250) GICv3 bring-up: binary patch A.

The board header has no HAS_GIC_V3 (it is only defined in
pexpert/pexpert/arm64/VMAPPLE.h), so pexpert/arm/pe_fiq.c compiles its GICv3
setup out and pe_init_fiq() is an empty function. Until an interrupt
controller exists, ArmCpu::prepareGic/mapGic/enableGic cannot run either --
they are reached through IOKit matching -- and matching itself stalls in
zalloc's lck_spin_sleep_with_inheritor because zalloc's slow path has no
timer. This patch breaks that circle before the source fix:

  pe_arm_init_interrupts tail -> kebab_gic_stub (scripts/kebab-gic-stub.c)
    gicd = ml_io_map(0x17a00000, 0x10000)
    gicr = ml_io_map(0x17a60000, 0x100000)
    - wake this CPU's redistributor
    - disable firmware's PPIs/SGIs and SPIs and drop their pending state
    - PPI 27 -> Group 1 non-secure (TrustZone owns Group 0)
    - GICD_CTLR.ARE_NS|EnableGrp1NS and ICC_SRE/PMR/BPR1/CTLR/IGRPEN1
    - ml_install_interrupt_handler(NULL, 0, NULL, kebab_irq, NULL)
    - kebab_irq: ICC_IAR1_EL1 loop; INTID 27 -> rtclock_intr(TRUE); EOI;
      stop at 1020 (spurious)
  sleh_irq() already calls cdp->interrupt_handler, so sleh.c is untouched.

The hook replaces the single final `ret` of pe_arm_init_interrupts (all its
return paths converge on one epilogue) with `b kebab_gic_stub`; the stub hands
x0, the function's return value, back unchanged. The stub is compiled
freestanding for an absolute VA and placed in the zero padding at the end of
__TEXT_EXEC, from where it reaches the device tree, the kernel and the
prelinked text. Before writing anything the tool validates the symbol set, the
exact tail instruction, the emptiness of the cave, the branch range and the
stub entry, and refuses a kernel that does not match.

Patch a COPY of the kernel, never the original:

    python3 scripts/patch-kebab-gic.py ~/kebabwork/kernel-bsd18.macho
    python3 scripts/patch-kebab-gic.py --dry-run ~/kebabwork/kernel-bsd18.macho

This is step A (binary patch, no kernel CI). Step B folds the same sequence
into pe_fiq.c plus HAS_GIC_V3 (BCM2837.h) and the pe_init_fiq() call.
"""
import argparse
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUB = ROOT / 'scripts' / 'kebab-gic-stub.c'

RET = 0xD65F03C0
BRANCH = 0x14000000
EXECUTE = 0x4                  # VM_PROT_EXECUTE in the Mach-O initprot
ALIGN = 16
MIN_CAVE = 0x400

# Device constants for kebab. The interrupt-controller window maps GICD at
# 0x17a00000; the redistributors start at 0x17a60000, one 0x20000 frame per CPU.
GICD_BASE = 0x17A00000
GICR_BASE = 0x17A60000
GICR_STRIDE = 0x20000
GICR_FRAMES = 8
VIRTUAL_TIMER_INTID = 27

SEGMENTS = []                  # (vmaddr, vmsize, fileoff, filesize, initprot)
SECTIONS = []                  # (vmaddr, vmsize)


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
    """Address -> name for the kernel's symbols, from llvm-nm."""
    table = {}
    for line in run([tool('llvm-nm'), '-n', str(kernel)]).splitlines():
        parts = line.split()
        if len(parts) == 3:
            table.setdefault(int(parts[0], 16), parts[2])
    if not table:
        raise SystemExit(f'{kernel} has no symbol table')
    return table


def load_segments(data):
    """Read the Mach-O segment and section tables; the layout differs per kernel."""
    magic, _, _, _, ncmds = struct.unpack_from('<IiiII', data, 0)
    if magic != 0xFEEDFACF:
        raise SystemExit('not a 64-bit Mach-O kernel')
    SEGMENTS.clear()
    SECTIONS.clear()
    offset = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, offset)
        if cmd == 0x19:  # LC_SEGMENT_64
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


def function_range(table, name):
    starts = sorted(table)
    for start, symbol in table.items():
        if symbol == name:
            end = next((address for address in starts if address > start), None)
            if end is None:
                raise SystemExit(f'{name} is the last symbol; its end is unknown')
            return start, end
    raise SystemExit(f'no symbol {name} in the kernel')


def hook_site(data, table):
    """The final `ret` of pe_arm_init_interrupts, where every return converges."""
    start, end = function_range(table, '_pe_arm_init_interrupts')
    site = end - 4
    found = [va for va in range(start, end, 4) if word(data, va) == RET]
    if found != [site]:
        raise SystemExit('expected exactly one ret at the tail of _pe_arm_init_interrupts, found '
                         + (', '.join(f'{va:#x}' for va in found) or 'none'))
    return site


def trailing_cave(data, site):
    """The zero padding that ends the executable segment holding the hook."""
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
        raise SystemExit(f'the padding at the end of the hook segment is only {cave_end - cave_start:#x} bytes')
    if any(addr <= cave_start < addr + size for addr, size in SECTIONS):
        raise SystemExit(f'{cave_start:#x} is inside a section, not padding')
    return cave_start, cave_end


def explicit_cave(va):
    vmaddr, vmsize, _, filesize, initprot = segment_containing(va)
    if not initprot & EXECUTE:
        raise SystemExit(f'{va:#x} is not in an executable segment')
    if va % 4:
        raise SystemExit(f'{va:#x} is not 4-byte aligned')
    return va, vmaddr + min(vmsize, filesize)


def scratch_address():
    """Unused zero padding at the end of the writable segment with a BSS tail.

    The GIC stub stores the framebuffer mapping there so the later paint probes
    (scripts/patch-kebab-probe.py) can draw without allocating anything.
    """
    writable = [segment for segment in SEGMENTS if segment[4] & 0x2 and segment[1] > segment[3]]
    if not writable:
        raise SystemExit('no writable segment with a BSS tail')
    vmaddr, vmsize = max(writable, key=lambda segment: segment[0])[:2]
    address = (vmaddr + vmsize - 0x100) & ~0xF
    if any(addr <= address < addr + size for addr, size in SECTIONS):
        raise SystemExit(f'{address:#x} is inside a section; cannot use it as scratch')
    return address


def assemble_c(source, va, workdir, defines, entry):
    """Build a freestanding C file for VA; returns (bytes, load, entry address).

    The linker may start .text a little after VA, so the load address is read
    back from the ELF and is what the caller must write the bytes to.
    """
    clang, lld, objcopy = tool('clang'), tool('ld.lld'), tool('llvm-objcopy')
    nm, size_tool, readelf = tool('llvm-nm'), tool('llvm-size'), tool('llvm-readelf')
    obj, elf, binary = (workdir / name for name in ('stub.o', 'stub.elf', 'stub.bin'))
    run([clang, '--target=aarch64-none-elf', '-ffreestanding', '-fno-builtin',
         '-fno-stack-protector', '-mgeneral-regs-only', '-fno-pic', '-mcmodel=tiny', '-Oz',
         *[f'-D{name}={value:#x}ULL' for name, value in defines.items()],
         '-c', str(source), '-o', str(obj)])
    undefined = run([nm, '-u', str(obj)]).split()
    if undefined:
        # Everything would have to be resolved by hand in the kernel's address space.
        raise SystemExit('the stub needs external symbols: ' + ', '.join(undefined))
    sizes = run([size_tool, '--format=berkeley', str(obj)]).splitlines()[-1].split()
    if len(sizes) < 3 or int(sizes[1], 0) or int(sizes[2], 0):
        # It runs from read-only kernel text: a writable global would fault on its first store.
        raise SystemExit(f'the stub has writable data ({sizes}); keep all state on the stack')
    script = workdir / 'stub.ld'
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


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('kernel', type=Path, help='the COPY of the kernel to patch in place')
    parser.add_argument('--cave', metavar='ADDRESS', type=lambda text: int(text, 0),
                        help='place the stub at this virtual address instead of the trailing padding; '
                             'only the zero-byte check guards this choice')
    parser.add_argument('--gicd-base', metavar='PA', type=lambda text: int(text, 0), default=GICD_BASE)
    parser.add_argument('--gicr-base', metavar='PA', type=lambda text: int(text, 0), default=GICR_BASE)
    parser.add_argument('--gicr-stride', metavar='BYTES', type=lambda text: int(text, 0), default=GICR_STRIDE)
    parser.add_argument('--gicr-frames', metavar='N', type=int, default=GICR_FRAMES)
    parser.add_argument('--intid', metavar='INTID', type=int, default=VIRTUAL_TIMER_INTID)
    parser.add_argument('--dry-run', action='store_true', help='assemble and verify but do not write')
    parser.add_argument('--fb-only', action='store_true', help='publish the framebuffer scratch and return without touching the GIC')
    args = parser.parse_args()

    if not args.kernel.is_file():
        raise SystemExit(f'{args.kernel}: no such file')
    if not STUB.is_file():
        raise SystemExit(f'{STUB}: the C stub is missing')

    data = bytearray(args.kernel.read_bytes())
    load_segments(data)
    table = kernel_symbols(args.kernel)
    by_name = {symbol: address for address, symbol in table.items()}
    for name in ('_pe_arm_init_interrupts', '_ml_io_map', '_ml_install_interrupt_handler',
                 '_rtclock_intr', '_sleh_irq', '_PE_state'):
        if name not in by_name:
            raise SystemExit(f'{args.kernel} has no {name}; it is not the kernel this patch targets')

    site = hook_site(data, table)
    cave_start, cave_end = explicit_cave(args.cave) if args.cave is not None else trailing_cave(data, site)
    if args.fb_only:
        defines_fb_only = {'FB_ONLY': 1}
    else:
        defines_fb_only = {}

    defines = {
        'GICD_BASE': args.gicd_base,
        'GICR_BASE': args.gicr_base,
        'GICR_STRIDE': args.gicr_stride,
        'GICR_FRAMES': args.gicr_frames,
        'VIRTUAL_TIMER_INTID': args.intid,
        'ML_IO_MAP': by_name['_ml_io_map'],
        'ML_INSTALL_INTERRUPT_HANDLER': by_name['_ml_install_interrupt_handler'],
        'RTCLOCK_INTR': by_name['_rtclock_intr'],
        'PE_STATE': by_name['_PE_state'],
        'SCRATCH': scratch_address(),
    }
    defines.update(defines_fb_only)
    with tempfile.TemporaryDirectory() as tmp:
        code, load, entry = assemble_c(STUB, cave_start, Path(tmp), defines, 'kebab_gic_stub')
    if load < cave_start or load + len(code) > cave_end:
        raise SystemExit(f'the {len(code)}-byte stub at {load:#x} does not fit '
                         f'{cave_start:#x}..{cave_end:#x}')
    if any(data[file_offset(load):file_offset(load) + len(code)]):
        raise SystemExit(f'the cave bytes at {load:#x} are not empty')
    target = branch(site, entry)

    if args.dry_run:
        print(f'{args.kernel}: would replace {site:#x} ({word(data, site):#010x}) with '
              f'b {entry:#x}; {len(code)} bytes at {load:#x}')
        print(f'  GICD {args.gicd_base:#x}, GICR {args.gicr_base:#x}, PPI {args.intid} -> Group 1 NS')
        return
    # Code first: a partially written image must never branch into garbage.
    data[file_offset(load):file_offset(load) + len(code)] = code
    struct.pack_into('<I', data, file_offset(site), target)
    args.kernel.write_bytes(data)
    print(f'patched {args.kernel}: pe_arm_init_interrupts tail {site:#x} ret -> b {entry:#x} '
          f'({len(code)} bytes at {load:#x})')
    print(f'  PPI {args.intid} -> Group 1 NS, GICD {args.gicd_base:#x}, '
          f'GICR {args.gicr_base:#x}; kebab_irq -> rtclock_intr')


if __name__ == '__main__':
    main()
