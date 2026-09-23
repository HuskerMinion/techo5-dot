# Porting plan

Target: an unlocked Echo Dot 2 (`biscuit`) boots the TECHO5 Linux image. That means the stock 3.18
kernel, an Alpine armv7 rootfs in trial slots and one daemon (`echod`, `dot` build) that owns the
array, the speaker, the LED ring, the buttons, Wi-Fi/BLE and the ESPHome API. No Fire OS processes
and no controller server in between. As in TECHO5, every milestone leaves a usable device and
recovery is proven before the first flash.

Facts and sources for everything below are in [hardware.md](hardware.md); the microphone work is
in [microphones.md](microphones.md).

## How this relates to TECHO5, EchoLocal and emOS

- **Daemon**: TECHO5's `echod` already builds for the Dot (`-tags dot`). It cross-compiles
  (19.8 MB stripped) and its array tests pass at TECHO5 `994e3f9`. **Decided 2026-09-15: one
  daemon source.** Daemon changes for the Dot (the microphone work included) land in TECHO5's
  `echod` behind the `dot` tag or in shared code. This repository holds the Dot's docs, image
  tooling and installer, and builds the daemon from `../techo5/echod`.
- **Image tooling**: TECHO5's `tools/linux` (initramfs rescue, `slotctl`, `mkrootfs.sh`,
  `deploy-rootfs.sh`) is the template. The Dot differs in kernel (3.18, no configfs gadget, no
  devtmpfs), in Wi-Fi bring-up, in A/B partitions and in half the RAM.
- **emOS** (EchoMuse, MIT) has already booted a Linux userspace with no Amazon processes on this
  hardware. Its device knowledge is the shortcut for M1–M2: the static PID 1, hand-made `/dev`,
  MUSB `cmode`, `f_acm`, the WMT patch-download sequence, hostap 2.10 nl80211, codec DAPM routes
  and amp hiss. Borrow with credit, don't rediscover. The architecture differs: emOS keeps Amazon's
  bionic and tinyalsa from `/system`, and sends wake word, noise suppression and the ESPHome API to
  a Python controller. TECHO5 runs all of that on the device.

## M0 — Know the unit (first time it is plugged in)

Read-only, from TWRP's adb:

- Fire OS line and amonet version, `uname -a`, kernel bitness, `bootopt` of each boot slot.
- Partition table with sizes (`/proc/partitions`, by-name links), `/proc/idme`, `/proc/cmdline`.
- A raw backup of every partition that matters: both boot slots (`boot_a_x`/`boot_b_x` or
  `boot_a`/`boot_b`), `recovery`, `lk_a`, `lk_b`, `tee1`, `tee2`, `expdb`, `misc`, and the running
  `system` slot. Kept off the device, with hashes.
- `tools/hwdump.sh` adapted for biscuit (ALSA cards and controls, input devices, I²C, GPIO, sysfs
  LED/privacy nodes, `/dev` majors and minors for the hand-made `/dev`).
- **Prove recovery before anything is written**: restore a boot slot from backup in TWRP.

If the unit is still locked: unlock it with amonet first. Decide v1.1.0 vs v2.0.0 deliberately
(v2.0.0 ends Fire OS 5 and emOS's aarch64 path; EchoLocal targets Fire OS 6).

## M1 — Daemon on Fire OS (the known-good baseline) — done on the bench unit

Done 2026-09-15 on the bench unit. TECHO5's `dot` build uses EchoLocal's own
layout — `/system/app/echod/echod`, the `ledcontroller` service, state in `/data/misc/echolocal` —
so installing it is replacing one binary, and Home Assistant reconnects to the same device with the
same name, key and entity ids. Nothing to remove or re-add there.

```
adb -s <serial> push echod-dot /data/local/tmp/t5dot/echod-new
adb -s <serial> shell 'cp /system/app/echod/echod /data/local/tmp/t5dot/echod-echolocal.bak; setprop ctl.stop ledcontroller'
adb -s <serial> remount                     # / is dm-verity backed; mount(8) cannot do this
adb -s <serial> shell 'cat /data/local/tmp/t5dot/echod-new > /system/app/echod/echod; chmod 755 /system/app/echod/echod'
adb -s <serial> shell '/data/local/tmp/t5dot/echod-new tools remount ro'
adb -s <serial> shell 'setprop ctl.start ledcontroller'
```

**The way back** is the same sequence with `echod-echolocal.bak` in place of the new binary. Copies
of EchoLocal's binary and of the settings file as they were:
`D:\platform-tools\echodot\<serial>\echolocal-backup\`, and on the device at
`/data/local/tmp/t5dot/echod-echolocal.bak`. Nothing outside `/system/app/echod` was touched, and
both boot slots are untouched.

**Saved settings win over new defaults.** The unit had `mixing=delay-sum` and
`cancel_engine=webrtc` from EchoLocal, so the new seven-microphone default did not apply until
`/data/misc/echolocal/state.json` was edited (`mixing=all`, `cancel_engine=builtin`) with the
service stopped. Worth checking after any install, in the log: `restored what=microphone_mixing`.

Verified after the swap: version `0.0.7-dot-mics`, wake words loaded, `listening addr=[::]:6053
name=<unit>`, Home Assistant connected (`voice configuration requested peer=…`), wake
detection at 28% of one core.

## M1 (original plan)

Install the `dot` build the way EchoLocal does (TWRP writes a permissive boot image, then `/system`
is patched with the service takeover). Home Assistant pairs with it as an ESPHome device, and a
voice turn works. This also produces the first raw 9-channel captures for the mic bench (B1)
while Fire OS still configures the codec.

## M2 — First Linux boot (initramfs) — **done 2026-09-16, 02:37**

Linux runs on the Echo Dot 2 with no Amazon userspace: kernel up from our own ramdisk, `/dev` built
by `mdev`, userdata mounted, and a root shell over USB serial (COM26 on the host). Confirmed from
that shell: `Linux (none) 3.18.19-gecb8cb46060-dirty #1 SMP PREEMPT armv7l`, `uid=0(root)`, 135
device nodes, 481 MB free of 492 MB, `mt-snd-card` present with its PCM devices, the four
TLV320ADC3101s at i2c `0-0018`–`0-001b`, and the input devices (`ACCDET`, `mtk-kpd`, `keys`).

**The `skip_initramfs` reasoning held exactly.** From inside Linux, `/proc/cmdline` shows the
bootloader adding `root=/dev/dm-0`, the `dm=` verity table and `androidboot.slot_suffix=_b` — but
**no `skip_initramfs`**, because this is a recovery boot. That one word is the difference between
our ramdisk running and Fire OS booting instead.

It took two images. The first booted and ran its init through to the end (breadcrumbs 01–08, five
minutes up) but the USB gadget sat at `DISCONNECTED` and the host reported "Configuration Descriptor
Request Failed": the `acm` function was never told how many ports to offer, and the device class was
never set. `f_acm/instances=1` and `bDeviceClass=02` before `enable` fixed it, both from emOS, which
met the same two on this kernel. The console appeared about 20 s after the reboot.

Notes for next time:
- **`adb reboot recovery` boots Linux now**, not TWRP, so the image is flashed from Android with
  root (`dd` to `/dev/block/platform/bootdevice/by-name/recovery`), not from TWRP.
- TWRP is not in the recovery partition while this image is: `dd` `recovery.img` back to restore it.
- A normal power-on is Android throughout, and the Dot came back to Android with the daemon running
  and Home Assistant reconnected each time.

### Wi-Fi is the next piece, and where it starts

Inside Linux, `/proc/devices` has only `btif` (249): the combo chip's `stpwmt` (190), `wmtWifi`
(153) and `stpbt` (192) char devices are **not registered**, so `mknod` makes nodes nothing answers.
`/sys/class/wmtdetect/wmtdetect/dev` exists and says `154:0`, which is the driver's detect
interface and the way in. On Fire OS, Amazon's `wmt_loader` is what registers the rest; emOS
replaced it with `SET_PATCH_NAME`/`SET_STP_MODE` ioctls plus a patch-download loop
(docs/hardware.md, "Wi-Fi without Fire OS"). That is the next milestone's work, and it needs the
firmware from the Android system partition where `/etc/firmware` can find it.

### The image (prepared 2026-09-15)

All of it read-only on the unit. The image is
`D:\platform-tools\echodot\<serial>\techo5-dot-linux-first.img` (9.6 MB of a 16 MB
partition), built by `tools/linux/build-image.sh` from `tools/linux/init` and `mkimage.py`.

### It goes in the recovery partition, not a boot slot

**The bootloader adds `skip_initramfs` to the kernel command line, and this kernel honors it.**
None of the three stock images carries it in its own header — the running system shows it in
`/proc/cmdline`, along with `root=/dev/dm-0` and the `dm=` verity table, so LK composes those for a
normal boot of this A/B, system-as-root device. The string is present in the kernel image, so an
image flashed to a boot slot would have its ramdisk ignored and would quietly boot Fire OS.
(EchoMuse's emOS never met this: its tested path is Fire OS 5, which is not system-as-root, and its
Fire OS 6 path is marked untested.)

Recovery is booted as it is — that is how TWRP runs there — so the first Linux image goes to
`recovery` (p12, 16 MB). Both boot slots stay untouched, so:

- a normal power-on is still Android, whatever the state of this image;
- getting in is `adb reboot recovery` from Android, no buttons;
- getting out is a plain reboot, which the init does for itself after 15 unattended minutes;
- the way back is `dd` of `recovery.img` (the verified TWRP backup) from Android with root.

The cost is that TWRP is not available while this image is in its place. Acceptable because Android
boots normally and can restore it; the boot slots, `lk`, `tee` and `preloader` are never touched.

### What the image does

`tools/linux/init` is PID 1 under the static busybox: mounts `proc`, `sys`, a tmpfs `/dev`,
populates `/dev` with `mdev -s` (there is no devtmpfs), makes the MISC and userdata nodes by hand,
clears the bootloader message, mounts userdata for a log, brings up the Android USB gadget with the
`acm` function (`mt_usb/cmode=1` first, disable/enable around the function change) and puts a root
shell on `/dev/ttyGS0`. Progress goes to the kernel log, to breadcrumbs in MISC past the bootloader
message and slot metadata (offset 8 KiB, one 512-byte block per stage), and to
`/data/techo5-linux/first-boot.log`. `/sbin/to-android` and `/sbin/to-recovery` are the two exits.

### Flashing it (with the unit at hand)

```
adb -s <serial> reboot recovery                                   # TWRP, adb as root
adb -s <serial> push techo5-dot-linux-first.img /tmp/linux.img
adb -s <serial> shell 'dd if=/tmp/linux.img of=/dev/block/mmcblk0p12 && sync'
adb -s <serial> reboot recovery                                   # now boots Linux
```

Success looks like a new COM port on the host (the `acm` gadget) within about a minute, and
breadcrumbs in MISC afterwards. Failure looks like nothing at all, in which case: power-cycle,
Android comes up, `tools/readmisc.sh` says how far it got, and `/proc/last_kmsg` holds the previous
boot's kernel log.

### Then

- Wi-Fi, which on this board means the WMT bring-up below rather than a module `insmod`.
- Only after Linux is proven does anything get written to a boot slot.
- Wi-Fi: WMT bring-up (`SET_PATCH_NAME`/`SET_STP_MODE` 0x23 + patch loop) in Go or C, firmware
  where `/etc/firmware` finds it, then a static wpa_supplicant (try 2.10 first) with busybox
  `udhcpc` and a lease script. SSH (dropbear) replaces the cable.
- Daemon: open the codec's DAPM routes and mixer paths itself (`routeInputs` + the speaker path
  sequences), feed silence to hide the amp hiss, and complete a voice turn with no Fire OS
  userspace running.

## M3 — Persistent rootfs with trial slots — **done 2026-09-16**

As built, differing from the original plan:

- **Store:** the slots are directories `a` and `b` under `/techo5/` on the **cache** partition
  (p15), and `active` names one. Android's system partitions stay untouched, so Fire OS remains
  bootable as the fallback. The recovery initramfs bind-mounts the active slot, moves /dev, /sys
  and /proc across, and switch_roots. A `rescue` marker in the store keeps the initramfs up
  instead.
- **Rootfs:** busybox init with `/etc/techo5/boot.sh` as sysinit, respawning `techo5-run`
  (echod) and a root shell on the USB serial port. `tools/linux/mkrootfs.py` builds it.
- **No Android at runtime:** `cmd/wmtup` does the WMT detect ioctls and the patch
  conversation itself. The unit's Wi-Fi firmware is copied into the store (`firmware/`) on the
  first boot and shared by both slots, so Android's system partition is not mounted after that.
  Never call `EXT_CHIP_DETECT`, which panics this kernel.
- **Default boot:** each boot, the initramfs asks for `boot-recovery` again in MISC and counts
  tries in MISC block 14. boot.sh resets the count once echod has stayed up. After five unhealthy
  boots the unit boots no slot at all and stays in the rescue initramfs, on the network with SSH and
  the USB console; `techo5-retry` clears the count and `/sbin/to-android` boots Fire OS, but only
  when asked. Verified by a power cut.
- **Trial slots and updates (2026-09-16):** `slotctl` (the Show's, with the Dot's store at
  `/store/techo5`) keeps `slots/<x>.state` as good, trial n, or bad. `boot.sh` mounts the store
  again inside the slot, since the initramfs mount does not survive `switch_root`.
  - **Install:** the daemon's updater (`slotctl install` on the release's `rootfs["arm-dot"]`,
    then a reboot) unpacks into the other slot with two tries.
  - **Commit:** boot.sh commits once the same echod process has lived 30 s past the first minute.
  - **Rollback:** a trial slot that is not healthy by three minutes reboots itself. Out of tries,
    it is marked bad and the good slot boots.
  - Verified on the bench unit:
    - a 0.1.6 rootfs installed into slot b and committed;
    - a rootfs with a daemon that exits at once went trial 1, then trial 0, then bad;
    - slot b came back healthy with the try count at 0, never touching Fire OS.
  - Home Assistant is shown an update only when the release carries a Dot rootfs
    (`Manifest.Serves`, TECHO5 dot/mic-average). `tools/linux/build-dot-rootfs.py` builds the
    published tarball and TECHO5's `release.ps1 -DotRootfs` publishes it. No release has been
    published yet.
  - The boot image (kernel and initramfs) is per unit and never published, so changes to
    `tools/linux/init` still go out through the installer.

## M3b — Running a Dot (2026-09-16)

- **SSH:** the daemon's Security feature runs dropbear (port 22, keys only). It runs only while the
  **SSH** switch in Home Assistant is on, and a new install starts with it off.
  - Keys live in `/data/misc/echolocal/ssh` and host keys in `/data/techo5-linux/dropbear`, so both
    outlast slots and updates.
  - Keys normally come from Home Assistant (`ssh_keys`). The installer can add one of the PC's as
    well, but only when asked: `install-dot.py --ssh-key ~/.ssh/id_ed25519.pub`.
  - A unit set up before this moved its key and host key over once, with the switch left on.
  - The rescue initramfs starts its own server with the same keys, without waiting for the switch.
- **Wi-Fi without Fire OS:**
  - `wifi-set "name" "passphrase"` on the unit, or `tools/set-wifi.py --serial <s> --ssid <name>`
    from the PC over the USB console, which works with the network down. Either writes
    `/data/techo5-linux/wifi.conf` (the name and WPA key as hex) and rejoins at once.
  - `wifi-set --forget` returns to the network Fire OS saved.
  - The PC derives the key (`techo5lib.wifi_conf` in tools/, checked against the IEEE 802.11i test vector),
    so the passphrase never reaches the unit.
  - The installer asks for a network when the unit has none.
  - `techo5-net` is the one bring-up used by boot, rescue and `wifi-set`. udhcpc now stays running
    and renews its lease; before, it quit after the first lease.
- **Logs:** `techo5-logs` (init respawn) checks every ten minutes. It copies any log in
  `/data/techo5-linux` over 1 MB to `.1` and empties the original in place. Every writer appends,
  so this is safe, and each log stays at about 2 MB whatever the uptime.
- **TWRP without a PC:** the installer keeps the unit's own TWRP backup and its Linux image in the
  store.
  - `to-twrp --yes` writes TWRP to recovery, checks it, and reboots into it.
  - `adb shell sh /cache/techo5/back-to-linux.sh` from TWRP restores Linux.
  - Both verified on the bench unit, with md5s matching the backups.
- **Maintenance:** `touch /run/techo5/hold; killall echod` keeps the daemon down, freeing the mics
  and ring for `echod tools`.
- **LED whine:** absent under Linux; see [microphones.md](microphones.md).

## M4 — Microphones

[microphones.md](microphones.md) B0–B4: an offline bench and wake-word scoring first, then
full-resolution capture, then WebRTC AEC on the loopback, then a spatial stage only if it beats the
center mic where the center mic fails.

## M3c — Security (2026-09-16)

The Show's hardening (TECHO5 34d8b3d, bec64f5) merged into dot/mic-average, plus these (verified on
the bench unit):

- **Signed releases:**
  - A device takes `manifest.json` only with `manifest.json.sig`, the release key's ed25519
    signature over its exact bytes. The public key is built into echod; the private key is
    kept off every repository, on the release machine only.
  - `release.ps1` signs every release.
  - The updater uses its own HTTP client, which the "skip certificate checks" switch cannot reach,
    and fetches nothing before the clock is set.
- **SSH and the device key:** SSH cannot be switched on, and `ssh_keys` is refused, while Home
  Assistant's link has no device key.
- **Remote adb:** offered only on Android. A saved one is cleared on Linux, since the Fire OS
  fallback reads the same state file.
- **No automatic Fire OS:** five unhealthy boots leave the unit in rescue (USB console, SSH,
  firewall). `techo5-retry` tries the slots again, and `/sbin/to-android` boots Fire OS on request.
  Simulated with the try count at 5: rescue, then retry, then a healthy slot.
- **Firewall** (`techo5-firewall`, legacy iptables; this kernel has no nftables): inbound DROP on
  IPv4 and IPv6 except 6053, 5353/udp, 22, 8928, DHCP, ping and IPv6 neighbor discovery. Checked
  from the PC: only 22, 6053 and 8928 answer, and a test listener on 9000 is unreachable.
- **Bluetooth:**
  - bluetoothd runs without the input, hog, network and sap plugins.
  - The agent accepts pairing only in pairing mode and authorizes only A2DP/AVRCP.
  - bluealsa runs as the user `bluealsa`, with its own D-Bus policy.
- **bluealsa start crash fixed at the source:** Alpine's 4.3.1 scanned a 16-entry `LIB_INFO` array
  as 39 entries (fdk-aac 2.0.2) and ran off the stack depending on the environment's size.
  `build-bluealsa.sh` rebuilds it with upstream 6ccf455, "Fix SIGSEGV caused by too short fdk-aac
  LIB_INFO", and the environment-padding workaround is gone.
- **Permissions:** `/data/techo5-linux` and `/data/misc/echolocal` are 700; EchoLocal had left the
  latter world-writable.
- **Deliberately not done:**
  - a password on the USB console, since physical access already means an unlocked bootloader;
  - closing Sendspin on 8928, which is kept for playing audio to the Dot.

## M5 — Bluetooth

Done on the bench unit 2026-09-16 (see `docs/hardware.md`, Bluetooth kernel):
- kernel 3.18.19-bt with 48 security backports;
- btbridge with H4 framing and `-max-feature-page 1`;
- BlueZ and bluealsa, with A2DP to a speaker verified;
- the Home Assistant BLE proxy through a raw HCI socket alongside BlueZ, verified;
- screenless pairing that guesses: a phone that pairs in plays to the Dot; otherwise, after 20 s,
  the strongest speaker heard is connected to.

**The Dot as a Bluetooth speaker (v0.3.0, verified on the bench unit with a phone):**
- bluealsa runs `a2dp-sink` beside `a2dp-source`, and bluetoothd's class makes the Dot a loudspeaker
  to phones.
- The daemon reads the phone's stream and plays it as a media track (`feature/media/bluetooth.go`),
  resampling 44.1 kHz to the speaker's 48 kHz. The phone tested sent AAC at 44.1 kHz; bluealsa used
  about 2% CPU and the wake word kept 50 frames per second.
- Behavior:
  - the wake word ducks or pauses the phone's music;
  - the phone's volume slider controls the level;
  - pausing the phone releases the speaker after 5 s;
  - whichever of the phone and Home Assistant media started last plays.

**Phone calls (v0.4.0, verified on the bench unit with VoIP.ms):** TECHO5 main's `feature/phone`,
merged into the Dot branch. The Dot signs in over TLS, rings with its ring pulsing green, and the
action button answers and hangs up. Tested: calls to and from mobiles and between devices, the help
call (both phones alerted, a spoken message into the answered call), announcements into a call. The
firewall needs nothing: registration and media are connections the Dot makes, answered through its
ESTABLISHED rule.

**Pairing without Home Assistant (v0.3.1, verified on the bench unit with a phone):**
- Holding the action button for 5 s toggles pairing mode, with a rising (on) or falling (off)
  three-note chime; the ring pulses blue while pairing mode is on, however it was turned on. With no
  second assistant set up, the 0.7 s hold on the way does nothing; with one, the turn it starts is
  canceled at 5 s. Home Assistant sees the hold as a `long_hold` button event.
- A phone pairing, or the Dot connecting a speaker it picked, plays a two-note chime and holds the
  ring solid blue for 1.5 s.
- Saying "pair Bluetooth" works through a Home Assistant sentence automation
  ([bluetooth-pairing-automation.yaml](bluetooth-pairing-automation.yaml)) that turns on the pairing
  switch of the Dot that heard it.
- Fixed with it: a phone that paused and resumed within 5 s could find its stream still held by the
  old track, and was not tried again, so the music stayed silent. A failed open is now retried at
  the next look, and a stream already playing is not reopened.

## M6 — Installer

`tools/install-dot.py --serial <serial>` (first written in PowerShell) takes an unlocked Dot running Fire OS 6 with root adb
(EchoLocal) to the M3 end state. Verified on the bench unit 2026-09-16: it came back under its existing Home Assistant
name, healthy at 110 s. The steps:

1. **Check** the unit: biscuit, Fire OS 6, root adb, a saved Wi-Fi network.
2. **Back up** preloader through recovery, md5-checked against the device. An existing backup is
   never replaced. misc changes on every Linux boot, so a new copy is saved beside the old one
   with a timestamp. The recovery backup (TWRP) is the way back.
3. **Build** the boot image from this unit's own recovery backup, and build the rootfs. No
   Amazon code is published.
4. **Home Assistant identity:** keep EchoLocal's name and key if present. Otherwise ask for a name
   and generate a key, saved beside the backups and printed for Home Assistant.
5. **Write** the image to recovery and read it back. Unpack the rootfs into slot a (the old slot
   is kept as `a.old`), reset the tries, and reboot into Linux.

`--dry-run` stops after step 4 and writes nothing to the device. Unlocking (amonet, TWRP) stays a
separate step.

**Starting from TWRP** (verified on the bench unit 2026-09-16) works the same way. The installer
mounts userdata and cache itself and reads the Fire OS release off the running slot's system
partition. One trap: amonet's TWRP links `preloader`, `lk_a/b` and `tee1/2` in by-name to decoy
files in `/tmp/ota-decoy`, so an OTA cannot overwrite the unlock. The installer backs up the
`*_real` links instead, and `mmcblk0boot0` for the preloader. Those copies match the ones taken in
Fire OS byte for byte.

**After the reboot** the installer finds the unit's USB serial console by its serial number (both
boot stages set the gadget's iSerial from `androidboot.serialno`). It waits there for the try count
to read `healthy`, then checks port 6053, so it knows the Linux address even when started from TWRP.

## Ground rules

- Everything stays local; no cloud services.
- Upstream projects (EchoLocal, EchoMuse/emOS, amonet/kaeru, TECHO5, jxlarrea's research) are used
  under their licenses and credited in `NOTICE`.
- Never distribute a boot image: it contains Amazon's kernel. Images are assembled from each
  unit's own backup, as emOS does.
- No adb or fastboot command runs without an explicit `-s <serial>`, since other MT8163 devices
  may be attached to the same host.
