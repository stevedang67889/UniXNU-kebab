#!/usr/bin/env python3
"""Generate the kebab (OnePlus 8T / SM8250) Apple-format device tree.

The structure mirrors the proven sheng and QEMU entry fixtures: the same
required nodes and the same properties the source XNU boot converged on.
The hardware values are kebab's own, taken from this device's firmware:

  - GICv3 distributor 0x17A00000/0x10000, redistributor 0x17A60000/0x100000
    with a 0x20000 stride, both inside an APSS window at 0x17000000.
    Source: the MADT (APIC.aml) of the kebab ACPI tables in Mu-Silicium.
  - CPU timebase 19200000 Hz (the Qualcomm architectural counter; the board
    firmware programs the same value).
  - DRAM base 0x80000000 (kebab MemoryMapLib).
  - UFS host controller 0x01D84000, measured on this device.

This is first-boot bring-up data. The platform has no matching platform
expert yet, so the kernel is expected to reach the console and report the
genuine missing-driver panic on screen. No property is invented beyond the
sources named above.
"""
from pathlib import Path
import argparse
import os
import struct
import base64
import json
import zlib

ROOT = Path(__file__).resolve().parents[1]

DRAM_BASE = 0x80000000
ARMIO_BASE = 0x17000000          # APSS peripheral block containing the GIC
ARMIO_SIZE = 0x08000000
GIC_DIST = 0x17A00000
GIC_DIST_SIZE = 0x10000
GIC_REDIST = 0x17A60000
GIC_REDIST_SIZE = 0x100000
GIC_REDIST_STRIDE = 0x20000      # 8 cores x 0x20000 == the 0x100000 range
TIMEBASE = 19200000
UFS_HC = 0x01D84000

# SM8250 ("kona"): four Cortex-A55 little cores then four Cortex-A77 big cores,
# numbered 0x000..0x700 in Affinity1.  The MADT lists eight GICC entries with
# ACPI uids 0..7 and the PPTT agrees.
LITTLE = range(0x000, 0x400, 0x100)
BIG = range(0x400, 0x800, 0x100)


def node(name, properties=(), children=()):
    properties = [('name', name.encode() + b'\0'), *properties]
    result = struct.pack('<II', len(properties), len(children))
    for key, data in properties:
        result += struct.pack('<32sI', key.encode(), len(data))
        result += data + bytes(-len(data) % 4)
    return result + b''.join(children)


def load_product_node(path):
    """The real D22AP /product node, converted from `ipsw dtree` output.

    syscfg placeholders and AAPL,phandle were filtered when the JSON was
    generated; values carry their ADT type so this only encodes them.
    """
    import base64 as _b64

    def enc(prop):
        kind, value = prop['t'], prop['v']
        if kind == 'str':
            return value.encode() + b'\0'
        if kind == 'u32':
            return struct.pack('<I', value)
        if kind == 'u64':
            return struct.pack('<Q', value)
        if kind == 'data':
            return _b64.b64decode(value)
        raise SystemExit(f'unknown product property type {kind!r}')

    def build(entry):
        return node(entry['name'],
                    [(prop['k'], enc(prop)) for prop in entry['props']],
                    [build(child) for child in entry.get('children', [])])

    data = json.loads(Path(path).read_text())
    return ([(prop['k'], enc(prop)) for prop in data['props']],
            [build(child) for child in data.get('children', [])])


def empty_nvram_proxy():
    """A fresh volatile CHRP bank for upstream IODTNVRAM, with no controller."""
    def header(name, size):
        raw = bytearray(struct.pack('<BBH12s', 0x70, 0, size // 16, name))
        checksum = sum(raw)
        while checksum > 255:
            checksum = (checksum & 255) + (checksum >> 8)
        raw[1] = checksum
        return raw

    image = bytearray(8192)
    image[:16] = header(b'nvram', 32)
    image[32:48] = header(b'common', 16 + 2048)
    free_at = 32 + 16 + 2048
    image[free_at:free_at + 16] = header(b'free', len(image) - free_at)
    struct.pack_into('<I', image, 16, zlib.adler32(image[20:]))
    return [('nvram-bank-size', struct.pack('<I', len(image))),
            ('nvram-bank-count', struct.pack('<I', 1)),
            ('nvram-current-bank', struct.pack('<I', 0)),
            ('nvram-proxy-data', bytes(image))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'build/kebab/boot/kernel.macho.adt')
    parser.add_argument('--dram-size', type=lambda value: int(value, 0), required=True,
                        help='Actual observed DRAM size, e.g. 0x300000000 for 12 GiB')
    parser.add_argument('--owned-memory-size', type=lambda value: int(value, 0), default=None,
                        help='Explicit DarwinBoot contiguous window request, 256 MiB..8 GiB in 2 MiB steps')
    parser.add_argument('--root-match-volume-uuid', default=None,
                        help='/chosen/root-matching: select the root by the UUID of an IOMedia')
    parser.add_argument('--unique-chip-id', type=lambda value: int(value, 16), default=None,
                        help='/chosen/unique-chip-id (8 bytes) and --chip-id (4 bytes) seed XNU IOPlatformUUID')
    parser.add_argument('--chip-id', type=lambda value: int(value, 16), default=None)
    parser.add_argument('--iphone-identity', action='store_true',
                        help='/product and /buttons describe an iPhone-class device: phone idiom, '
                             'iPhone X (iPhone10,3 / D22AP) identity strings')
    parser.add_argument('--ipad-identity', action='store_true',
                        help='/product and /buttons describe an iPad-class device without a Home button; '
                             'the prelinked kernelcache is an iPad7,1 one, so the identity matches it')
    parser.add_argument('--cpu-regs', default=','.join(hex(reg) for reg in (*LITTLE, *BIG)),
                        help='Comma-separated CPU MPIDR affinity values; the first is the boot CPU')
    parser.add_argument('--psci-method', choices=['smc', 'hvc'], default='smc',
                        help='/psci method used by the secondary-CPU start (the board firmware uses smc)')
    parser.add_argument('--timebase', type=lambda value: int(value, 0), default=TIMEBASE,
                        help='CNTFRQ value in Hz; override with the value read from the device')
    parser.add_argument('--packed-data-ramdisk', action='store_true',
                        help='Hand off validated DBRDISK1 bytes to the native sparse block driver')
    parser.add_argument('--data-ramdisk', action='store_true',
                        help='/chosen/memory-map RAMDisk placeholder for a scratch Data seed')
    parser.add_argument('--trust-cache', action='store_true',
                        help='Zeroed TrustCache sidecar handoff placeholder')
    parser.add_argument('--product-file', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'configs/d22ap-product.json',
                        help='real D22AP /product node as JSON (from ipsw dtree)')
    args = parser.parse_args()

    if (args.unique_chip_id is None) != (args.chip_id is None):
        parser.error('--unique-chip-id and --chip-id must be supplied together')
    if args.unique_chip_id is not None and not (0 < args.unique_chip_id < 1 << 64 and 0 < args.chip_id < 1 << 32):
        parser.error('--unique-chip-id must fit 8 bytes and --chip-id 4 bytes, both nonzero')
    if args.owned_memory_size is not None and (
            not 0x10000000 <= args.owned_memory_size <= 0x200000000
            or args.owned_memory_size % 0x200000):
        parser.error('Owned memory must be 256 MiB to 8 GiB, aligned to 2 MiB')

    try:
        cpu_regs = [int(value, 0) for value in args.cpu_regs.split(',')]
    except ValueError:
        parser.error('--cpu-regs must be integers')
    if (not cpu_regs or cpu_regs[0] != 0 or len(set(cpu_regs)) != len(cpu_regs) or len(cpu_regs) > 8
            or any(reg < 0 or reg > 0xffff or reg & 0xff for reg in cpu_regs)):
        parser.error('--cpu-regs: up to eight distinct Affinity1-numbered cores, boot CPU 0 first')

    def cpu_compatible(reg):
        return b'arm,cortex-a77\0' if reg >= 0x400 else b'arm,cortex-a55\0'

    cpus = [node('cpu%d' % index, [('state', b'running\0' if index == 0 else b'waiting\0'),
                                   ('reg', struct.pack('<I', reg)),
                                   ('device_type', b'cpu\0'),
                                   ('compatible', cpu_compatible(reg)),
                                   ('timebase-frequency', struct.pack('<I', args.timebase))])
            for index, reg in enumerate(cpu_regs)]

    if args.iphone_identity and args.product_file and args.product_file.is_file():

        _pp, _pc = load_product_node(args.product_file)

        product_node = node('product', _pp + [('model', b'oneplus,kebab\0')], _pc)

    else:

        product_node = node('product', [('product-name', b'OnePlus 8T\0'),

                                            ('model', b'oneplus,kebab\0')])


    tree = node('device-tree', [
        ('model', b'iPhone10,3\0' if args.iphone_identity else (b'iPad7,1\0' if args.ipad_identity else b'OnePlus 8T\0')),
        *([('target-type', b'D22AP\0')] if args.iphone_identity else ([('target-type', b'J120\0')] if args.ipad_identity else [])),
        # The compiled board DT's own root strings.
        ('compatible', b'oneplus,kebab\0qcom,sm8250\0qcom,kona\0'),
        ('#address-cells', struct.pack('<I', 2)),
        ('#size-cells', struct.pack('<I', 2)),
    ], [
        node('chosen', [('random-seed', os.urandom(64)),
                        *([('unique-chip-id', struct.pack('<Q', args.unique_chip_id)),
                           ('chip-id', struct.pack('<I', args.chip_id))]
                          if args.unique_chip_id is not None else []),
                        ('osenvironment', b'main\0'),
                        *([('certificate-production-status', struct.pack('<I', 0)),
                           ('effective-production-status-ap', struct.pack('<I', 0)),
                           ('certificate-security-mode', struct.pack('<I', 1)),
                           ('effective-security-mode-sep', struct.pack('<I', 1))]
                          if args.ipad_identity else []),
                        *([('ephemeral-storage', struct.pack('<I', 1))]
                          if (args.data_ramdisk or args.packed_data_ramdisk) else []),
                        *([('darwinboot-memory-size', struct.pack('<Q', args.owned_memory_size))]
                          if args.owned_memory_size else []),
                        ('dram-base', struct.pack('<Q', DRAM_BASE)),
                        ('dram-size', struct.pack('<Q', args.dram_size)),
                        ('crypto-hash-method', b'sha2-384\0'),
                        *([('root-matching', ('<dict><key>IOProviderClass</key><string>IOMedia</string>'
                                              '<key>IOPropertyMatch</key><dict><key>UUID</key><string>' +
                                              args.root_match_volume_uuid.upper() + '</string></dict></dict>\0').encode())]
                          if args.root_match_volume_uuid else []),
                        ('amfi-only-platform-code', struct.pack('<I', 1)),
                        *empty_nvram_proxy()],
             [node('memory-map', ([("PackedRAMDisk" if args.packed_data_ramdisk else "RAMDisk", bytes(16))]
                                  if (args.data_ramdisk or args.packed_data_ramdisk) else []) +
                                 ([('TrustCache', bytes(16))] if args.trust_cache else [])),
              node('manifest-properties')]),
        node('cpus', children=cpus),
        *([node('psci', [('compatible', b'arm,psci-1.0\0'),
                         ('method', args.psci_method.encode() + b'\0')])] if len(cpus) > 1 else []),
        node('defaults', [('sleep-disabled', struct.pack('<I', 1))]),
        node('options', []),
        *([node('buttons', [('compatible', b'buttons\0'), ('device_type', b'buttons\0'),
                            ('button-names', b'hold\0voldown\0volup\0'),
                            ('home-button-type', struct.pack('<I', 2))])] if args.ipad_identity
                           else ([node('buttons', [('compatible', b'buttons\0'), ('device_type', b'buttons\0'),
                                                   ('button-names', b'hold\0voldown\0volup\0')])]
                                 if args.iphone_identity else [])),
        product_node,
        node('arm-io', [('device_type', b'arm-io\0'),
                        ('#address-cells', struct.pack('<I', 2)),
                        ('#size-cells', struct.pack('<I', 2)),
                        ('ranges', struct.pack('<QQQ', 0, ARMIO_BASE, ARMIO_SIZE))],
             [node('intc', [('compatible', b'arm,gic-v3\0'),
                            ('reg', struct.pack('<4Q',
                                                GIC_DIST - ARMIO_BASE, GIC_DIST_SIZE,
                                                GIC_REDIST - ARMIO_BASE, GIC_REDIST_SIZE)),
                            ('redistributor-stride', struct.pack('<Q', GIC_REDIST_STRIDE))]),
              # UFS (0x01D84000) lies far below the arm-io window, so a child reg
              # offset would be negative. The node exists only for class matching;
              # the driver takes its physical windows from board constants.
              node('ufshc', [('compatible', b'qcom,sm8250-ufshc\0qcom,kona-ufshc\0')])]),
    ])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(tree)
    print(f'kebab ADT: {len(tree)} bytes, dram {args.dram_size:#x}, cpu {len(cpus)}, '
          f'timebase {args.timebase}, gic {GIC_DIST:#x}/{GIC_REDIST:#x}')


if __name__ == '__main__':
    main()
