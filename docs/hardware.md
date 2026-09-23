# Echo Dot 2nd gen (2016) — `biscuit`

Most of this file is collected from other people's work: what the daemon's `dot` build (from
EchoLocal), EchoMuse's emOS, the amonet/kaeru/TWRP sources, postmarketOS's archived port and a boot
image unpacked offline already say. Each fact names its source, and anything sourced that way is
*unverified here*. The exception is [First unit (the bench unit)](#first-unit-the-bench-unit-read-2026-09-15)
near the end, which is read off a real Dot on 2026-09-15; entries marked *unconfirmed* stay that way
until a `hwdump` covers them.

Sources, short names used below:

- **EchoLocal**: https://github.com/ygelfand/echolocal (MIT). Fire OS 6 daemon, hardware facts in
  code comments; also the `dot` build of TECHO5's `echod`.
- **EchoMuse / emOS**: https://github.com/wilbowes/EchoMuse (MIT, Wil Bowes). `SETUP.md`,
  `emos/README.md`, `docs/rooting.md`. emOS is a Linux userspace on biscuit with no Amazon
  processes, bench-proven at v0.4 (2026-09).
- **amonet**: https://github.com/R0rt1z2/amonet branch `mt8163-biscuit` (v2.0.0, 2026-09-11),
  with https://github.com/R0rt1z2/kaeru and https://github.com/R0rt1z2/twrp_device_amazon_echo-mt8163.
- **pmOS**: https://gitlab.postmarketos.org/postmarketOS/pmaports/-/tree/main/device/archived/device-amazon-biscuit
  (archived 2026-06-22).
- **boot.img**: EchoLocal's shipped `internal/host/assets/boot.img`, unpacked offline; its embedded
  kernel config is [dumps/biscuit-fireos6-echolocal-boot-3.18.19.config](dumps/biscuit-fireos6-echolocal-boot-3.18.19.config).

## Board

| Item | Value | Source |
|---|---|---|
| SoC | MediaTek MT8163, 4× Cortex-A53 | boot.img DT, EchoMuse |
| RAM | **512 MB** (DT memory node `0x20000000`), half the Show 5's | boot.img DT |
| Storage | 4 GB eMMC (Samsung `FJ25AB` reported) | teardowns, EchoMuse; unconfirmed |
| Wi-Fi / BT | SoC CONSYS (`CONFIG_MTK_COMBO_CHIP="CONSYS_8163"`) + MT6625L RF, BTIF host interface; WMT/STP, BT and gen2 wlan drivers **built into the kernel** | boot.img config, eevblog teardown |
| Mic ADCs | 4× TI **TLV320ADC3101** (stereo), I²C 0 at `0x18`–`0x1b`, one shared TDM bus | EchoMuse (PCB traces), config `SND_SOC_TLV320AIC3101=y` |
| Playback | TI **TLV320DAC3203** (`ti,tlv320aic32x4`, I²C 2 `0x18`), card 0 device 23, 48 kHz; speaker amp part unknown | boot.img DT |
| LED ring | ISSI **IS31FL3236** at I²C 0 `0x3f`, 36 PWM channels = 12 RGB segments; sysfs `frame`, `boot_animation`. DT also lists 4× LP55231 (other board revisions?) | boot.img DT, EchoLocal `led` |
| Light sensor | tsl2584tsv (`0x29`, IIO) or tsl2540 (`0x39`); units vary | boot.img DT |
| Buttons | evdev codes 113 mute, 114 vol−, 115 vol+, 138 action; gpio-keys vol± on GPIO 50/37, `mtk-kpd`, ACCDET jack | EchoLocal `codes_dot.go`, DT |
| Mute | `amz_privacy` under `/sys/devices/soc/10010000.keypad/`, MTK pin 87; Fire OS 5 leaves the lines unclaimed (sysfs GPIO) and Fire OS 6 binds them to the keypad driver | EchoLocal `privacy` |
| Headphone jack | `/sys/class/switch/h2w/state` | EchoLocal `speaker` |
| PMIC | MT6323 | boot.img DT |
| Display | none | — |

## Firmware lines and kernels

Two Fire OS lines exist and they boot **different kernels**. That decides the architecture of
everything in the image.

| | Fire OS 5 | Fire OS 6 |
|---|---|---|
| Kernel | 3.18.19, **AArch64** (`bootopt=…,64N2`) | 3.18.19, **32-bit ARM** (`32N2`, zImage at `0x40008000`) |
| emOS status | working (its init is aarch64) | init path built, untested on hardware |
| EchoLocal | — | its target |
| After amonet v2.0.0 | **no longer boots** | boots |

Kernel config highlights (Fire OS 6 image, same on pmOS's build except devtmpfs):

- **No devtmpfs** (`# CONFIG_DEVTMPFS is not set`); pmOS's own build turned it on. Without it,
  every node has to be `mknod`'d from numbers read off a running device (emOS does exactly that).
- **No `CONFIG_BT`**, same as cronos: Bluetooth is raw H4 on `/dev/stpbt` only. EchoLocal's BLE
  proxy talks H4 to it directly. BlueZ would need the kernel rebuild TECHO5 did (`CONFIG_BT` +
  `hci_vhci`, and `btbridge`).
- No overlayfs, no squashfs, no namespaces, no VT. ext4, cfg80211, `I2C_CHARDEV`, `SPIDEV`,
  `GPIO_SYSFS`, zram, `RD_GZIP`/`RD_XZ`, `IKCONFIG` present.
- USB gadget is the **Android gadget** (`CONFIG_USB_G_ANDROID`), not configfs as on cronos.
  MUSB must be switched to device mode (`/sys/devices/platform/mt_usb/cmode` = 1) before `f_acm`
  gives `/dev/ttyGS0` (emOS).
- `CONFIG_MTK_RAM_CONSOLE`: the previous boot's kernel log survives a warm reset as
  `/proc/last_kmsg`. No pstore.
- An `amzn_mt_spi` FPGA driver (iCE40UL1K) is compiled in with an enabled DT node, as on the
  Show. But EchoMuse traces the mics straight onto the TDM bus, so whether biscuit uses the FPGA
  path at all is **open**.

GPL source: Fire OS 6.5.5.9 kernel with `arch/arm/configs/biscuit_defconfig` at
https://gitlab.com/echo-pmos/amazon-biscuit-kernel. Amazon's notices page:
https://www.amazon.com/gp/help/customer/display.html?nodeId=G202096090.

### Bluetooth kernel (`3.18.19-bt`) and its security backports

`tools/linux/build-kernel.sh` builds Amazon's 3.18.19 source (Echo_Dot_src-6.5.7.1) with
`kernel-bt.fragment`, taking `net/bluetooth`, `drivers/bluetooth` and `include/net/bluetooth` from
upstream linux-3.18.19. That code has none of the fixes since 2015, and the Dot runs bluetoothd
all the time, so `tools/linux/kernel-patches/` backports the remotely reachable ones (48 patches,
applied in order, the build stops if one does not apply). Each patch header names the upstream
commit, the stable backport it came from (3.18.y, 4.4.y, 4.14.y, 4.19.y or 5.4.y, whichever is
closest), its CVE and any 3.18 adaptation. Adaptations are mechanical: `bt_dev_*()` →
`BT_*()`, `kref_read()` → `atomic_read(&kref.refcount)`, two missing constants
(`L2CAP_PSM_LE_DYN_END`, `SMP_KEY_REJECTED`), `ZERO_KEY`, and different surrounding code. The
config is unchanged. Build of 2026-09-16: `zImage-dtb` 5951508 bytes, sha256
`c2981fc9b8451a29b2d8a38bfd0029c62d78cf65d641bd03640df70e4a098082` (the boot-tested unpatched
build was 5950380 bytes, `fb07faf5…f581a8`). The patched kernel has not been booted yet.

| Area | Fixes |
|---|---|
| L2CAP config parsing | CVE-2017-1000251 (BlueBorne), CVE-2017-1000410, CVE-2019-3459, CVE-2019-3460, CVE-2022-42895, CVE-2022-45934 |
| LE credit-based channels | CVE-2022-42896 (SPSM range), invalid LE DCID/SCID checks, `l2cap_le_credits` lock leak (3.18 returns with the channel locked) |
| L2CAP channel lifetime (use-after-free, races with remote disconnects) | CVE-2022-20566 plus its regression fixes, CVE-2022-3564, CVE-2022-3640, CVE-2022-50386, CVE-2023-40283 + CVE-2025-39860, CVE-2023-53297, CVE-2023-53305, CVE-2023-53827, CVE-2023-54214, CVE-2024-27399; also `6c08fc896b60`/`2a154903cec2`/`20ae4089d0af` (socket kill races), `28261da8a26f` and `02c5ea5246a4` (disconnect response state, lock order), `75767213f3d9` (invalid DCID), `f937b758a188` (fixed channels matched by PSM) |
| A2MP | CVE-2020-12352 (BadChoice) and its follow-up `a5687c644015` |
| HCI event parsing | CVE-2020-36386 and the two other inquiry result length checks, legacy advertising report checks (`a2ec905d1e16`, CVE-2021-47620), duplicate sync connection complete |
| BR/EDR pairing | CVE-2020-26555 (reject our own BD_ADDR, drop NULL link keys) |
| Profiles | CVE-2018-9363 (HIDP report length), CVE-2024-26903 (RFCOMM), SCO disconnect crashes (`2c501cdd6823`, `75e34f5cf69b`, `435c51336976`, `1da5537eccd8`) |
| SMP | `fe4840df0bdf` (unexpected key distribution fails pairing instead of stalling) |

Not backported, and why:

- **KNOB, CVE-2019-9506** (`d5bb334a8e17` + `693cd8ce3f88` + `eca94432934f`): the check compares
  `hcon->enc_key_size`, which 3.18 only fills for LE. Reading it for BR/EDR (HCI Read Encryption
  Key Size after Encryption Change) is later infrastructure; without it every encrypted BR/EDR
  link would be refused. LE is already covered by SMP's own 7-byte minimum. Mitigation otherwise
  depends on the combo chip's firmware.
- **BIAS, CVE-2020-10135** (`3ca44c16b0dc`, `8746f135bb01`): built on the 4.x
  `hci_encrypt_change_evt`/`read_enc_key_size_complete` rework, and the check only rejects E0 on
  Security Level 4 links, which A2DP/AVRCP/HID do not request.
- **CVE-2020-26558 / CVE-2021-0129** (`6d19628f539f`), **CVE-2020-12351** (BadKarma),
  **CVE-2020-24490** (BleedingTooth): the vulnerable code (LE Secure Connections public keys,
  `sk_filter` in L2CAP receive, extended advertising reports) does not exist in 3.18.
- `hci_vhci.c` fixes (open/close races) need access to `/dev/vhci`, which only btbridge (root) has.
- Local-only socket/ioctl fixes, controller-misbehavior hardening (AMP events, zeroed events,
  `HCI_EV_NUM_COMP_PKTS` underflow) and interoperability changes (`1d8e801422d6`,
  `c569242cd492`, connection-parameter checks) were left out.

## Unlock (amonet, R0rt1z2)

**Version matters, and it has to be known before anything is flashed.**

- **v1.1.0** carved two 110 MB `boot_a`/`boot_b` partitions from the end of `userdata`
  (p17/p18) for its payload and renamed the real 16 MB slots to `boot_a_x`/`boot_b_x` (p10/p11).
  Fire OS 5 keeps booting.
- **v2.0.0** (2026-09-10/11, `mt8163-biscuit`): after a BROM or preloader handshake it undoes
  v1's GPT patch, zeroes RPMB, writes an old `tz.img` to `tee2` and `tee-payload.bin` to `tee1`,
  a stock `lk.bin` to `lk_a` and `lk_b`, `biscuit-kaeru.bin` to `expdb`, a downgraded preloader to
  BOOT0 (unless it came in through the preloader exploit), then `FASTBOOT_PLEASE` in `misc` and
  `fastboot flash recovery twrp.img`. **Fire OS 5 no longer boots afterwards.**
- EchoMuse: do **not** try to go back from v2.0.0 by flashing Fire OS 5 or an older amonet; that
  hand-rewrites bootloaders and is how units hard-brick.
- EchoLocal's `bootimg` recognizes both layouts by size: boot slots of 16 MB (unlocked before
  the Fire OS 6 OTA) or 110 MB (after it).

kaeru (the LK payload in `expdb`), from its source:

- fastboot `flash:`/`erase:` wrapped. `lk`, preloader, `tee1/2` and `lk_a/b` are refused unless
  you run `flashing unlock_critical`. Also `set_active:`, `oem print-bcb`, `oem idme`, `oem mem`,
  `oem partitions`.
- Keys at power-on: **Vol-Up → recovery, Vol-Down → fastboot**. v1: hold mute for TWRP.
- It reads `bootopt` from the boot image and forces the kernel to 32- or 64-bit to match.
- `fastboot boot` is **not** added, and there's no sign stock LK keeps it. Assume, as on cronos,
  that every test image has to be flashed.

TWRP (`twrp_device_amazon_echo-mt8163/biscuit`): ARM zImage-dtb, A/B, headless (adb only). While
in recovery it swaps `lk_a/b`, `tee1/2` and `preloader` for dummy "decoy" nodes so an OTA zip
can't overwrite them.

Brick recovery: `brick.sh`/`fastbrick*` corrupt the preloader header to fall into BROM USB
download mode, then `bootrom-step.sh`. EchoMuse: if both A/B slots exhaust their boot tries, the
way back is opening the case and shorting a test point (which one is not written down).

## Partitions

Known names: `misc` (p8), `boot_a_x` (p10), `boot_b_x` (p11), `system_a` (p13), `system_b` (p14),
`cache` (p15), `userdata` (p16), plus `lk_a`, `lk_b`, `tee1`, `tee2`, `expdb`, `recovery`,
`persist`, and on v1 layouts `boot_a`/`boot_b` (p17/p18, 110 MB). By-name path:
`/dev/block/platform/bootdevice/by-name/`. **Full size table: open**, the first `hwdump` fills it.

## Audio

Capture, card 0 device 24 (`pcmC0D24c`): the only format accepted is **16 kHz, S24_3LE, 9 channels**
(EchoLocal, EchoMuse).

| ch | EchoMuse (tone injection at each hole, 2026-05) | EchoLocal (`beam.go`) |
|---|---|---|
| 0–5 | perimeter mics MK1–MK6 at 330°, 30°, 90°, 150°, 210°, 270° (clock face) | ring, ch 0 at 108°, 60° apart |
| 6 | center mic MK7 | center mic |
| 7, 8 | playback loopback L, R | loopback L, R |

The two angle columns use different zero references; they agree on the 60° spacing and the
center mic. The ring radius is **36 mm** (PCB measurement), so it's 72 mm across.

ADC probe order sets the channel order: `0x18` → ch 0/1, `0x19` → 2/3, `0x1a` → 4/5, `0x1b` → 6/7.
That is eight ADC inputs for nine channels; how the FPGA/driver fills the ninth is open.

Loopback (EchoMuse `aec_map.sh`, 2026-08-29):

- It is the digital playback stream itself (RMS matched to 5 significant figures), **bit-exact
  zero** in silence, stereo (L on ch 7, R on ch 8).
- It is taken **before** the volume control, so an adaptive filter has to learn the gain.
- Mic lag behind it is **+33 samples (2.06 ms), polarity inverted**; provisional at correlation −0.31.
- It does not show DAC clipping: at the top of the range the echo is mostly harmonics absent from
  the reference, so the volume cap protects the canceller too.
- `Audio_ExtCodec_EchoRef_Switch` does not gate it; it is unconditional.

Mixer (EchoLocal `gain.go`, `paths_dot.go`):

- Mic: `ADC_{A..D} MICPGA Volume Ctrl` (0.5 dB steps; declared max 80, real to 119), inputs
  routed through `ADC_x Left/Right Ip Select ADC_x DIF1_L/R switch`. EchoMuse runs digital 88 /
  PGA 40 on all four ADCs (Amazon's init values).
- Speaker path: `HPL/HPR Output Mixer L/R_DAC Switch`, `Audio_DacMux_Setting`,
  `Right Channel Only`, `HP Driver Gain Volume` (6 speaker / 11 headphone), a 7-block
  `biquad coefficients` EQ blob (the vendor's speaker tuning), `Ext_Speaker_Amp_Switch`.
- Playback ring 1024 × 4. Unlike cronos, the Dot's DL1 SRAM ring **works** (no hold node needed).
- Vendor volume curves (31 steps, dB per step) for speaker and headphone are in `paths_dot.go`.

Without Android (emOS):

- Both directions clock with no HAL. But **nothing opens the codec's DAPM routes**, so mics and
  speaker are powered down until userspace sets the mixer. That's the same trap as the bare cronos
  boot.
- The speaker amp idles on and **hisses**. Gating it clicks audibly, so emOS feeds a silence stream
  instead.

Amazon's processing is software in `amazon.speech.sim` reading the raw 9 channels, with tuning in
`/vendor/etc/audio-algorithms/` (`AFE.cfg`, `coefs_FBF.cfg`, `coefs_FBFV2_LowLatency_8beams.cfg`,
filter banks). There's no hardware beamformed channel. See [microphones.md](microphones.md).

## Wi-Fi without Fire OS (emOS)

- Fire OS 6's `wmt_loader` exits 255 outside Android, `wmt_launcher` sits silent, and its
  `wpa_supplicant`/`dhcpcd` abort on `open /dev/binder`.
- emOS brings the combo chip up itself: `SET_PATCH_NAME` and `SET_STP_MODE` ioctls on
  `/dev/stpwmt` (HIF `(fm << 4) | stp`; biscuit is BTIF, `0x23`), then a loop answers the driver's
  `srh_patch` requests with `SET_PATCH_NUM` / `SET_PATCH_INFO`.
  - The patch order runs **backwards** through the sorted names (`ROMv2_lm_patch_1_0` is
    sequence 2).
  - The address is `{0, 0, hdr[0x1A], hdr[0x1B]}` of the 28-byte header.
  - These values were read off Amazon's launcher under an `LD_PRELOAD` shim. The code is MIT, in
    `emos/init`.
- The firmware loader searches `/etc/firmware`. Without the blobs there, the chip "powers on",
  reports success and never creates `wlan0`.
- Static hostap **2.10**, nl80211 (WEXT scans but never associates), plus busybox `udhcpc` with a
  lease script. Compare TECHO5 on cronos: 2.11 fails and 2.9 works on MT7668. The supplicant
  version is a per-chip question.
- No SAE, so no WPA3. PMF is possible (BIP-CMAC-128 advertised).
- Device nodes: `/dev/stpwmt` 190:0, `/dev/stpbt` 192:0, `/dev/wmtWifi` 153:0,
  `/dev/wmtdetect` 154:0. Firmware: `WMT_SOC.cfg`, `WIFI_RAM_CODE`, `ROMv2_lm_patch*`.
- Cold boot to network: about 32 s.
- Wi-Fi and Bluetooth share one antenna. EchoLocal keeps BLE scan windows small, and TECHO5 found
  an idle A2DP stream starving Wi-Fi on the same kind of shared path.

## Other things emOS learned the hard way

- PID 1 should be a **static binary**. A busybox-shell init produced no output at all, which looks
  exactly like a kernel that never started.
- Bionic binaries resolve DNS through Android's property service, so they have no DNS.
  Go's own resolver is fine, and so is musl from an Alpine rootfs.
- Reboot to recovery is one `reboot(RESTART2, "recovery")` syscall.
- The clock starts at 2010 with no RTC sync. emOS has the controller push the time; the TECHO5
  image uses NTP by IP or the gateway.

## First unit (the bench unit), read 2026-09-15

Read-only over adb; nothing written.

- Fire OS **6574.1** (`NS65741/8138`, Fire OS 6.0), running slot **`_b`**.
- Kernel `3.18.19-gecb8cb46060-dirty #1 SMP PREEMPT Mon Aug 31 21:29:05 UTC 2026`, `armv7l`,
  `bootopt=64S3,32N2,32N2`.
- adbd runs as root, SELinux permissive. `MemTotal` 489 068 kB, zram 150 MB.
- **EchoLocal installed and running**:
  - `/system/bin/ledcontroller` → `/system/app/echod/echod` (18.7 MB, 2026-09-14).
  - State in `/data/misc/echolocal` (its Home Assistant name).
  - Its log reports "vendor beamformer available tuning=FilterBank_768cvxGLow + FBFV2 8 beams,
    mics=4" and the light sensor at `iio:device0/illuminance0_input`.
  - Do not run `echod --version` by hand: it starts a second daemon (it did, briefly, and exited
    on SIGPIPE).
- Boot slots, compared offline:
  - `boot_b` is **byte-identical to EchoLocal's `boot.img`** (userdebug, `androidboot.selinux=permissive`).
  - `boot_a` is a stock-looking Fire OS 6 `user` image, the fallback.
- `expdb` carries an LK with fastboot strings (the kaeru payload location under amonet v2). `lk_a`
  and `lk_b` are identical.
- **Partition table** (eMMC 3 817 472 KiB):

  | p | name | KiB |
  |---|---|---|
  | boot0 | preloader | 4 096 |
  | 1, 2 | kb, dkb | 1 024 each |
  | 3 | lk_a | 1 024 |
  | 4 | tee1 (tee_a) | 5 120 |
  | 5 | lk_b | 1 024 |
  | 6 | tee2 (tee_b) | 5 120 |
  | 7 | expdb | 10 240 |
  | 8 | misc | 512.5 (1025 sectors) |
  | 9 | persist | 16 384 |
  | 10, 11 | boot_a, boot_b | 16 384 each |
  | 12 | recovery | 16 384 |
  | 13, 14 | system_a, system_b | 786 432 each |
  | 15 | cache | 802 816 |
  | 16 | userdata | 1 294 319 |

  No `boot_a_x` names and no p17/p18: not the amonet v1 layout.
- **`/proc/idme` has seven mic calibrations**, one per capsule (`miccal.0`–`6`; per-unit values,
  not recorded here). On the bench unit, if they are linear gains, they spread by about 2.5 dB,
  worth correcting before any mix. Units and meaning unverified.
- ALSA: one card `mt-snd-card`; `pcmC0D24c` capture and `pcmC0D23p` playback present.
- Backups of preloader, kb, dkb, lk_a/b, tee1/2, expdb, misc, persist, boot_a/b and recovery
  are in `D:\platform-tools\echodot\<serial>\`, each md5-verified against the device
  (`SUMS.txt`). Not yet backed up: system_a/b, cache, userdata.

## Open questions for the first unit

1. Which Fire OS line and which amonet version is it on (kernel 32 or 64-bit; `boot_a_x` or 110 MB
   `boot_a`)? Keep a raw copy of every boot, lk, tee and expdb partition before any change.
2. The full partition table with sizes, and `/proc/idme` (mic calibration?).
3. Is the FPGA SPI path live, or is capture pure TDM? Where does the ninth channel come from?
4. Does `pcmC0D24c` read all nine channels distinct once the DAPM routes are opened, with no
   downmix property as on cronos?
5. The speaker amp part, and whether its hiss can be removed without the click.
6. The action button's input device.
