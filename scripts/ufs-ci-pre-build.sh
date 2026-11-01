#!/bin/bash
# UFS for the macOS UniXNU workflow: component-based tree, probe-only build.
#
#   bash device/scripts/ufs-ci-pre-build.sh
#
# The ShengUfs component compiles through scripts/patch-shengufs.py, which fixes
# the upstream tick-helper guard and calls build-arm-driver.py.  This step only
# selects the component and switches the driver builder to probe-only flags.
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
from pathlib import Path

catalog = Path('components.toml')
text = catalog.read_text()
old = 'build = ["build-arm-driver.py", "ShengUfs"]'
new = 'build = ["patch-shengufs.py"]'
if old not in text:
    raise SystemExit('components.toml ShengUfs build recipe changed; update the pre-build')
catalog.write_text(text.replace(old, new))
print('components.toml: ShengUfs builds through patch-shengufs.py')
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
       "                '-DKEBAB_UFS_PROBE_ONLY=1', '-mgeneral-regs-only']\n")
if old not in text:
    raise SystemExit('build-arm-driver.py NativeUfs branch changed; update the pre-build')
path.write_text(text.replace(old, new))
print('build-arm-driver.py: NativeUfs builds without NATIVE_VERIFIED_BLOCK')
PY
