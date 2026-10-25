#!/bin/sh
# Pack the Mu-Silicium firmware volume into an Android boot image that the
# OnePlus 8T accepts.
#
#   pack-bootimg.sh <uefi-fd> <live-dtb> <output.img> [payload.img]
#
# The kernel is BootShim + the firmware volume. When a payload image is given it
# is appended after the volume: the firmware keeps it as a raw region of the
# device, so KebabRamDisk reads it at FD_BASE + 0x300000 without any storage
# driver. Header v2 / 4096-byte pages, kernel at 0x8000, DTB at 0x1f00000.
#
# Requirements: clang with the AArch64 target, llvm-objcopy, python3, and the
# UniXNU checkout (for Resources/Scripts/mkbootimg.py).
set -eu

if [ "$#" -lt 3 ]; then
    echo "usage: $0 <uefi-fd> <live-dtb> <output.img> [payload.img]" >&2
    exit 2
fi

FD="$1"
DTB="$2"
OUT="$3"
PAYLOAD="${4:-}"

UNIXNU="${UNIXNU:-$(cd "$(dirname "$0")/../.." && pwd)/UniXNU}"
UEFI="${UNIXNU_UEFI:-$(cd "$(dirname "$0")/../.." && pwd)/Mu-Silicium}"
MKBOOTIMG="$UNIXNU/Resources/Scripts/mkbootimg.py"
SHIM="$UEFI/BootShim/BootShim.S"

for file in "$FD" "$DTB" "$MKBOOTIMG" "$SHIM"; do
    [ -f "$file" ] || { echo "missing: $file" >&2; exit 1; }
done

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

FDSIZE=$(wc -c < "$FD")
PAYLOAD_OFFSET=$((0x300000))
KERNEL_SIZE=$((FDSIZE))

if [ -n "$PAYLOAD" ]; then
    [ "$FDSIZE" -le "$PAYLOAD_OFFSET" ] || {
        echo "the firmware volume is larger than the payload offset ($PAYLOAD_OFFSET)" >&2
        exit 1
    }
    PAYLOAD_SIZE=$(wc -c < "$PAYLOAD")
    KERNEL_SIZE=$((PAYLOAD_OFFSET + PAYLOAD_SIZE))
fi

# The shim copies the volume to the base and jumps into it. Its Image Size covers
# the whole payload, so the trailing payload stays untouched in memory.
clang --target=aarch64-none-elf -c \
    -DFD_BASE=0x9fc00000 -DFD_SIZE="$KERNEL_SIZE" -DREQUIRES_KERNEL_HEADER=1 \
    "$SHIM" -o "$WORK/shim.o"
llvm-objcopy -O binary "$WORK/shim.o" "$WORK/shim.bin"

cat "$WORK/shim.bin" "$FD" > "$WORK/kernel.bin"
if [ -n "$PAYLOAD" ]; then
    dd if=/dev/zero bs=1 count=$((PAYLOAD_OFFSET - FDSIZE)) status=none >> "$WORK/kernel.bin"
    cat "$PAYLOAD" >> "$WORK/kernel.bin"
fi

: > "$WORK/ramdisk"

python3 "$MKBOOTIMG" \
    --kernel "$WORK/kernel.bin" --ramdisk "$WORK/ramdisk" --dtb "$DTB" \
    --header_version 2 --pagesize 4096 --base 0 --kernel_offset 0x8000 \
    --ramdisk_offset 0x1000000 --tags_offset 0x100 --dtb_offset 0x1f00000 \
    --cmdline "" --os_version 14.0.0 --os_patch_level 2026-10 \
    -o "$OUT"

echo "boot image: $OUT ($(wc -c < "$OUT") bytes)"
echo "  firmware: $FDSIZE bytes"
[ -n "$PAYLOAD" ] && echo "  payload:  $(wc -c < "$PAYLOAD") bytes at +0x300000"
echo "  dtb:      $DTB"
sha256sum "$OUT" 2>/dev/null || true
