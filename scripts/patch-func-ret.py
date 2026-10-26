"""Patch one function in a thin arm64 Mach-O kernel to return immediately.

Used to skip a function that hangs before the kernel has a console. The patch
writes a single `ret` at the symbol's first instruction.

Usage: patch-func-ret.py <kernel.macho> <symbol> [--output <file>]
"""

import argparse
import struct
import subprocess
from pathlib import Path

LC_SEGMENT_64 = 0x19
RET = struct.pack("<I", 0xD65F03C0)


def symbol_address(nm_output, symbol):
    for line in nm_output.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == symbol:
            return int(parts[0], 16)
    return None


def file_offset(blob, address):
    magic, = struct.unpack_from("<I", blob, 0)
    if magic != 0xFEEDFACF:
        raise SystemExit("not a thin 64-bit Mach-O")
    _, _, _, _, ncmds, _, _, _ = struct.unpack_from("<IiiIIIII", blob, 0)
    offset = 32
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", blob, offset)
        if cmd == LC_SEGMENT_64:
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", blob, offset + 24)
            if vmsize and vmaddr <= address < vmaddr + vmsize:
                delta = address - vmaddr
                if delta >= filesize:
                    raise SystemExit("symbol is in a zero-fill part of the segment")
                return fileoff + delta
        offset += cmdsize
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kernel")
    parser.add_argument("symbol")
    parser.add_argument("--output")
    args = parser.parse_args()

    kernel = Path(args.kernel)
    blob = bytearray(kernel.read_bytes())
    nm = subprocess.run(["llvm-nm", str(kernel)], capture_output=True, text=True).stdout
    address = symbol_address(nm, args.symbol)
    if address is None:
        raise SystemExit(f"symbol not found: {args.symbol}")
    offset = file_offset(blob, address)
    if offset is None:
        raise SystemExit("symbol is not inside a file-backed segment")

    original = bytes(blob[offset:offset + 4])
    blob[offset:offset + 4] = RET
    out = Path(args.output) if args.output else kernel
    out.write_bytes(blob)
    print(f"{args.symbol} at {address:#x} (file {offset:#x}): {original.hex()} -> d65f03c0")


if __name__ == "__main__":
    main()
