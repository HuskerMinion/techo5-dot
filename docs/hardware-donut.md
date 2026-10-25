# Echo Dot 3rd gen (2018) — `donut`

The Echo Dot 3rd gen is a different machine from the Dot 2: another SoC, another Wi-Fi and Bluetooth
chip, four microphones instead of seven, and a 64-bit kernel. TECHO5 runs on it with the same image
tooling, slot scheme and installer as the Dot 2, and its own build of the daemon (`-tags dot,donut`).
Everything here was read off one unit on Fire OS 6574.1.

## Identity

| Item | Value |
|---|---|
| Model (on the label) | D9N29T (2018). **Not** the 2019 refresh (`crumpet`, C78MP8) or the model with a clock (`doebrite`) |
| Fire OS `ro.product.device` / `ro.product.model` | `donut_puffin` / `AEODN` |
| `/proc/idme/device_type_id` | `A32DOYMUN6DTXA` (what the installer and `update-boot.py` go by) |
| USB ids | BROM `0e8d:0003`, preloader `0e8d:2000`, TWRP `18d1:4ee2`, Fire OS adb `0e8d:201c`, TECHO5's console `1d6b:0104` |

## Board

| Item | Value |
|---|---|
| SoC / RAM | MediaTek MT8516 (device tree model `MT8167B`), 4× Cortex-A35, 512 MB; 4 GB eMMC |
| Kernel | Amazon's **4.4.22, 64-bit**; Fire OS's userspace is 32-bit, and so is TECHO5's (armv7) |
| Speaker amp | TI TAS2770 on I2S. It plays S24_LE and stays silent on S32_LE |
| Headphone jack | the SoC's own codec; jack detection on `ACCDET` |
| Microphones | 4 analog mics on 2× TI TLV320ADC3101, captured over TDM (card 0, device 1, S32 with 24 bits) |
| Echo reference | the playback loopback, `DL1_AWB_Record` (card 0, device 7) |
| LED ring | ISSI IS31FL3236, 12 RGB LEDs, the same driver as the Dot 2. Segment 0 sits between volume down and mute; the ring runs clockwise |
| Light sensor | `tsl2572` |
| Buttons | volume up 115, volume down 114, action 138; mute 113 |
| Mute | `gpio-privacy`: software can turn mute on but never off. Only the button unmutes |
| Wi-Fi / Bluetooth | MediaTek MT7668 on SDIO, through Amazon's own kernel modules |
| USB | none on the case. D− and D+ are on test pads TM4 and TM5, ground on TM1 (or TM3); power comes from the barrel adapter only |

## Partitions

GPT, A/B, and no `recovery` partition:

kb, dkb, lk_a, tee1, lk_b, tee2, expdb, misc (p8), persist (p9), boot_a / boot_b (p10 / p11),
**swdl (p12, 32 MB)**, system_a / system_b (p13 / p14), cache (p15, 32 MB), userdata (p16, 1.5 GB). The
preloader is on `mmcblk0boot0`.

kaeru, the unlock's bootloader patch, boots `swdl` as recovery, so the unlock kit put TWRP there, and
TECHO5's Linux image goes there in its place. The cache partition is too small for two root filesystem
slots, so the store is on userdata (`/data/techo5-store`, bound at `/store` as on the Dot 2).

## Boot image

The image carries the unit's own stock kernel, taken by the installer from the backup of the boot slot
Fire OS runs in. TWRP's kernel in `swdl` is another build, and Amazon's Wi-Fi and Bluetooth modules
only load into the stock one (they are checked against its version string). The initramfs goes at
`0x43478000`, TWRP's address: the stock header's `0x44000000` leaves less than 4 MB below the kernel's
crash log area at `0x44400000`, and an initramfs this size there never boots.

Every Linux boot asks for the next one to be Linux too, so a power cut comes back to TECHO5.
`to-android --yes` boots Fire OS once more, and it stays until `adb reboot recovery`. `to-twrp` puts
the unit's TWRP back into `swdl`, as on the Dot 2.

## Wi-Fi

Amazon's `wlan_mt76x8_sdio_prealloc.ko`, then `wlan_mt76x8_sdio.ko`, loaded from the unit's own system
partition with the firmware in place first. Neither is published: each unit adopts its own into the
store on the first boot. Never unload the driver: a reload can't claim its interrupt, and a reset job
the unloaded module left behind crashes the kernel.

Alpine's wpa_supplicant 2.11 never completes the 4-way handshake with this driver, on any access point:
the AP drops the station right after message 2 of 4. 2.11 advertises 16 replay counters in its RSN
capabilities whenever the AP has WMM, with no option to turn it off. The driver writes its own RSN
element, with capabilities 0, into the association request, so the two don't match. wpa_supplicant 2.10
and Fire OS's own 2.6 send 0 and join. TECHO5 ships Alpine's 2.11 with that one change
(`tools/linux/wpa-patches/0100-donut-no-wmm-replay-counters.patch`, built by
`tools/linux/build-wpa-supplicant.sh`).

## Audio

- **Speaker:** DL1 at 96 kHz, 16-bit, to the TAS2770 amplifier alone (the jack's back end off). At
  48 kHz the amplifier turns the high frequencies of voices and music into static (#20). The ring
  buffer is 6 periods of 2048 frames (48 KB, 128 ms). A ring of 36 KB or less goes into the audio
  block's SRAM, and clearing that memory the way ALSA does crashes the kernel. The DL1 front end needs
  a routed back end before it opens.
- **Jack:** DL1 at 48 kHz, S24_LE, with both back ends on and the amplifier muted. The jack codec
  refuses 96 kHz, so echod closes DL1 and opens it again in the other format when the jack is plugged
  in or out.
- **Microphones:** TDM capture, four channels; the echo reference is the DL1 loopback, which follows
  either format.

## Bluetooth

`btmtksdio.ko` (Amazon's) gives a raw H4 device, `/dev/stpbt`. The stock kernel has no Bluetooth stack
at all, so TECHO5 builds one as modules for it: `bluetooth.ko`, `hci_vhci.ko` and `cmac.ko`
(`tools/linux/build-bt-modules.sh`). The kernel checks only a module's version string, so modules built
from source of the same version load into it.

Amazon hasn't published the Dot 3's kernel source. The Echo Input's (Echo_Input_src-6.5.4.7) is the
same 4.4.22 MT8516 tree. It lacks the Bluetooth sources, which come unchanged from mainline 4.4.22,
listed in `tools/linux/bt-modules/added-from-4.4.22.txt`. Each release carries the complete source the
modules were built from (`techo5-donut-bt-modules-source.tar.xz`).

On top, the same stack as the Dot 2: `btbridge` puts `/dev/stpbt` behind `hci0`, with BlueZ and
bluez-alsa. The daemon's Home Assistant Bluetooth proxy runs on `hci0` alongside them. The boot script
waits for `hci0` before the daemon starts: `/dev/stpbt` accepts a second opener, and a proxy that
opened it before the bridge did would split the controller's replies with it.

## Unlock

With the kamakiri-donut v1.0.0 kit ([XDA thread](https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-dot-3rd-gen-2018-donut.4801400/)):

- The bootrom needs the **action (dot) button held** while power is applied. Without it the Dot goes
  straight to the preloader.
- Start kamakiri with the Dot powered off.
- After the unlock: TWRP in `swdl`, stock 6574.1 in both slots, then `boot-root.zip` for root adb.

## Tested

On one unit, from Fire OS 6574.1 through `tools/install-dot.py`:
- voice with Home Assistant, all four microphones, the wake word
- the speaker, and the headphone jack (switching back when it's unplugged)
- the buttons, mute and its light, the light ring
- Wi-Fi from boot, and the Home Assistant Bluetooth proxy
- Bluetooth audio from a phone, with the pairing kept across a reboot
- an update into the spare slot, committed once healthy
- an update to dot-v1.0.31, the first release with the Dot 3, from Home Assistant's update card

Not yet: the rescue environment after five failed boots, `update-boot.py`, installing on a unit with no saved Wi-Fi
network, and a check that the firewall rules load on the stock kernel.
