<p align="center">
  <img src="https://raw.githubusercontent.com/HuskerMinion/techo5/main/logo/TECHO5_logo.png" alt="TECHO5" width="200">
</p>

<p align="center">
  <img src="images/biscuit.png" alt="BISCUIT" width="120">&nbsp;&nbsp;<img src="images/radar.png" alt="RADAR" width="120">&nbsp;&nbsp;<img src="images/donut.png" alt="DONUT" width="120">
</p>

<h1 align="center">TECHO5 Dot</h1>

<h3 align="center">The 2016 Echo Dot, minus Amazon's cloud. Linux inside, Home Assistant in charge.</h3>

<p align="center">
  <a href="https://github.com/HuskerMinion/techo5-dot/releases/latest"><img src="https://img.shields.io/github/v/release/HuskerMinion/techo5-dot?label=release&color=e9a23b" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/Linux-Alpine-0D597F?logo=alpinelinux&logoColor=white" alt="Alpine Linux">
  <img src="https://img.shields.io/badge/Fire%20OS-none-3a2c22" alt="No Fire OS">
  <img src="https://img.shields.io/badge/Alexa-none-3a2c22" alt="No Alexa">
  <img src="https://img.shields.io/badge/Home%20Assistant-ESPHome%20API-41BDF5?logo=homeassistant&logoColor=white" alt="Home Assistant">
  <img src="https://img.shields.io/badge/updates-signed-2ea44f" alt="Signed updates">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT license"></a>
</p>

<p align="center">
  <a href="#what-happened-to-the-dot">What changed</a> ·
  <a href="#stock-echo-dot-2-vs-techo5-dot">Stock vs TECHO5 Dot</a> ·
  <a href="#installing">Install</a> ·
  <a href="#echo-2nd-gen">Echo 2nd gen</a> ·
  <a href="#echo-dot-3rd-gen">Echo Dot 3rd gen</a> ·
  <a href="#credits">Credits</a> ·
  <a href="https://github.com/HuskerMinion/techo5">TECHO5 for the Echo Show 5</a>
</p>

---

TECHO5 Dot replaces Fire OS on the Amazon Echo Dot 2nd generation (codename `biscuit`), the Echo
2nd generation (2017, codename `radar`) and the Echo Dot 3rd generation (2018, codename `donut`) with a
small Alpine Linux image and one daemon. The Dot becomes a Home Assistant voice satellite: it hears the
wake word on the device, talks to Home Assistant over its encrypted native API, and sends nothing to
Amazon. It still uses the internet for what you'd expect: checking this repo for signed updates and
setting its clock.

|  |  |
|---|---|
| 🐧 **Real Linux, no Fire OS** | Linux boots from the recovery partition and no Android process runs, not even for Wi-Fi. |
| 🚫 **No Alexa, no Amazon account** | Your voice goes to your Home Assistant, over its encrypted API, and nowhere else. |
| 🎙️ **All seven microphones** | Averaged, with echo cancellation, and a wake word heard on the device. |
| 🔊 **A Bluetooth speaker again** | Pair a phone, or send the Dot's audio to a speaker; a Home Assistant Bluetooth proxy on top. |
| 📞 **A speakerphone again** | Calls through your own SIP provider, placed by voice or from Home Assistant, answered with the action button; a help call that alerts your phones and dials people in turn. |
| 🔐 **Locked down** | A firewall that lets in only Home Assistant, mDNS, SSH, Sendspin, the setup page, and on Wi-Fi AirPlay and Spotify Connect while they are on; SSH is keys-only and starts off, and the setup page answers only while it is open. |
| 🔄 **Signed updates with rollback** | ed25519-signed releases install into a spare slot and roll back on their own if they don't come up healthy. |

**Built on [EchoLocal](https://github.com/ygelfand/echolocal)** by Yuri Gelfand (MIT), which first
made the Echo Dot 2 an ESPHome-native Home Assistant satellite with one Go daemon on Fire OS. TECHO5
Dot's daemon is that daemon, by way of TECHO5's `echod` (EchoLocal's code vendored and developed
further), and much of it is still EchoLocal's. What this project adds is Linux in place of Fire OS:
the boot image and slots, Wi-Fi without Android, the seven-microphone work, rebuilt Bluetooth, signed
updates and the installer. See [NOTICE](NOTICE).

It's a sibling of [TECHO5](https://github.com/HuskerMinion/techo5), which did the same for the
Echo Show 5. Both run the same daemon source, built per device.

> **Status: in daily use on three units.** Everything marked ✅ below was verified on a bench Dot
> that came from Fire OS 6574.1, and the installer and updates on two more. Expect rough edges, and
> keep your backups. The Echo 2nd gen and the Echo Dot 3rd gen are supported too, each tested on one
> unit so far; see [Echo 2nd gen](#echo-2nd-gen) and [Echo Dot 3rd gen](#echo-dot-3rd-gen).

## What happened to the Dot

- **Fire OS is gone at runtime.** Linux boots from the recovery partition. No Android process
  runs, not even for Wi-Fi: [`cmd/wmtup`](cmd/wmtup) talks to MediaTek's combo chip directly,
  where Amazon's loader used to.
- **All seven microphones work together.** The daemon averages all seven microphones and cancels
  the speaker's own echo from that average. Measured against the center mic alone, the average heard
  speech 1–2.5 dB better, and 2–4 dB better between 1.5 and 4.7 kHz. Steering a delay-and-sum beam
  added nothing on this 72 mm ring, so it isn't the default. The numbers and method are in
  [docs/microphones.md](docs/microphones.md).
- **Bluetooth came back, rebuilt.** Stock Bluetooth belonged to Fire OS. Here the Dot runs:
  - a kernel built from Amazon's GPL source with Bluetooth turned on and **48 upstream security
    fixes** backported (BlueBorne and later);
  - a bridge from the chip's raw HCI channel to BlueZ;
  - bluez-alsa, rebuilt with an upstream crash fix Alpine's package lacks.
- **Updates can't be forged, and a bad one rolls itself back.** Releases are signed (ed25519). An
  update installs into the spare of two root filesystem slots and boots on trial. If it doesn't
  come up healthy, the Dot returns to the previous slot by itself.

## Stock Echo Dot 2 vs TECHO5 Dot

| | Stock Echo Dot 2 (Alexa) | TECHO5 Dot |
|---|---|---|
| Voice assistant | Alexa, in Amazon's cloud | Home Assistant Assist ✅. Wake word on the device (microWakeWord: "Okay Nabu", "Hey Jarvis", "Hey Mycroft", "Alexa", and eight from the community collection — "Computer", "Jarvis", "Hey Friday", "GLaDOS", "HAL", "Terminator", "Marvin", "Home Assistant") ✅. Speech-to-text and replies come from whatever your Home Assistant pipeline uses |
| Where your voice goes | Amazon | Your Home Assistant, over its encrypted native API ✅ |
| Microphones | 7-mic array, Amazon's processing | All 7 averaged, with echo cancellation on the average ✅ |
| Talking over music | Alexa ducks on wake | Ducks on wake, and also for a few seconds after a *near miss*, so the second try is heard ✅ |
| Speaker | Yes | Yes, a Home Assistant media player ✅ |
| Light ring | Alexa's colors | Wake, listening, thinking, replying and error effects, set from Home Assistant ✅ |
| Buttons (action, volume, mic mute) | Yes | Yes, all four ✅. Mute is the hardware mute line |
| Timers | Yes | Yes (Home Assistant timers) |
| 3.5 mm audio out | Yes | In the daemon (jack detection, headphone path), with an **Audio output** choice on the setup page and in Home Assistant |
| Bluetooth: phone to Dot (Dot as a speaker) | Yes | Yes ✅. Hold the action button for 5 seconds (the ring pulses blue), or turn on **Bluetooth pairing** in Home Assistant, or say "pair Bluetooth" with [this automation](docs/bluetooth-pairing-automation.yaml). Then pick the Dot on your phone; a chime says it worked. Phone volume works, the wake word still works over the music, and whichever started last plays: phone or Home Assistant media |
| Bluetooth: Dot to a speaker or headphones | Yes | Yes ✅. If no phone pairs within 20 s, pairing mode connects the strongest speaker it hears, since there's no screen to choose on |
| Home Assistant Bluetooth proxy | No | Yes ✅, alongside Bluetooth audio |
| Multi-room music | Alexa groups | Sendspin (Music Assistant) client on port 8928. Built in |
| AirPlay and Spotify Connect | Spotify Connect | Both: off until turned on, on the setup page (Sound & Voice) or in Home Assistant. The Dot shows up under its own name in the Apple and Spotify apps (Spotify Premium), and an iPhone can group it with other AirPlay 2 speakers |
| Calling | Alexa calling and Drop In | Yes ✅, through your own SIP provider (TLS and SRTP): "call Alex" by voice, a help call that alerts phones and dials people in turn, calls between your own devices. The action button answers and hangs up, and the ring pulses green. Off until Home Assistant signs the Dot in. See [TECHO5's phone.md](https://github.com/HuskerMinion/techo5/blob/main/docs/phone.md) |
| Skills, shopping | Yes | **No.** Those are Alexa cloud services |
| Routines and smart home control | Alexa | Whatever Home Assistant does ✅ |
| Updates | Amazon, automatic | Signed releases from this repo, offered in Home Assistant, A/B slots with automatic rollback ✅ |
| Remote access | None | SSH, keys only, behind a Home Assistant switch that starts off ✅ |
| Network exposure | Amazon's | Firewall: inbound only the Home Assistant API, mDNS, SSH, Sendspin and the setup page, and on Wi-Fi AirPlay and Spotify Connect (nothing listens there unless they are on) ✅ |
| Changing Wi-Fi | Alexa app | `wifi-set` on the Dot, or `tools/set-wifi.py` from a computer over USB ✅ |
| If it won't boot | Factory reset | Rescue mode (USB console, SSH, firewall) after five bad boots. Fire OS and TWRP are still on the device, one command away ✅ |

Numbers from the bench unit:
- **Memory:** about 445 MB of 481 MB free with everything running, Bluetooth included.
- **Wake word:** about 30% of one core.
- **Root filesystem:** 49 MB.

### Boot time

Timed from a reboot command until Home Assistant's API answers from another machine, on the same
Dot:

| | Reboot to Home Assistant API |
|---|---|
| Fire OS 6 with the daemon as an Android service | 43.6 s |
| TECHO5 Dot v0.2.0 | 45.5–46.4 s (three runs) |

About the same, not faster. By Linux's own clock the Dot is on Wi-Fi at 21 s and listening for the
wake word at 26 s. The other ~19 s come before that clock starts: shutting down, the bootloader, and
loading the kernel. An earlier build waited for Bluetooth before starting the daemon and took 128 s;
now Bluetooth comes up in the background and a paired speaker reconnects when it's ready.

## What you need

- An Echo Dot 2nd gen, **already unlocked** with amonet, with Fire OS 6574.1 in its system slots, and
  sitting in TWRP or running Fire OS with root adb (for example with
  [EchoLocal](https://github.com/ygelfand/echolocal) installed). Unlocking isn't part of this project;
  [Getting started](https://github.com/HuskerMinion/techo5/blob/main/docs/getting-started.md#echo-dot-2nd-gen)
  has every command. For an Echo 2nd gen, see [Echo 2nd gen](#echo-2nd-gen): it's unlocked the same
  way, over wires soldered inside. For an Echo Dot 3rd gen, unlocked with kamakiri-donut, see
  [Echo Dot 3rd gen](#echo-dot-3rd-gen) for what differs.
- The Dot joined to Wi-Fi once in Fire OS. If it hasn't been, the installer asks for a network.
- A Windows, Linux or macOS computer with Python 3, adb and git
  ([setup for each](https://github.com/HuskerMinion/techo5/blob/main/docs/getting-started.md#set-up-your-computer-once)),
  plus Home Assistant. Nothing is compiled.

## Installing

Starting from a stock Dot? [Getting started](https://github.com/HuskerMinion/techo5/blob/main/docs/getting-started.md)
walks through it in order: updating Fire OS, the amonet unlock (linked), this installer, and Home
Assistant.

```
git clone https://github.com/HuskerMinion/techo5-dot
cd techo5-dot
python3 tools/install-dot.py --dry-run   # checks, backups, download, boot image; writes nothing
python3 tools/install-dot.py
```

It finds the Dot on its own (asking which, if several are plugged in) and asks what to call it in Home
Assistant when it has no name yet. From a script, pass `--serial <adb serial> --name "Kitchen"` and it
asks nothing.

The installer:
1. Backs up every boot-critical partition to `backups/<serial>/` and checks each copy against the
   device. On TWRP it reads the real bootloader partitions, not amonet's decoys.
2. Downloads the latest signed release (the root filesystem, the Bluetooth kernel and the rescue
   environment's packages). The manifest is only believed once the release key's signature over it
   checks out — the same key the device itself trusts — and every file is checked against the
   checksums it carries. `--release dot-v0.6.0` picks a version instead of the newest — releases here
   are tagged `dot-vX.Y.Z`, so the tag is what goes in, not the bare version; `--rootfs` and `--kernel`
   take your own builds ([docs/building.md](docs/building.md)).
3. Builds this unit's boot image from **its own** recovery backup, with the Bluetooth kernel for its
   board (`--no-bluetooth-kernel` keeps the unit's own, without Bluetooth). No Amazon binary is ever
   downloaded or published.
4. Writes the image to recovery, and the root filesystem into slot a.
5. Keeps an existing Home Assistant name and key, or asks for a name and makes a key.
6. Reboots, then waits on the Dot's USB console until the boot reports healthy.

Home Assistant then finds the Dot as an ESPHome device.

Updates after that come from this repo's releases, through Home Assistant's update card. They carry the
root filesystem only; a Dot installed before the Bluetooth kernel gets it with
`python3 tools/update-boot.py --serial <serial> --address <address>` (SSH switched on in Home Assistant).
On Windows, type `python` for `python3`.

### Echo 2nd gen

<img src="images/radar.png" alt="RADAR" width="90" align="right">

The Echo 2nd gen (2017, model XC56PY, `radar`) has the Dot's SoC and boot chain, so it uses the same release and the
same installer. The installer and the daemon tell the two apart. Supported, tested on one unit so far.
Details are in [docs/hardware-radar.md](docs/hardware-radar.md).

Tested on that unit: the speaker (with the unit's own EQ), the headphone jack (mono), voice with Home
Assistant, Bluetooth (a phone plays to it), Wi-Fi, all seven microphones, the buttons, the light ring
and the light sensor. Not yet: the vendor's own sound tuning files aren't used. It plays without them.

It can also run [LibreEcho](https://github.com/aslater3/LibreEcho-Linux)'s Linux 6.1 kernel instead of
the 3.18 one, opt-in: `--linux-61` on the installer or on `update-boot.py`, and `--linux-318` goes back.
Bluetooth and the headphone jack aren't tested on 6.1 yet. See
[docs/hardware-radar.md](docs/hardware-radar.md#linux-61-optional).

**USB is inside.** There's no USB port on the case, only D+, D- and GND pads on the amplifier board
inside, marked TP15 (D+), TP14 (D-) and TP13 (GND).
1. Open the case. [LibreEcho](https://github.com/aslater3/LibreEcho)'s disassembly guide shows the
   way in.
2. Solder short wires from a USB cable to D+, D- and GND. Leave its red (5 V) wire unconnected: the
   Echo runs from its own barrel-jack supply. A 3D-printed pogo-pin dock was tried and dropped out once
   TWRP started. Soldered wires were steady.

Once it's installed you rarely need USB (updates, SSH and Home Assistant all go over Wi-Fi), so the
wires can come off afterward. Note which pad is which first.

**Unlock** with amonet-radar v1.0.0 ([R0rt1z2's amonet, branch
`mt8163-radar`](https://github.com/R0rt1z2/amonet/tree/mt8163-radar), release zip `amonet-radar-v1.0.0.zip`):
1. Hold the action button (the dot) while you plug in the power. The ring turns blue, then white:
   that's stock fastboot.
2. Run `fastbrick.ps1` on Windows, or `fastbrick.sh`. It prints `Exploit most likely successful!`.
   If the script never sees the unit, check the wires and try again. With a flaky pogo dock, Windows
   reported "Port Reset Failed" and "Device Descriptor Request Failed".
3. If the ring then stays green for minutes, unplug the power, hold **+** (volume up) and plug it back
   in. A white ring is amonet's TWRP.

Stock fastboot and TWRP both show white, so go by the step you're on, not only the color. Volume down
at power-on gives amonet's fastboot (a rainbow ring). It doesn't support `fastboot boot`, and
`fastboot reboot` from it returns to Fire OS. Until TECHO5 is installed, holding **+** at power-on gets you back to TWRP.
Afterward TECHO5 sits in the recovery partition where TWRP was: `to-twrp` puts TWRP back (Getting
started, "Going back to TWRP or Fire OS").

**Back up.** The installer backs up every boot partition itself and checks each copy by md5. A full
copy of the eMMC from TWRP is worth having too: the whole disk is about 3.9 GB and took about 30
minutes over the soldered wires.

**Install** from TWRP:

```
python3 tools/install-dot.py --wifi "<network name>"
```

It asks for the Wi-Fi passphrase and a name for Home Assistant. `--ssh-key <file.pub>` adds your SSH
key. From dot-v1.0.19 on, the release carries the Echo 2nd gen's own Bluetooth kernel and the installer
takes it. With an older release the unit keeps its own kernel, without Bluetooth.

On Windows, a PC that has seen other Amazon devices may bind the TECHO5 USB console (`18d1:4ee7`) to
Google's ADB driver instead of a COM port. The install still finished, because Wi-Fi came up. To get
the console, pick **USB Serial Device** for it in Device Manager.

### Echo Dot 3rd gen

<img src="images/donut.png" alt="DONUT" width="90" align="right">

The Echo Dot 3rd gen (2018, model D9N29T, `donut`) is a different machine from the Dot 2: a MediaTek
MT8516, four microphones, and Wi-Fi and Bluetooth on an MT7668. It uses the same release, installer and
update channel, with its own build of the daemon and its own root filesystem. Supported, tested on one
unit so far. Details are in [docs/hardware-donut.md](docs/hardware-donut.md). The 2019 refresh and the
model with a clock are other boards and aren't supported.

Tested on that unit: voice with Home Assistant, all four microphones, the speaker, the headphone jack,
the buttons, mute and its light, the light ring, Wi-Fi, Bluetooth (a phone plays to it), the Bluetooth
proxy, and an update to dot-v1.0.31 from Home Assistant. Not yet tried on a Dot 3: `update-boot.py`,
the rescue after five failed boots, and installing on a unit with no saved Wi-Fi network.

It differs from the Dot 2 in a few places:
- **It keeps its own kernel.** The image carries the unit's stock kernel, which loads Amazon's Wi-Fi
  and Bluetooth drivers from the unit's own system partition. The Bluetooth stack that kernel lacks
  ships as modules built from source, and each release carries their complete source.
- **Linux goes in `swdl`.** There's no recovery partition: the unlock boots `swdl` as recovery,
  where the kit put TWRP. The installer keeps TWRP's copy in the store, so `to-twrp` works as on
  the Dot 2.
- **Our own wpa_supplicant.** Alpine's 2.11 can't finish a Wi-Fi handshake with this driver; ours
  carries a one-line fix (see the hardware notes).

**USB is under the base.** There's no USB port on the case. D− and D+ are on test pads TM4 and TM5,
ground on TM1. Solder short wires from a USB cable: white (D−) to TM4, green (D+) to TM5, black to TM1.
Leave the red (5 V) wire unconnected; the Dot runs from its own adapter. Once it's installed you rarely
need USB, since updates, SSH and Home Assistant all go over Wi-Fi.

**Unlock** with the kamakiri-donut v1.0.0 kit from
[its XDA thread](https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-dot-3rd-gen-2018-donut.4801400/):
hold the action (dot) button while you plug in the power to reach the bootrom, and start kamakiri with
the Dot powered off. The kit leaves TWRP in `swdl` and stock Fire OS in both slots. Then flash its
`boot-root.zip` for root adb.

**Install** from Fire OS with root adb, or from TWRP:

```
python3 tools/install-dot.py
```

It backs up every boot partition, including `swdl` with TWRP in it, builds the image on the unit's own
kernel, and puts the root filesystem on userdata. On Windows the TECHO5 console shows up as a USB serial
device (`1d6b:0104`).

## Where things are

- [docs/porting-plan.md](docs/porting-plan.md): how it was built, milestone by milestone, and what
  each step proved.
- [docs/hardware.md](docs/hardware.md): the board, partitions, kernels, and the Bluetooth kernel's
  security backports.
- [docs/hardware-radar.md](docs/hardware-radar.md): the Echo 2nd gen: its board, audio, kernel,
  unlock and USB.
- [docs/hardware-donut.md](docs/hardware-donut.md): the Echo Dot 3rd gen: its board, partitions,
  boot image, Wi-Fi, audio and Bluetooth modules.
- [docs/microphones.md](docs/microphones.md): the array measurements, echo cancellation, and the
  LED whine that turned out to be Fire OS's.
- `tools/`: the installer, the Wi-Fi tool, the boot image updater, and `tools/linux/` (initramfs,
  root filesystem overlay, slot tool, firewall, kernel and bluez-alsa build scripts and patches).
- `cmd/wmtup`: Wi-Fi chip bring-up without Android.
- [docs/building.md](docs/building.md): building the kernel, bluez-alsa, the daemon and the root
  filesystem yourself.
- The daemon's source: [TECHO5](https://github.com/HuskerMinion/techo5), branch `main`, built with
  `-tags dot` (`-tags dot,donut` for the Echo Dot 3rd gen).

## Credits

See [NOTICE](NOTICE) for the full list.
- The badges: BISCUIT is a homemade Southern buttermilk biscuit with sorghum syrup, photo by
  Georgiabrown24, [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Homemade_Southern_Buttermilk_Biscuit_with_Sorghum_Syrup.jpg),
  [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) (the cropped badge is CC BY-SA 4.0
  too). RADAR is the 30-foot dish of NSSL's first research Doppler weather radar, NOAA Photo Library,
  OAR/ERL/National Severe Storms Laboratory, public domain,
  [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Nssl0026_-_Flickr_-_NOAA_Photo_Library.jpg).
  DONUT is a chocolate-glazed doughnut with sprinkles, photo by Kiran Foster,
  [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Chocolate-glazed_doughnut_with_sprinkles_(7429937320).jpg),
  [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/).
- [EchoLocal](https://github.com/ygelfand/echolocal) (MIT, Yuri Gelfand): the Echo Dot 2 daemon this
  project is built on (see above).
- [EchoMuse / emOS](https://github.com/wilbowes/EchoMuse) (MIT, Wil Bowes): the first Linux
  userspace on this hardware. Its notes on the USB gadget, Wi-Fi patch download and the mic array
  saved days.
- amonet and kaeru ([R0rt1z2](https://github.com/R0rt1z2)): the unlock. The Echo Dot 3rd gen's
  kamakiri-donut kit and its
  [XDA thread](https://xdaforums.com/t/unlock-root-twrp-unbrick-amazon-echo-dot-3rd-gen-2018-donut.4801400/).
- [Pallav Aggarwal's teardown](https://pallavaggarwal.in/2023/01/10/teardown-amazon-echo-dot-3rd-gen/)
  of the Echo Dot 3rd gen.
- [@proffalken](https://github.com/proffalken): the
  [step-by-step install from Linux](https://gist.github.com/proffalken/377ae50146affe1886dddaaacb87926b)
  (the exact Fire OS build, trusting the adb key, byte-safe backups, the Linux serial console lookup)
  that the getting started guide and the cross-platform installer are based on.
- [jxlarrea](https://github.com/jxlarrea/lineageos-echo-show-camera): echo cancellation
  measurements on the Echo Show family.
- [bluez-alsa](https://github.com/arkq/bluez-alsa) (arkq): the upstream fix for the fdk-aac
  capability crash (6ccf455).
- The Linux kernel's Bluetooth developers: every backported fix is listed with its commit.
- [TECHO5](https://github.com/HuskerMinion/techo5): the daemon, the Linux image tooling and the
  root filesystem slot design this project shares.
- Amazon's GPL kernel source releases for the Echo Dot (2nd generation), Echo_Dot_src-6.5.7.1-20251024,
  and the Echo (2nd generation), Echo_v2_src-6.5.7.1-20251024: the kernels the Bluetooth builds
  start from. For the Echo Dot 3rd gen's Bluetooth modules, the Echo Input's
  (Echo_Input_src-6.5.4.7-20210719, as imported in
  [bengris32/android_kernel_amazon_cupcake](https://github.com/bengris32/android_kernel_amazon_cupcake)).
- [wpa_supplicant](https://w1.fi/wpa_supplicant/) (Jouni Malinen and contributors, BSD): Wi-Fi on
  the Echo Dot 3rd gen, rebuilt with one change.
- [Alpine Linux](https://alpinelinux.org/), [BlueZ](https://www.bluez.org/) and BusyBox: the system
  underneath.
- [microWakeWord](https://github.com/kahrendt/microWakeWord) (Kevin Ahrendt) and the
  [ESPHome wake word models](https://github.com/esphome/micro-wake-word-models): the wake word on
  the device.
- [Home Assistant](https://www.home-assistant.io/), [ESPHome](https://esphome.io/) and
  [go-esphome-device](https://github.com/ygelfand/go-esphome-device): the voice pipeline and the API
  the Dot speaks; [Music Assistant](https://www.music-assistant.io/) for Sendspin.

## License

MIT for this project. See [LICENSE](LICENSE) and [NOTICE](NOTICE). The kernel patches in
`tools/linux/kernel-patches` and the bluez-alsa patch keep their upstream licenses (GPL-2.0 and
MIT), and so do the Echo Dot 3rd gen's wpa_supplicant patch (BSD) and Bluetooth modules (GPL-2.0).

TECHO5 Dot isn't affiliated with Amazon. Echo and Alexa are trademarks of Amazon.com, Inc.
