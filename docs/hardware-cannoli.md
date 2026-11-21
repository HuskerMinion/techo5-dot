# Echo Dot 5th gen (2022) — `cannoli`

The Echo Dot 5th gen is a new machine for TECHO5: a MediaTek MT8519 with NAND under UBI, two boot slots,
and no eMMC. Fire OS stays in slot A, rooted by firebreak, and TECHO5 Linux runs from slot B when
Volume-Up is held at power-on. The daemon has its own build (`-tags dot,cannoli`). Everything here was
read off one unit on Fire OS 6.5.7.5 (NS6575/8349).

The hardware was explored in the open, in TECHO5's discussion
[#610, "Echo Dot 5th gen, 2022 (cannoli)"](https://github.com/HuskerMinion/techo5/discussions/610):
the board, the boot chain, the firmware generations, and what each step of the port found. Read it
before changing anything pinned below.

Installing: [install-cannoli.md](install-cannoli.md). Building the slot-B pieces:
[building.md](building.md).

## Identity

| Item | Value |
|---|---|
| Model (on the label) | C2N6L4 (2022) |
| Fire OS `ro.product.device` | `cannoli` |
| Firmware TECHO5 is pinned to | Fire OS 6.5.7.5, **NS6575/8349** (the newest build on [FTVDB](https://ftvdb.com/echo/firmware/com.amazon.cannoli.android.os/)) |
| USB ids | Fire OS adb `1949:*` (Amazon), TWRP `18d1:4ee2`, TECHO5's console `1d6b:0104` ("Echo Dot 5 Linux") |

## Board

| Item | Value |
|---|---|
| SoC / storage | MediaTek MT8519, NAND with UBI: 17 volumes, mostly in `_a`/`_b` pairs, with `userdata` and `cache` among them |
| Kernel | Amazon's 5.4.86 (`-ab0013323246848`), 32-bit ARM, `MODVERSIONS` on and no module signing |
| Speaker amp | Maxim MAX98396 at i2c 3-0038, on eTDM2 at a fixed 96 kHz. Plays from `DL2 Playback` (card 0, device 1) |
| Microphones | 3 mics on a TI TLV320ADC5140 (i2c 4-004c) over eTDM1, captured on `UL9` (card 0, device 9) without the HiFi5 DSP. ch0–2 are the mics, ch3 is unwired. Which hole is which channel is not known yet |
| Echo reference | the playback looped into the same capture: `O30 I40` / `O31 I41` put DL2 on UL9's channels 4 and 5 |
| LED ring | ISSI IS31FL3246 (i2c 0-0031), 12 RGB LEDs as `ledmod_0..11`. `ledmod_0` sits over the power socket; the numbers run clockwise from above |
| Light sensor | `tsl2540` (IIO, raw counts; lux from the unit's idme calibration) |
| Buttons / mute | CPLD `amzn_cpld` at i2c 4-0008: volume up 115, volume down 114, action 138; mute 113 and the privacy switch 14 |
| Wi-Fi / Bluetooth | MediaTek MT7663 on SDIO, through the unit's own Amazon modules. Bluetooth is `btmtksdio`'s raw `/dev/stpbt`, put behind `hci0` by `btbridge` |
| USB | none on the case. D+, D− and GND are on the 3×3 test pads under the rubber foot (firebreak's [cannoli guide](https://github.com/Gamer92000/firebreak/blob/main/docs/cannoli.md) shows them); power comes from the 15 V adapter |

On a slot-B boot U-Boot leaves the amplifier, the microphone converter and the LED driver disabled in the
device tree. `cannoli_hwfixup.ko` creates their devices from their nodes.

## Why 8349, and what isn't reversible

- firebreak's unlock (CVE-2026-46728 in U-Boot) is validated on 8349 only, and the installer stops on a
  newer build with nothing written. Keep a unit off Wi-Fi until it is installed.
- The Bluetooth modules and `cannoli_hwfixup.ko` are built against 8349's kernel. Every CRC they import is
  checked against the stock `boot.img`'s (`tools/linux/cannoli-kernel.sh`).
- An older unit, including the 2022 launch firmware, is brought up to 8349 with `firebreak cannoli-upgrade`
  and the four `brhgptlk` copies from the same OTA. **That is one-way:** the first 8349 TEE boot burns the
  anti-rollback eFuse.

## What it depends on

TECHO5 doesn't carry any of Amazon's code or firmware for this unit. The pieces come from these places,
and every image that holds Amazon's kernel or drivers is built on your computer for your unit:

| Piece | Where it comes from | What it's used for |
|---|---|---|
| [firebreak](https://github.com/Gamer92000/firebreak) | Gamer92000 | The unlock, the 8349 upgrade (`cannoli-upgrade`) and `restore` of slot A |
| [cannoli-os-builder](https://github.com/Gamer92000/cannoli-os-builder) | Gamer92000 | firebreak's per-unit bundle: rooted Fire OS for slot A, the loaders, the patched U-Boot with the Volume-Up hook. Needs a bundle with `"adb": "keyless"` |
| [twrp_device_amazon_cannoli](https://github.com/Gamer92000/twrp_device_amazon_cannoli) | Gamer92000 | The headless TWRP that firebreak puts in slot B, packed for your serial |
| [echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist) | Gamer92000 | `tools/kcrc.py`, which reads the stock kernel's CRCs; its cannoli notes (BL2, lux, the first exploration) |
| [TECHO5](https://github.com/HuskerMinion/techo5) | HuskerMinion | `echod` (`-tags dot,cannoli`) and `btbridge` |
| Amazon's GPL kernel source | [amazon.com/echonotices](https://amazon.com/echonotices) | The kernel tree the modules build against: `Echo_Dot_5th_Gen_src-6.5.7.1-20251027` (every CRC matches), with the full `6.5.5.5-20221208` drop for the files the newer one lacks |
| The 8349 OTA | [FTVDB's record](https://ftvdb.com/echo/firmware/com.amazon.cannoli.android.os/0bf96b3cd8fca30fdd12f61208e44d76-13323246980-fire-os-6-5-7-5-ns6575-8349-2026-10-27/) (`update-kindle-cannoli-NS6575_user_8349_0013323246980.bin`, MD5 `0bf96b3cd8fca30fdd12f61208e44d76`) | The parts for `cannoli-upgrade` and the `brhgptlk` copies, unpacked with `payload_dump.py` |
| Alpine Linux armv7 | Alpine's mirrors, pinned in `tools/linux/packages-*.txt` | The rescue ramdisk and the root filesystem, BlueZ and bluez-alsa |
