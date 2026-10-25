# Echo 2nd gen (2017) — `radar`

The Echo (2nd Generation) is a biscuit (the Echo Dot 2, [hardware.md](hardware.md)) in a bigger
cabinet: the same SoC, partition table and boot chain, a woofer and tweeter behind an external amp, and
no USB port on the outside. **Status: supported, tested on one unit so far.** Install steps are in
the [README](../README.md#echo-2nd-gen). This page collects what has been found on it.

The tables are read off the first unit on 2026-10-16 (TWRP, read only) unless a line names another
source. Per-unit values (serials, MAC addresses, calibrations) are not recorded here.

## On TECHO5 Dot

Tested on one unit, 2026-10-16, on the radar kernel below:

- Speaker: plays, through the unit's own speaker EQ (`audio_device.xml`, below).
- Headphone jack: plays, and output switches between jack and speaker on every plug and unplug. The
  jack is mono.
- Voice with Home Assistant.
- Bluetooth: a phone pairs and plays through it.
- Wi-Fi.
- Microphones: 9-channel capture, the 7 mics plus the loopback.
- Buttons: key codes 115, 114, 138 and 113, the same as the Dot's.
- LED ring: segment 0 sits between mute and volume down, and the ring runs clockwise, the same as the
  Dot's.
- Light sensor: the `tsl2540` is found.

Not yet: the vendor's speaker tuning files (`audio-algorithms/`, below) are not used. It plays
without them.

## Identity

| Item | Value |
|---|---|
| Model (on the label) | XC56PY |
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
| Speaker amp gate | the codec's MFP2 pin, mixer control `MFP Gpio Mute` (On = amp muted). The kernel boots it On and never toggles it, so the speaker is silent until userspace sets it Off. The control is in the shared codec driver; reported needed on radar by EchoLocal PR #64 and EchoMuse PR #687. Read and toggled on the unit: booted On, silent; Off, the speaker plays |
| Ext-amp DAC mux | GPIO124, polarity inverted against biscuit (`radar.dts`, `radar_evt.dts`) |
| Mic ADCs | 4× TLV320ADC3101 (`tlv320aic3101`), I²C 0 `0x18`–`0x1b`, card 0 device 24 |
| LED ring | IS31FL3236, I²C 0 `0x3f`. The DT also binds 4× LP55231 (`0x32`–`0x35`) and an `lp855x-led` at `0x2c` |
| Light sensor | `tsl2584tsv` at I²C 0 `0x29` and `tsl2540` at `0x39`, both bound |
| Other I²C | `sym827-regulator` (I²C 1 `0x60`), `bq24297` charger (I²C 2 `0x6b`) |
| Inputs | `ACCDET` (jack), `mtk-kpd` (mute, action), `keys` (gpio-keys, volume) |
| Thermal zones | `mtktswmt`, `mtktscpu`, `mtkts1`, `mtkts3`–`5`, `mtktspmic`, `tmp103` |
| Wi-Fi / BT | CONSYS + MT6625L as on biscuit; the same firmware names (`WIFI_RAM_CODE_8163`, `WMT_SOC.cfg`, `ROMv2_lm_patch_1_0_hdr.bin`, `ROMv2_lm_patch_1_1_hdr.bin`) under `/system/vendor/firmware` |
| USB | **none outside.** D+, D- and GND pads on the amplifier board inside (TP15, TP14, TP13); power is a barrel jack |

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

### Linux 6.1 (optional)

[LibreEcho](https://github.com/aslater3/LibreEcho-Linux)'s Linux 6.1 kernel for the MT8163 runs the
Echo 2nd gen too. `tools/linux/build-kernel-61.sh` builds it at a pinned commit with its
`mt8163_arm32_defconfig` and `tools/linux/kernel-61-radar.fragment` (what TECHO5 needs on top: 32 MMC
minors so the partition numbers match, eventfd and friends for Go, the firewall's netfilter matches,
`hci_vhci`, `uhid`, zram). Releases carry it as `techo5-dot-kernel-61-radar.zImage-dtb`. It's opt-in:

```
python3 tools/install-dot.py --linux-61                                  # a new install
python3 tools/update-boot.py --serial <serial> --address <address> --linux-61   # a unit on TECHO5 now
python3 tools/update-boot.py --serial <serial> --address <address> --linux-318  # back to 3.18
```

A unit on 6.1 stays on 6.1 when `update-boot.py` runs again without either switch. It boots from the
recovery partition like the 3.18 kernel, packed the same way (the unit's own recovery header, no MediaTek
header on the kernel).

What changes for the daemon: the board comes from the device tree's `idme` node (6.1 has no
`/proc/idme`); the mute is LibreEcho's `amz_privacy` driver (`privacy_state` for the button,
`mute_lamp` for a mute set from Home Assistant; a mute set by the button is released only by the
button); the light ring has no global current setting; and the kernel applies the speaker's
crossover itself, so the 3.18 filter controls are skipped. The Wi-Fi driver reads its firmware from
`/lib/firmware`, which the initramfs and `boot.sh` link from the vendor partition.

Tested on one unit, 2026-10-25: Wi-Fi, Home Assistant, the wake word and answers, the speaker, the
light ring, the buttons and the mute (button and Home Assistant). Not yet tested on 6.1: Bluetooth,
the headphone jack, AirPlay 2. The Echo Dot 2nd gen doesn't run on this kernel yet: it needs its own
device tree.

## Unlock and USB

- Unlocked with amonet-radar v1.0.0 (R0rt1z2, branch `mt8163-radar`) `fastbrick`, from stock Fire OS
  6. The result is the same chain as biscuit's: kaeru in `expdb`, TWRP in `recovery` (TWRP 3.7.0,
  `omni_radar`), which swaps `lk`, `tee` and `preloader` for decoys while it runs.
- Volume-Up at power-on boots TWRP (until TECHO5 is installed; then it boots TECHO5, which sits in
  `recovery`).
- USB is only on the internal pads, so every adb, fastboot and USB-console step means opening the
  case. A three-pin pogo dock dropped out under TWRP (port resets, descriptor failures); wires soldered
  to the pads were steady.

## Sources

- Amazon's GPL kernel source, Echo_v2_src-6.5.7.1-20251024 (the `radar` board files and defconfig).
- EchoLocal PR #64 (vithurshanselvarajah) and EchoMuse PR #687 (wilbowes, sascha-hemi): radar ports,
  open at the time of writing; the `MFP Gpio Mute` and speaker EQ findings are theirs.
- amonet `mt8163-radar` and R0rt1z2's TWRP device tree.
