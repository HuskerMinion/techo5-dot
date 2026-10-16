# Echo 2nd gen (2017) — `radar`

The Echo (2nd Generation) is a biscuit (the Echo Dot 2, [hardware.md](hardware.md)) in a bigger
cabinet: the same SoC, partition table and boot chain, a woofer and tweeter behind an external amp, and
no USB port on the outside. **TECHO5 Dot does not support it yet.** This page collects what the work
toward that has found.

Read off the first unit on 2026-10-16 (TWRP, read only) unless a line names another source. Per-unit
values (serials, MAC addresses, calibrations) are not recorded here.

## Identity

| Item | Value |
|---|---|
| Fire OS `ro.product.device` / `ro.product.name` | `radar_puffin` |
| Fire OS `ro.product.model` | `AEORD` |
| `/proc/idme/device_type_id` | `A7WXQPH584YP` (biscuit: `A3S5BH2HU6VAYF`); NUL-terminated |
| fastboot `getvar product` | `RADAR` |
| Fire OS on the unit | 6.5.6.4 (NS6564/5403) in slot b, 6.5.6.3 in slot a |
| LK / preloader | `63cb91b-20221007_073612` / `642551b-20211203_021230` |
| Stock fastboot USB id | `0bb4:0c01` |
| idme fields | include seven mic calibrations (`miccal.0`–`6`), as on biscuit |

## Board

| Item | Value |
|---|---|
| SoC / RAM | MediaTek MT8163, 512 MB; the same `mt8163.dtsi` as biscuit (GPL source) |
| Playback codec | TI TLV320AIC3204 (`tlv320aic32x4`), I²C 2 `0x18`, card 0 device 23 |
| Speaker amp gate | the codec's MFP2 pin, mixer control `MFP Gpio Mute` (On = amp off). The kernel boots it muted and never toggles it, so the speaker is silent until userspace sets it Off. The control is in the shared codec driver; reported needed on radar by EchoLocal PR #64 and EchoMuse PR #687. Not yet read on the unit |
| Ext-amp DAC mux | GPIO124, polarity inverted against biscuit (`radar.dts`, `radar_evt.dts`) |
| Mic ADCs | 4× TLV320ADC3101 (`tlv320aic3101`), I²C 0 `0x18`–`0x1b`, card 0 device 24 |
| LED ring | IS31FL3236, I²C 0 `0x3f`. The DT also binds 4× LP55231 (`0x32`–`0x35`) and an `lp855x-led` at `0x2c` |
| Light sensor | `tsl2584tsv` at I²C 0 `0x29` and `tsl2540` at `0x39`, both bound |
| Other I²C | `sym827-regulator` (I²C 1 `0x60`), `bq24297` charger (I²C 2 `0x6b`) |
| Inputs | `ACCDET` (jack), `mtk-kpd` (mute, action), `keys` (gpio-keys, volume) |
| Thermal zones | `mtktswmt`, `mtktscpu`, `mtkts1`, `mtkts3`–`5`, `mtktspmic`, `tmp103` |
| Wi-Fi / BT | CONSYS + MT6625L as on biscuit; the same firmware names (`WIFI_RAM_CODE_8163`, `WMT_SOC.cfg`, `ROMv2_lm_patch_1_0_hdr.bin`, `ROMv2_lm_patch_1_1_hdr.bin`) under `/system/vendor/firmware` |
| USB | **none outside.** D+, D- and GND pads on the board inside; power is a barrel jack |

## Audio files on the system partition

- `/system/etc/audio_device.xml`: the codec paths Fire OS sets per output. Its `ext_speaker_output`
  path carries the speaker's `biquad coefficients`, different from biscuit's. With them all zero the
  speaker is silent. `/system/vendor/etc/audio_device.xml` is a different, older file without them.
  `boot.sh` adopts the first into the store as `/store/techo5/audio_device.xml`, once, the same way
  as the firmware: it is Amazon's, so it is never in a published image.
- `/system/vendor/etc/audio-algorithms/`: beamformer and filter-bank files (`AFE.cfg`, `asp.cfg`,
  `coefs_FBFV2_*`, `coefs_FilterBank_*`; not all named as on biscuit), plus the speaker tuning radar
  adds: `EQ_<n>.cfg` (one per volume step), `ParametricEQ.cfg`, `MBCL.cfg` and
  their VOIP variants, `UserEQ.cfg`.

## Partitions

The same 16 partitions as biscuit, with the same sizes, `userdata` included (see
[hardware.md](hardware.md#first-unit-the-bench-unit-read-2026-09-15)): misc p8, boot_a/b p10/p11,
recovery p12, system_a/b p13/p14, cache p15, userdata p16.

## Kernel

Fire OS 6 runs a 32-bit 3.18.19 kernel, `bootopt=64S3,32N2,32N2`, with three device trees appended:
`radar`, `radar_evt` and `radar_min_evt`. The stock kernel carries its config (`IKCONFIG`):
[dumps/radar-fireos6-boot-3.18.19.config](dumps/radar-fireos6-boot-3.18.19.config), from the unit's
`boot_b`. amonet's TWRP kernel reports the same config. Against biscuit's it has `CONFIG_THERMAL_RADAR`
in place of `CONFIG_BISCUIT`/`CONFIG_THERMAL_BISCUIT`, no `SPI_SPIDEV`, no `PM_AUTOSLEEP`, more debugging
(`MAGIC_SYSRQ`, `MTK_AEE_FEATURE`, `MTPROF`, UART printk console) and `PANIC_TIMEOUT=5`.

Its stock zImage leaves the end offset at `0x2c` unset (`0xffffffff`); the device trees simply follow
it.

`tools/linux/build-kernel.sh --board radar` builds Amazon's Echo_v2_src-6.5.7.1 with that config, the
same `kernel-bt.fragment` and the same Bluetooth backports as biscuit's kernel (all 48 apply to radar's
tree unchanged). The appended device trees come out byte for byte the same as the stock kernel's and
TWRP's. That kernel boots on a unit with Bluetooth (`/dev/stpbt`, a phone paired and played
through it), Wi-Fi, the speaker and the headphone jack all working. Releases after dot-v1.0.18 carry it
as `techo5-dot-kernel-bt-radar.zImage-dtb`, and the installer and `update-boot.py` take it for a radar;
with an older release a radar keeps its own kernel.

## Unlock and USB

- Unlocked with amonet-radar v1.0.0 (R0rt1z2, branch `mt8163-radar`) `fastbrick`, from stock Fire OS
  6. The result is the same chain as biscuit's: kaeru in `expdb`, TWRP in `recovery` (TWRP 3.7.0,
  `omni_radar`), which swaps `lk`, `tee` and `preloader` for decoys while it runs.
- Volume-Up at power-on boots TWRP.
- USB is only on the internal pads, so every adb, fastboot and USB-console step means opening the
  case. A three-pin pogo dock dropped out under TWRP (port resets, descriptor failures); wires soldered
  to the pads were steady.

## Sources

- Amazon's GPL kernel source, Echo_v2_src-6.5.7.1-20251024 (the `radar` board files and defconfig).
- EchoLocal PR #64 (vithurshanselvarajah) and EchoMuse PR #687 (wilbowes, sascha-hemi): radar ports,
  open at the time of writing; the `MFP Gpio Mute` and speaker EQ findings are theirs.
- amonet `mt8163-radar` and R0rt1z2's TWRP device tree.
