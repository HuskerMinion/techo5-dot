# Echo Input (2018) — `cupcake`

> **Experimental: not yet tried on a unit.** Everything below is from the Echo Dot 3rd gen's port
> ([hardware-donut.md](hardware-donut.md)), from Amazon's GPL kernel source for the Echo Input
> (Echo_Input_src-6.5.4.7, which the Dot 3's Bluetooth modules are already built from), and from
> [echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist)'s notes on the Echo Input's firmware
> (Gamer92000, MIT: `docs/re-cupcake.md` and `devices/cupcake/` there), where it runs on Fire OS.
> [What to check on the first unit](#what-to-check-on-the-first-unit) lists what none of those settle.

The Echo Input is the Echo Dot 3rd gen's board without a speaker. TECHO5 runs it on the Dot 3's
release as it is: the same installer, boot image, root filesystem and daemon build (`-tags dot,donut`),
on the unit's own stock kernel. The installer and the daemon tell the two apart by the device type id.

## Identity

| Item | Value |
|---|---|
| Model (on the label) | C1125P (2018) |
| Fire OS `ro.product.device` / `ro.product.model` | `cupcake` (no `_puffin` suffix) / `AEOCK` |
| `/proc/idme/device_type_id` | `A1JJ0KFC4ZPNJ3`, Amazon's device type for the Echo Input (what the installer, `update-boot.py`, the boot scripts and the daemon go by). **To be confirmed on a unit** |
| Firmware echo-dot-assist worked from | Fire OS 6.5.7.5 (`NS6575/8338`) |
| USB | the micro-USB socket carries data and power. TECHO5's console is `1d6b:0104`, "Echo Input Linux" |

If idme says something else, the installer stops before writing anything ("says cupcake in its build
props but device type id … in idme"): please open an issue with the id.

## Board: what it shares with the Echo Dot 3rd gen

| Item | Echo Input | Same as the Dot 3? |
|---|---|---|
| SoC | MediaTek MT8516 (`fstab.mt8167`, `init.mt8167.rc`), 32-bit userspace on a 64-bit 4.4.22 kernel | yes |
| Partitions | A/B, kaeru boots `swdl` as recovery and the unlock kit puts TWRP there (the same TWRP device tree, `echo-mt8167`) | expected: the installer checks |
| Microphones | 4, on two TI TLV320ADC3101 at i2c 0-0018 and 0-001a, 16 kHz (Fire OS's `AFE.cfg`) | yes |
| Wi-Fi / Bluetooth | MT7668 on SDIO: `wlan_mt76x8_sdio.ko` (its own build of the Dot 3's gen4m driver) and `btmtksdio.ko` (`/dev/stpbt`) | same chip and layout; another build of the driver |
| Kernel modules | vermagic `4.4.22+ SMP preempt mod_unload aarch64`, as the Dot 3's: the release's Bluetooth modules load | yes |
| Mute | `gpio-privacy`, the state in `/sys/devices/platform/gpio-privacy/state`: software can mute, only the button unmutes. The latch's GPIO differs by board revision, inside the device tree | the same files |
| Light sensor | TSL2572 at 0-0039 | yes |
| Thermal zone | `mtktscpu` | yes |

## Board: what differs

| Item | Echo Input | What TECHO5 does |
|---|---|---|
| Speaker | **none**, and no amplifier in the device tree. Sound comes from the MT8167 codec's 3.5 mm line out, or Bluetooth | The line out stands where the speaker is in the daemon: one output, no Audio output choice, no Speaker gain, no jack detection. The path is the Dot 3's jack path at 48 kHz, S24_LE, with the TAS2770's controls written only where they exist, and both of the codec's output muxes (HPOUT and LINEOUT) open |
| Light | **one RGB LED**, a TI LP5562 at i2c 0-0030, as three LED class devices (`/sys/bus/i2c/devices/0-0030/leds/{R,G,B}`) | The ring's twelve segments are drawn as on a Dot and mixed into one color: each channel at its brightest segment, scaled to that channel's `max_brightness`. What spins on a ring holds still |
| Buttons | action (`gpio-keys`, `KEY_HELP` 138) and mute (`gpio-privacy`, `KEY_MUTE` 113); **no volume keys** | Only action and mute are listed in Home Assistant. Holding the action button for 5 s still starts Bluetooth pairing. The action + volume down factory reset isn't available |
| Volume | — | Home Assistant, the setup page, or a phone over Bluetooth. The line out takes the Dot 3 jack's volume curve |
| Wi-Fi preallocation module | not known whether Fire OS ships `wlan_mt76x8_sdio_prealloc.ko` here | The boot scripts load it first where it's there, and the main driver alone otherwise |

## Partitions and boot

The same as the Dot 3 ([hardware-donut.md](hardware-donut.md#partitions)): the initramfs and the boot
script reach `misc` (p8), `swdl` (p12), `system_a` / `system_b` (p13 / p14) and `userdata` (p16) by
number, since the initramfs has no by-name links. Before it writes anything on a Dot 3 or an Echo Input,
the installer resolves each of those by-name links on the unit and stops if one is anywhere else. That
check is new with the Echo Input, and runs on the Dot 3 too.

The image carries the unit's own stock kernel, from the boot slot Fire OS runs in, and the initramfs is
loaded at the Dot 3's address (`0x43478000`).

## Unlock

With the kamakiri-cupcake kit, **by hand, as
[its XDA thread](https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-input-2018-cupcake.4797603/)
says** (R0rt1z2 asks that the unlock is never run from an installer or a script). It works over the
micro-USB socket, with no opening and no soldering. Stop once TWRP is running in `swdl` with stock
Fire OS in both system slots; the installer works from TWRP, so root in Fire OS isn't needed.

Don't let the Echo Input go online with the Alexa app on the way: it would update itself, and an update
can close the hole the unlock uses.

The PC's USB port powers the Echo Input until the install is done (USB 2 gives 0.5 A, less than its own
adapter). If it restarts or drops off USB, put a powered USB hub in between.

## What to check on the first unit

- [ ] `/proc/idme/device_type_id` is `A1JJ0KFC4ZPNJ3`.
- [ ] The installer's partition check passes (misc p8, swdl p12, system p13/p14, userdata p16).
- [ ] Wi-Fi comes up: which modules Fire OS has in `/system/lib/modules`, and whether the firmware is
      `WIFI_RAM_CODE_MT7668.bin` as on the Dot 3. The Dot 3's patched wpa_supplicant completes the
      handshake.
- [ ] The line out plays, and on which of the codec's outputs (`HPOUT Mux` or `LINEOUT Mux`): then
      the other can be left closed. Whether `HP Ext Amp Switch` or `LINEOUT Ext Amp Switch` needs to be
      on. `tinymix` or `amixer -c 0 contents` on the unit lists the controls.
- [ ] The four microphones capture (`TDM_Capture`, hw:0,1) and the echo reference (`DL1_AWB_Record`,
      hw:0,7) follows the line out.
- [ ] The LED: `ls /sys/bus/i2c/devices/0-0030/leds/` shows `R`, `G` and `B`, and the states show.
- [ ] The action and mute buttons; mute's latch.
- [ ] Bluetooth: `/dev/stpbt`, `hci0`, a phone playing to it, the Home Assistant Bluetooth proxy.
- [ ] An update from Home Assistant's update card.
