#!/usr/bin/env python3
"""Build the pinned ShengUfs source probe-only (no verified-root bundle).

The component recipe points here instead of straight at build-arm-driver.py:
the transport's read path uses timerTicks() and native_ufs_read_ticks, which
upstream keeps inside the NATIVE_VERIFIED_BLOCK guard.  With that guard off
the compile fails, so move the two helpers out of it first.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
SOURCE = ROOT / 'drivers/ShengUfs/NativeUfs.cpp'
OLD = ('#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK\n'
       'volatile uint64_t native_ufs_verified_blocks = 0;\n'
       '// Time spent hashing/verifying and reading from the UFS transport, in generic-timer ticks (diagnostic only).\n'
       'volatile uint64_t native_ufs_verify_ticks = 0, native_ufs_read_ticks = 0;\n'
       'static inline uint64_t timerTicks (void) {uint64_t v; __asm__ volatile ("isb\\n\\tmrs %0, cntpct_el0" : "=r" (v)); return v;}\n'
       'volatile uint32_t native_ufs_integrity_failures = 0;\n')
NEW = ('// Time spent hashing/verifying and reading from the UFS transport, in generic-timer ticks (diagnostic only).\n'
       'volatile uint64_t native_ufs_verify_ticks = 0, native_ufs_read_ticks = 0;\n'
       'static inline uint64_t timerTicks (void) {uint64_t v; __asm__ volatile ("isb\\n\\tmrs %0, cntpct_el0" : "=r" (v)); return v;}\n'
       '#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK\n'
       'volatile uint64_t native_ufs_verified_blocks = 0;\n'
       'volatile uint32_t native_ufs_integrity_failures = 0;\n')

if not SOURCE.is_file():
    raise SystemExit(f'ShengUfs source missing: {SOURCE}')
text = SOURCE.read_text()
if OLD in text:
    SOURCE.write_text(text.replace(OLD, NEW))
    print('ShengUfs: tick helpers moved out of the NATIVE_VERIFIED_BLOCK guard')
else:
    print('ShengUfs: guard block already patched or different, source left unchanged')

result = subprocess.run([sys.executable, str(ROOT / 'scripts/build-arm-driver.py'), 'ShengUfs'])
if result.returncode:
    log = ROOT / 'build/sheng-ufs/compile.log'
    if log.is_file():
        target = ROOT.parent / 'logs/component-ShengUfs-compile.log'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(log, target)
        print(f'ShengUfs: compile log copied to {target}')
    raise SystemExit(result.returncode)
