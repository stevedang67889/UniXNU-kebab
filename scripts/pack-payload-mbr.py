#!/usr/bin/env python3
"""Wrap the payload FAT image in the MBR disk image the firmware publishes.

KebabRamDisk hands the payload region to PartitionDxe as a 512-byte-block
block device. PartitionDxe only creates a partition child when the image has a
partition table, and FatPkg then mounts the FAT32 partition, so the FAT volume
that `unixnu build payload` produces (a bare superfloppy) is placed at LBA 2048
behind a one-partition DOS MBR.

Usage: pack-payload-mbr.py <payload.img> <output.img>
"""
import struct
import sys
from pathlib import Path

SECTOR = 512
PARTITION_LBA = 2048          # 1 MiB, the alignment the previous images used
PARTITION_TYPE = 0x0C         # FAT32 LBA


def mbr(fat_bytes: int) -> bytes:
    if fat_bytes % SECTOR:
        raise SystemExit(f"payload size {fat_bytes} is not a multiple of {SECTOR}")
    sectors = fat_bytes // SECTOR
    if not 0 < sectors <= 0xFFFFFFFF:
        raise SystemExit("payload does not fit in one MBR partition")
    entry = struct.pack(
        "<B3sB3sII",
        0x00,                       # status: not bootable
        bytes((0x00, 0x02, 0x00)),  # CHS of LBA 2048: head 0, sector 2, cylinder 0
        PARTITION_TYPE,
        bytes((0xFE, 0xFF, 0xFF)),  # CHS of the last sector: beyond the CHS range
        PARTITION_LBA,
        sectors,
    )
    boot = bytearray(SECTOR)
    boot[0x1BE:0x1BE + len(entry)] = entry
    boot[0x1FE:0x200] = b"\x55\xaa"
    return bytes(boot)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} <payload.img> <output.img>")
    source, target = (Path(argument) for argument in sys.argv[1:])
    fat = source.read_bytes()
    prefix = mbr(len(fat)) + bytes(PARTITION_LBA * SECTOR - SECTOR)
    target.write_bytes(prefix + fat)
    print(f"{target}: {len(prefix) + len(fat)} bytes "
          f"(MBR sector 0, FAT32 partition at LBA {PARTITION_LBA}, {len(fat)} bytes)")


if __name__ == "__main__":
    main()
