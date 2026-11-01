#!/bin/bash
# UFS pre-build for the 407db1a framework (branch old-fw).
#
# The working base kernel was built by this framework with only the (empty)
# soc patch series. The one patch the build does need on the current runner is
# local-availability.patch (the availability.pl lookup), so the board applies a
# device series with just that entry.
set -euo pipefail
DEVICE=${UNIXNU_DEVICE:-$PWD/device}

python3 - "$DEVICE" <<'PY'
import re
import subprocess
import sys
from pathlib import Path

device = Path(sys.argv[1])
board = device / 'board.toml'
text = board.read_text()

if '"NativeUfs"' not in text:
    match = re.search(r'^include\s*=\s*\[(.*?)\]', text, re.M)
    if not match:
        raise SystemExit('board.toml [components] include not found')
    items = [item.strip() for item in match.group(1).split(',') if item.strip()]
    items.append('"NativeUfs"')
    text = text[:match.start()] + 'include = [' + ', '.join(items) + ']' + text[match.end():]

text = re.sub(r'^patch_series\s*=\s*\[.*?\]\s*$',
              'patch_series = ["device:patches/xnu/series"]', text, count=1, flags=re.M)
text = re.sub(r'^config_fragments\s*=\s*\[.*?\]\s*$',
              'config_fragments = ["soc:configs/sm8250.config"]', text, count=1, flags=re.M)
board.write_text(text)

series = device / 'patches/xnu/series'
series.parent.mkdir(parents=True, exist_ok=True)
series.write_text('arch:../../../patches/xnu/local-availability.patch\n')
print('board.toml: NativeUfs selected, device series with local-availability')

revision = subprocess.check_output(['git', '-C', str(device), 'rev-parse', 'HEAD'], text=True).strip()
catalog = Path('components.toml')
text = catalog.read_text()
if 'id = "NativeUfs"' not in text:
    text += f'''
[[component]]
id = "NativeUfs"
kind = "driver"
function = "Qualcomm UFS host controller probe"
scope = "board"
repository = "https://github.com/stevedang67889/UniXNU-kebab"
revision = "{revision}"
path = "drivers/NativeUfs"
source_files = ["NativeUfs.cpp"]
runtime_identifiers = ["NativeUfs.cpp", "org.blu-sharky.NativeUfs", "NativeUfs"]
build_stage = "kernel"
build = ["patch-nativeufs.py"]
outputs = ["build/native-ufs/libNativeUfs.a"]
'''
    catalog.write_text(text)
print('components.toml: NativeUfs entry')

script = Path('scripts/build-xnu-4k-baseline.sh')
text = script.read_text()
if 'build/sheng-ufs/libShengUfs.a' in text:
    script.write_text(text.replace('build/sheng-ufs/libShengUfs.a', 'build/native-ufs/libNativeUfs.a'))
    print('build-xnu: UFS archive -> build/native-ufs/libNativeUfs.a')

driver = Path('scripts/build-arm-driver.py')
text = driver.read_text()
old = ("if component == 'NativeUfs':\n"
       "    command += ['-I', str(ROOT / 'kits/VerifiedRoot/include'), '-I', str(ROOT / 'drivers/VirtioMmioBlock'),\n"
       "                '-DNATIVE_VERIFIED_BLOCK=1', '-DNATIVE_VERIFIED_BLOCK_MULTI_POLICY=1', '-mgeneral-regs-only']\n")
new = ("if component == 'NativeUfs':\n"
       "    command += ['-I', str(ROOT / 'kits/VerifiedRoot/include'), '-I', str(ROOT / 'drivers/VirtioMmioBlock'),\n"
       "                '-DKEBAB_UFS_PROBE_ONLY=1', '-Wno-unneeded-internal-declaration', '-Wno-unused-function',\n"
       "                '-mgeneral-regs-only']\n")
if old in text:
    driver.write_text(text.replace(old, new))
    print('build-arm-driver: probe-only flags')
else:
    print('build-arm-driver: NativeUfs branch unchanged (already probe-only?)')
PY
