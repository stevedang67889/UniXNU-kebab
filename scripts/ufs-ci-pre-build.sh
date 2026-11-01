#!/bin/bash
# UFS for the macOS UniXNU workflow: component-based tree, probe-only build.
#
#   bash device/scripts/ufs-ci-pre-build.sh
#
# 1. Select the pinned ShengUfs component in the device board.
# 2. Materialize its submodule and move timerTicks()/native_ufs_read_ticks out
#    of the NATIVE_VERIFIED_BLOCK guard: the transport's read path uses them
#    and the probe-only build has no verified-root bundle yet.
# 3. Patch build-arm-driver.py to build NativeUfs without the verified block.
set -euo pipefail
DEVICE=${UNIXNU_DEVICE:-$PWD/device}

python3 - "$DEVICE" <<'PY'
import re
import sys
from pathlib import Path

device = Path(sys.argv[1])
board = device / 'board.toml'
text = board.read_text()
if '"ShengUfs"' not in text:
    match = re.search(r'^include\s*=\s*\[(.*?)\]', text, re.M)
    if not match:
        raise SystemExit('board.toml [components] include not found')
    items = [item.strip() for item in match.group(1).split(',') if item.strip()]
    items.append('"ShengUfs"')
    text = text[:match.start()] + 'include = [' + ', '.join(items) + ']' + text[match.end():]
for argument in ('native-ufs-diagnostics=1', 'native-ufs-probe=2'):
    if argument not in text:
        text = text.replace('kernel_args = [\n  "-v",',
                            'kernel_args = [\n  "-v",\n  "%s",' % argument)
board.write_text(text)
print('board.toml: ShengUfs selected, native-ufs-diagnostics=1, native-ufs-probe=2')

config = device / 'configs/kebab_defconfig'
text = config.read_text().replace('CONFIG_XNU_NATIVE_UFS=n', 'CONFIG_XNU_NATIVE_UFS=y')
config.write_text(text)
print('defconfig: CONFIG_XNU_NATIVE_UFS=y')
PY

python3 - <<'PY'
import subprocess
from pathlib import Path

try:
    subprocess.run(['git', 'submodule', 'update', '--init', '--depth', '1', 'drivers/ShengUfs'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
except FileNotFoundError:
    pass

path = Path('drivers/ShengUfs/NativeUfs.cpp')
if not path.is_file():
    print('ShengUfs: submodule source missing; left unchanged')
    raise SystemExit(0)

text = path.read_text()
old = ('#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK\n'
       'volatile uint64_t native_ufs_verified_blocks = 0;\n'
       '// Time spent hashing/verifying and reading from the UFS transport, in generic-timer ticks (diagnostic only).\n'
       'volatile uint64_t native_ufs_verify_ticks = 0, native_ufs_read_ticks = 0;\n'
       'static inline uint64_t timerTicks (void) {uint64_t v; __asm__ volatile ("isb\\n\\tmrs %0, cntpct_el0" : "=r" (v)); return v;}\n'
       'volatile uint32_t native_ufs_integrity_failures = 0;\n')
new = ('// Time spent hashing/verifying and reading from the UFS transport, in generic-timer ticks (diagnostic only).\n'
       'volatile uint64_t native_ufs_verify_ticks = 0, native_ufs_read_ticks = 0;\n'
       'static inline uint64_t timerTicks (void) {uint64_t v; __asm__ volatile ("isb\\n\\tmrs %0, cntpct_el0" : "=r" (v)); return v;}\n'
       '#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK\n'
       'volatile uint64_t native_ufs_verified_blocks = 0;\n'
       'volatile uint32_t native_ufs_integrity_failures = 0;\n')
if old in text:
    path.write_text(text.replace(old, new))
    print('ShengUfs: timerTicks/native_ufs_read_ticks moved out of the guard')
else:
    print('ShengUfs: guard block not found; source left unchanged')
PY

python3 - <<'PY'
from pathlib import Path

path = Path('scripts/build-arm-driver.py')
text = path.read_text()
old = ("if component == 'NativeUfs':\n"
       "    command += ['-I', str(ROOT / 'kits/VerifiedRoot/include'), '-I', str(ROOT / 'drivers/VirtioMmioBlock'),\n"
       "                '-DNATIVE_VERIFIED_BLOCK=1', '-DNATIVE_VERIFIED_BLOCK_MULTI_POLICY=1', '-mgeneral-regs-only']\n")
new = ("if component == 'NativeUfs':\n"
       "    # kebab bring-up: probe-only, no verified-root bundle yet.\n"
       "    command += ['-I', str(ROOT / 'kits/VerifiedRoot/include'), '-I', str(ROOT / 'drivers/VirtioMmioBlock'),\n"
       "                '-mgeneral-regs-only']\n")
if old not in text:
    raise SystemExit('build-arm-driver.py NativeUfs branch changed; update the pre-build')
path.write_text(text.replace(old, new))
print('build-arm-driver.py: NativeUfs builds without NATIVE_VERIFIED_BLOCK')
PY
