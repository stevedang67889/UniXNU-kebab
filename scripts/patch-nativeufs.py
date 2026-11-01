#!/usr/bin/env python3
"""Build the device-repository NativeUfs driver probe-only (old framework).

The component recipe points here instead of straight at build-arm-driver.py.
Two source fixes are needed for a build without the sheng verified-root bundle:

  * timerTicks()/native_ufs_read_ticks are declared inside the
    NATIVE_VERIFIED_BLOCK guard but used by the transport's read path.
  * NativeUfsBlock (the IOBlockStorageDevice subclass) references the original
    IOStorageFamily ABI, which only the verified-root bundle provides. With
    KEBAB_UFS_PROBE_ONLY the class, its methods, the root publication block
    and NativeUfs::startRoot are compiled out; the probe path stays.

Then build-arm-driver.py (switched to probe-only flags by the pre-build)
compiles and archives the driver.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
SOURCE = ROOT / 'drivers/NativeUfs/NativeUfs.cpp'

OLD_TICKS = ('#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK\n'
             'volatile uint64_t native_ufs_verified_blocks = 0;\n'
             '// Time spent hashing/verifying and reading from the UFS transport, in generic-timer ticks (diagnostic only).\n'
             'volatile uint64_t native_ufs_verify_ticks = 0, native_ufs_read_ticks = 0;\n'
             'static inline uint64_t timerTicks (void) {uint64_t v; __asm__ volatile ("isb\\n\\tmrs %0, cntpct_el0" : "=r" (v)); return v;}\n'
             'volatile uint32_t native_ufs_integrity_failures = 0;\n')
NEW_TICKS = ('// Time spent hashing/verifying and reading from the UFS transport, in generic-timer ticks (diagnostic only).\n'
             'volatile uint64_t native_ufs_verify_ticks = 0, native_ufs_read_ticks = 0;\n'
             'static inline uint64_t timerTicks (void) {uint64_t v; __asm__ volatile ("isb\\n\\tmrs %0, cntpct_el0" : "=r" (v)); return v;}\n'
             '#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK\n'
             'volatile uint64_t native_ufs_verified_blocks = 0;\n'
             'volatile uint32_t native_ufs_integrity_failures = 0;\n')

GUARD = '#if !defined(KEBAB_UFS_PROBE_ONLY)'
STUB = ('#if defined(KEBAB_UFS_PROBE_ONLY)\n'
        'bool NativeUfs::startRoot (IOService *provider) { (void)provider; return false; }\n'
        '#endif')


def find(lines, predicate, start=0):
    for index in range(start, len(lines)):
        if predicate(lines[index]):
            return index
    raise SystemExit('NativeUfs: expected source marker not found')


def main():
    if not SOURCE.is_file():
        raise SystemExit(f'NativeUfs source missing: {SOURCE}')
    text = SOURCE.read_text()
    if OLD_TICKS in text:
        text = text.replace(OLD_TICKS, NEW_TICKS)
        print('NativeUfs: tick helpers moved out of the NATIVE_VERIFIED_BLOCK guard')
    elif NEW_TICKS not in text:
        print('NativeUfs: tick helper block not recognised; left unchanged')

    lines = text.split('\n')
    if GUARD not in lines:
        class_start = find(lines, lambda line: line.startswith('class NativeUfsBlock'))
        class_end = find(lines, lambda line: 'OSDefineMetaClassAndStructors (NativeUfsBlock' in line, class_start)
        methods_start = find(lines, lambda line: line.startswith('bool NativeUfsBlock::configure ('), class_end)
        methods_end = find(lines, lambda line: line.startswith('// Ordered publication of the root windows.'), methods_start)
        publication_end = find(lines, lambda line: line.startswith('// Memory snapshot (native-ufs-probe=13)'), methods_end)
        root_start = find(lines, lambda line: line.startswith('bool NativeUfs::startRoot ('), publication_end)
        root_end = find(lines, lambda line: line == '}', root_start + 1)
        inserts = [
            (class_start, [GUARD]),
            (class_end + 1, ['#endif']),
            (methods_start, [GUARD]),
            (methods_end, ['#endif']),
            (methods_end + 1, [GUARD]),
            (publication_end, ['#endif']),
            (root_start, [GUARD]),
            (root_end + 1, ['#endif', STUB]),
        ]
        for index, block in sorted(inserts, key=lambda item: item[0], reverse=True):
            lines[index:index] = block
        print('NativeUfs: block class and root publication compiled out under KEBAB_UFS_PROBE_ONLY')
    else:
        print('NativeUfs: probe-only guards already present')
    SOURCE.write_text('\n'.join(lines))

    result = subprocess.run([sys.executable, str(ROOT / 'scripts/build-arm-driver.py'), 'NativeUfs'])
    if result.returncode:
        log = ROOT / 'build/native-ufs/compile.log'
        if log.is_file():
            target = ROOT.parent / 'logs/component-NativeUfs-compile.log'
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(log, target)
            print(f'NativeUfs: compile log copied to {target}')
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
