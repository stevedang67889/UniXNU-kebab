#!/bin/bash
# UFS kernel build recipe for the macOS UniXNU workflow.
#
# Paste this as the workflow's `pre_build` input:
#
#   bash device/scripts/ufs-ci-pre-build.sh
#
# It runs in the UniXNU checkout (the framework), with the device repository
# checked out at $UNIXNU_DEVICE (device/).  It wires the local Qualcomm UFS
# driver into the kernel build, which upstream does not know about:
#   - components.toml gets a NativeUfs entry (the catalog is read-only upstream)
#   - build-xnu-4k-baseline.sh links build/sheng-ufs/libNativeUfs.a
#     (upstream hard-codes the sheng archive name)
#   - the device board selects the component and enables CONFIG_XNU_NATIVE_UFS
#   - kernel_args get native-ufs-root/native-ufs-diagnostics
set -euo pipefail
DEVICE=${UNIXNU_DEVICE:-$PWD/device}

echo "== UFS pre-build: device at $DEVICE"

# 1. driver source into the kernel build tree
rm -rf drivers/NativeUfs
cp -a "$DEVICE/drivers/NativeUfs" drivers/NativeUfs

# 2. component catalog entry (upstream components.toml stays untouched otherwise)
python3 - "$DEVICE" <<'PY'
import subprocess
import sys
from pathlib import Path

device = Path(sys.argv[1])
revision = subprocess.check_output(['git', '-C', str(device), 'rev-parse', 'HEAD'], text=True).strip()
url = subprocess.check_output(['git', '-C', str(device), 'remote', 'get-url', 'origin'], text=True).strip()
catalog = Path('components.toml')
text = catalog.read_text()
if 'id = "NativeUfs"' not in text:
    text += f'''
[[component]]
id = "NativeUfs"
kind = "driver"
function = "Qualcomm UFS host controller and root block device"
scope = "arm64"
repository = "{url}"
revision = "{revision}"
path = "drivers/NativeUfs"
source_files = ["NativeUfs.cpp"]
runtime_identifiers = ["NativeUfs.cpp", "org.blu-sharky.NativeUfs", "NativeUfs"]
build_stage = "kernel"
build = ["build-arm-driver.py", "NativeUfs"]
outputs = ["build/sheng-ufs/libNativeUfs.a"]
'''
    catalog.write_text(text)
print('components.toml: NativeUfs entry present')
PY

# 3. kernel script: link the archive this component actually builds
sed -i 's#\$ROOT/build/sheng-ufs/libShengUfs\.a#$ROOT/build/sheng-ufs/libNativeUfs.a#' scripts/build-xnu-4k-baseline.sh
grep -n 'UFS_ARCHIVE=' scripts/build-xnu-4k-baseline.sh

# 4. device repository: component selection, defconfig, root boot arguments
python3 - "$DEVICE" <<'PY'
import sys
from pathlib import Path

device = Path(sys.argv[1])
board = device / 'board.toml'
text = board.read_text()
if '"NativeUfs"' not in text:
    text = text.replace(
        'include = ["ArmDTPlatform", "ArmCpu", "SoftwareAES", "XnuPrelink", "DarwinBoot"]',
        'include = ["ArmDTPlatform", "ArmCpu", "SoftwareAES", "XnuPrelink", "DarwinBoot", "NativeUfs"]')
if 'native-ufs-root=1' not in text:
    text = text.replace('kernel_args = [\n  "-v",',
                        'kernel_args = [\n  "-v",\n  "native-ufs-root=1",\n  "native-ufs-diagnostics=1",')
board.write_text(text)

config = device / 'configs/kebab_defconfig'
text = config.read_text().replace('CONFIG_XNU_NATIVE_UFS=n', 'CONFIG_XNU_NATIVE_UFS=y')
config.write_text(text)
print('board.toml: NativeUfs selected, native-ufs-root=1; defconfig: CONFIG_XNU_NATIVE_UFS=y')
PY
