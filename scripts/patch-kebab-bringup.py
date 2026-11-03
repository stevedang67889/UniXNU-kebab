#!/usr/bin/env python3
"""Re-apply the kebab bring-up skips to a freshly built kernel.

The 2026-10-31 bring-up kernel was a binary patch of the old base; a new source
build needs the same skips again. Everything is resolved by symbol name:

  * return-only: functions whose driver path is not implemented yet
  * mov x0,#0; ret: functions whose return value gates the next step
  * nop: the ml_set_interrupts_enabled(1) call in kernel_bootstrap_thread

_printf/_vprintf are deliberately NOT in the return list: the framebuffer log
(scripts/patch-kebab-fb-log.py) redirects them and the original UART path is
what used to hang.

    python3 scripts/patch-kebab-bringup.py <kernel.macho>
"""
import argparse
import struct
import subprocess
import shutil
from pathlib import Path

RET = 0xD65F03C0
MOV_X0_0 = 0x52800000
NOP = 0xD503201F
BRANCH = 0x14000000

RETURN_ONLY = [
    '_vcattach',
    '_PE_create_console',
    '_initialize_screen',
    '_vc_progress_initialize',
    '_vm_commpage_init',
    '_mac_policy_initmach',
    '_PE_lockdown_iokit',
    '_platform_stall_panic_or_spin',
    '_vm_commpage_text_init',
    '_machine_lockdown',
    '_kperf_init_early',
    '_throttle_init',
    '_vm_set_restrictions',
    '_kernel_list_tests',
    '_OSKextRemoveKextBootstrap',
    '_OSKextLog',
    '_os_log_with_args',
]
MOV0_RETURN = ['_PE_get_default', '_kernel_do_post']
NOP_CALL = ('_kernel_bootstrap_thread', '_ml_set_interrupts_enabled')

SEGMENTS = []


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


def load_segments(data):
    magic, _, _, _, ncmds = struct.unpack_from('<IiiII', data, 0)
    if magic != 0xFEEDFACF:
        raise SystemExit('not a 64-bit Mach-O kernel')
    SEGMENTS.clear()
    offset = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, offset)
        if cmd == 0x19:
            _, vmaddr, vmsize, fileoff, filesize, _, _, _, _ = struct.unpack_from(
                '<16sQQQQiiII', data, offset + 8)
            if filesize:
                SEGMENTS.append((vmaddr, vmsize, fileoff, filesize))
        offset += size


def file_offset(va):
    for vmaddr, vmsize, fileoff, filesize in SEGMENTS:
        if vmaddr <= va < vmaddr + min(vmsize, filesize):
            return fileoff + (va - vmaddr)
    raise SystemExit(f'{va:#x} is not in a file-backed segment')


def symbols(kernel):
    table = {}
    for line in run([tool('llvm-nm'), '-n', str(kernel)]).splitlines():
        parts = line.split()
        if len(parts) == 3:
            table[parts[2]] = int(parts[0], 16)
    return table


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('kernel', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    data = bytearray(args.kernel.read_bytes())
    load_segments(data)
    table = symbols(args.kernel)

    def write_word(va, value):
        struct.pack_into('<I', data, file_offset(va), value)

    for name in RETURN_ONLY:
        if name not in table:
            raise SystemExit(f'{args.kernel} has no {name}')
        va = table[name]
        current = struct.unpack_from('<I', data, file_offset(va))[0]
        if current != RET:
            write_word(va, RET)
            print(f'ret  {name} {va:#x}')
        else:
            print(f'ret  {name} {va:#x} (already)')

    for name in MOV0_RETURN:
        if name not in table:
            raise SystemExit(f'{args.kernel} has no {name}')
        va = table[name]
        current = struct.unpack_from('<I', data, file_offset(va))[0]
        if current != MOV_X0_0:
            write_word(va, MOV_X0_0)
            struct.pack_into('<I', data, file_offset(va) + 4, RET)
            print(f'mov0 {name} {va:#x}')
        else:
            print(f'mov0 {name} {va:#x} (already)')

    caller, callee = NOP_CALL
    if caller not in table or callee not in table:
        raise SystemExit(f'{args.kernel} has no {caller}/{callee}')
    start = table[caller]
    target = table[callee]
    end = min([address for address in table.values() if address > start] or [start + 0x4000])
    patched = False
    for va in range(start, end, 4):
        word = struct.unpack_from('<I', data, file_offset(va))[0]
        if word & 0xFC000000 == 0x94000000:  # bl
            delta = word & 0x3FFFFFF
            if delta & 0x2000000:
                delta -= 1 << 26
            if va + delta * 4 == target:
                if word != NOP:
                    write_word(va, NOP)
                    print(f'nop  {caller} bl {callee} at {va:#x}')
                else:
                    print(f'nop  {caller} bl {callee} at {va:#x} (already)')
                patched = True
                break
    if not patched:
        raise SystemExit(f'no bl {callee} found in {caller}')

    if not args.dry_run:
        args.kernel.write_bytes(data)
        print(f'patched {args.kernel}')


if __name__ == '__main__':
    main()
