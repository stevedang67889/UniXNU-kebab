#!/bin/bash
# UFS for the macOS UniXNU workflow (component-based tree).
#
# Workflow pre_build input:
#
#   bash device/scripts/ufs-ci-pre-build.sh
#
# The framework already ships the ShengUfs component (repository UniXNU/ShengUfs,
# pinned revision, output build/sheng-ufs/libShengUfs.a) and the kernel script
# links it when XNU_NATIVE_UFS=1.  Only the device repository has to select it.
set -euo pipefail
DEVICE=${UNIXNU_DEVICE:-$PWD/device}

python3 - "$DEVICE" <<'PY'
import sys
from pathlib import Path

device = Path(sys.argv[1])
board = device / 'board.toml'
text = board.read_text()
if '"ShengUfs"' not in text:
    needle = 'include = ["ArmDTPlatform", "ArmCpu", "SoftwareAES", "XnuPrelink", "DarwinBoot"]'
    if needle not in text:
        raise SystemExit('board.toml [components] include changed; update the pre-build')
    text = text.replace(needle, needle[:-1] + ', "ShengUfs"]')
if 'native-ufs-diagnostics=1' not in text:
    text = text.replace('kernel_args = [\n  "-v",',
                        'kernel_args = [\n  "-v",\n  "native-ufs-diagnostics=1",')
board.write_text(text)
print('board.toml: ShengUfs selected, native-ufs-diagnostics=1')

config = device / 'configs/kebab_defconfig'
text = config.read_text().replace('CONFIG_XNU_NATIVE_UFS=n', 'CONFIG_XNU_NATIVE_UFS=y')
config.write_text(text)
print('defconfig: CONFIG_XNU_NATIVE_UFS=y')
PY
