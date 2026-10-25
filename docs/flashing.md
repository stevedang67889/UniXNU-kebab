# Flashing kebab

Everything here runs from a normal fastboot session (`fastboot devices` must
print the phone). The bootloader is unlocked (`verifiedbootstate=orange`).

## What can be flashed where

| Image | Partition | Notes |
| --- | --- | --- |
| `boot.img` (BootShim + firmware volume) | `boot_a` | `fastboot boot` is refused by ABL for unsigned images |
| payload FAT image | inside the firmware volume | see `scripts/pack-bootimg.sh`; no partition is used |
| `userdata` image | `userdata` | destroys Android data, do it last |

## Flash the firmware

```sh
fastboot flash boot_a out/ramdisk-boot.img
fastboot reboot
```

Watch the panel: a DEBUG build draws its log into the splash framebuffer that
ABL leaves behind, and the first line of the firmware is
`Project Silicium for OnePlus 8T`.

## Keep a stock boot image to go back

```sh
fastboot flash boot_a stock-boot.img
fastboot flash vbmeta_a stock-vbmeta.img
fastboot reboot
```

`boot_a` is 96 MiB (`0x6000000`). A firmware image with the payload appended is
about 72 MiB, which fits, but check with

```sh
fastboot getvar partition-size:boot_a
```

## If the phone stops answering fastboot

* Hold power for 15 s, then hold **volume up + volume down + power** until
  `FASTBOOT MODE` appears. The bootloader always comes back unless a flash was
  interrupted.
* `fastboot oem edl`, `oem reboot-edl`, `oem enter-edl` and `reboot edl` are all
  rejected by this ABL. EDL is only reachable while Android runs:
  `adb reboot edl`.
* With a rooted Android, the partitions that explain a failed boot can be read
  without EDL:

  ```sh
  adb shell su -c 'dd if=/dev/block/by-name/abl_log of=/sdcard/abl_log.img'
  adb pull /sdcard/abl_log.img
  ```

  `abl_log` holds the bootloader's own log, including
  `Could not get splash memory region node` and the AVB messages.

## Debug without UART

The framebuffer is enough: build the firmware as `DEBUG`, flash it, and read the
panel. To keep a frame on screen, halt after printing or paint a colour marker.
A UART console exists at `0xA90000` (GPIO34/35, 115200 8N1) but needs a 1.8 V
adapter.

## Restore Android

The KB2000 9008 package restores the phone completely: `adb reboot edl` from a
running Android, then the MSM download tool with the package. After a restore the
bootloader is unlocked again only if it was unlocked before (the unlock
persists).
