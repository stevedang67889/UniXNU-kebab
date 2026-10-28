# Checkpoint log in RAM

The kernel stops without a console, so the only signal used to be a timed reset:
the patched kernel waits six seconds after a probe point and reboots, and a
reboot means the point was reached. That answers "did we get here" and nothing
else, and it costs a flashing cycle per question.

This log keeps the answer in memory instead. A probe writes one record before it
continues, the panel shows the records on the next boot, and a single flash can
answer many questions at once.

## Where the ring lives

| Item | Value |
| --- | --- |
| Base | `0xB9000000` |
| Size | 8 MiB |
| Format version | 1 |

The address is inside the first DRAM bank (`0x80000000 + 0x39900000`) but outside
the kernel window (`0x200000000`, 4 GiB) and above the firmware volume
(`0x9FC00000`). A warm reset keeps DRAM, which is what makes the hand-over work.

## Record layout

Header, 16 bytes:

| Offset | Field | Meaning |
| --- | --- | --- |
| `0x00` | magic | `0x474C424B`, the ASCII bytes `KBLG` |
| `0x04` | version | 1 |
| `0x08` | count | records written, saturating |
| `0x0C` | dropped | records skipped because the ring was full |

Records start at `0x10`, 32 bytes each:

| Offset | Field | Meaning |
| --- | --- | --- |
| `0x00` | sequence | equals the index, so a gap shows a lost write |
| `0x08` | checkpoint | identifier of the probe point |
| `0x10` | value | a register or argument the probe records |
| `0x18` | counter | `CNTPCT_EL0`, 19.2 MHz on this SoC |

Capacity is `(8 MiB - 16) / 32 = 262143` records.

## Reader

`KebabBootLog` (a DXE driver in the firmware) runs on every boot with
`Depex TRUE`, before the payload is loaded. It prints the header and the records
with `DEBUG()`, which the panel already renders, then clears the magic so a later
boot reports nothing instead of stale entries. When the magic is absent it holds
the panel for five seconds with a banner, which makes the state readable on a
cold boot.

Verified on the device: a cold boot shows
`BOOTLOG base=b9000000 magic=ffffffff version=0 count=0 dropped=0`, i.e. the
address is readable and no records exist yet.

## Writer

Not implemented yet. The probe stubs are generated as C by the entry-probe tool
and injected at the call site, which is the natural place to append a record: the
stub calls the original function, writes one record with `dc cvac` on the touched
lines, and returns to the caller. Keeping the write ordered matters, because a
cached record is lost on reset.

## Reading a capture

1. Boot the image under test; the kernel writes records as it passes checkpoints.
2. Reboot into fastboot and boot the firmware again.
3. Read the banner and the record list on the panel.

The list is ordered by `sequence`, so the last record shown before the output
stops is the furthest checkpoint the kernel reached.
