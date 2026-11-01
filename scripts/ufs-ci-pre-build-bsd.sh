#!/bin/bash
# UFS pre-build for the 407db1a framework (branch old-fw), BSD-layer driver.
#
# The driver is a normal source file in bsd/dev and is reached from
# IOFindBSDRoot through a small rd=ufsroot0 hook.  It does not use IOKit
# IOBlockStorageDevice or VerifiedRoot, so the old framework's ABI is enough.
set -euo pipefail
DEVICE=${UNIXNU_DEVICE:-$PWD/device}

python3 - "$DEVICE" <<'PY'
import re
import sys
from pathlib import Path

device = Path(sys.argv[1])
board = device / 'board.toml'
text = board.read_text()

# Drop the old probe-only NativeUfs component if a branch carried it in.
text = re.sub(r',\s*"NativeUfs"', '', text)
text = re.sub(r'"NativeUfs"\s*,\s*', '', text)
text = re.sub(r'^patch_series\s*=\s*\[.*?\]\s*$',
              'patch_series = ["device:patches/xnu/series"]', text, count=1, flags=re.M)
text = re.sub(r'^config_fragments\s*=\s*\[.*?\]\s*$',
              'config_fragments = ["soc:configs/sm8250.config"]', text, count=1, flags=re.M)

# The archive pass runs before the kernel pass, so the corecrypto archive
# builder has no link.filelist yet. The defconfig's source corecrypto path
# builds it in-tree; drop the archive entries.
text = re.sub(r'^\s*"build-corecrypto-14\.py[^"]*",\n', '', text, flags=re.M)
board.write_text(text)
print('board.toml: BSD UFS series, corecrypto archives dropped')

series = device / 'patches/xnu/series'
series.parent.mkdir(parents=True, exist_ok=True)
series.write_text('arch:../../../patches/xnu/local-availability.patch\n'
                  'arch:../../../patches/xnu/disabled-deferred-reclaim.patch\n'
                  'arch:../../../patches/xnu/non-apple-pmap-field.patch\n'
                  'arch:../../../patches/xnu/public-entitlements-header.patch\n'
                  'arch:../../../patches/xnu/published-freezer-condition.patch\n'
                  '10063-kern-exec-entitlements.patch\n'
                  'kebab-ufs-10063.patch\n')
print('device series: compile fixes + BSD kebab_ufs')

defconfig = device / 'configs/kebab_defconfig'
text = defconfig.read_text()
text = text.replace('CONFIG_XNU_NATIVE_UFS=y', 'CONFIG_XNU_NATIVE_UFS=n')
defconfig.write_text(text)
print('kebab_defconfig: NativeUfs disabled')
PY
