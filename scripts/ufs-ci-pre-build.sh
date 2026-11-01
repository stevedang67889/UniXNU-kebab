#!/bin/bash
# UFS for the macOS UniXNU workflow: component-based tree, probe-only build.
#
#   bash device/scripts/ufs-ci-pre-build.sh
#
# ShengUfs is selected as the component the framework already pins.  Its block
# class needs the sheng verified-root bundle, which kebab does not have yet, so
# build-arm-driver.py is patched in the framework checkout to drop
# -DNATIVE_VERIFIED_BLOCK.  On a compile failure the full compiler output is
# copied into the work logs directory, which the workflow uploads.
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
text = text.replace(old, new)

old_fail = "if result.returncode:\n    raise SystemExit(result.stdout[-3500:])\n"
new_fail = ("if result.returncode:\n"
            "    logdir = ROOT.parent / 'logs'\n"
            "    logdir.mkdir(parents=True, exist_ok=True)\n"
            "    (logdir / ('component-' + identifier + '-compile.log')).write_bytes((out / 'compile.log').read_bytes())\n"
            "    raise SystemExit(result.stdout[-3500:])\n")
if old_fail not in text:
    raise SystemExit('build-arm-driver.py failure path changed; update the pre-build')
path.write_text(text.replace(old_fail, new_fail))
print('build-arm-driver.py: probe-only and full compile log on failure')
PY
