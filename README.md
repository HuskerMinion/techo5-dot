# TECHO5 Dot

**The 2016 Echo Dot, minus the cloud.** TECHO5 Dot replaces Fire OS on the Amazon Echo Dot 2nd
generation (codename `biscuit`) with a small Alpine Linux image and one daemon. The Dot becomes a
Home Assistant voice satellite: it hears the wake word on the device, talks to Home Assistant over
its encrypted native API, and sends nothing to Amazon.

It's a sibling of [TECHO5](https://github.com/HuskerMinion/techo5), which did the same for the
Echo Show 5. Both run the same daemon source, built per device.

> **Status: working, tested on one unit.** Everything marked ✅ below was verified on a bench Dot
> that came from Fire OS 6574.1. It hasn't been tried on a second unit yet. Expect rough edges, and
> keep your backups.

## What happened to the Dot

- **Fire OS is gone at runtime.** Linux boots from the recovery partition. No Android process
  runs, not even for Wi-Fi: [`cmd/wmtup`](cmd/wmtup) talks to MediaTek's combo chip directly,
  where Amazon's loader used to.
- **All seven microphones work together.** The daemon averages all seven microphones and cancels
  the speaker's own echo from that average. Measured against the centre mic alone, the average heard
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
| Voice assistant | Alexa, in Amazon's cloud | Home Assistant Assist ✅. Wake word on the device (microWakeWord: "Okay Nabu", "Hey Jarvis", "Hey Mycroft") ✅. Speech-to-text and replies come from whatever your Home Assistant pipeline uses |
| Where your voice goes | Amazon | Your Home Assistant, over its encrypted native API ✅ |
| Microphones | 7-mic array, Amazon's processing | All 7 averaged, with echo cancellation on the average ✅ |
| Talking over music | Alexa ducks on wake | Ducks on wake, and also for a few seconds after a *near miss*, so the second try is heard ✅ |
| Speaker | Yes | Yes, a Home Assistant media player ✅ |
| Light ring | Alexa's colours | Wake, listening, thinking, replying and error effects, set from Home Assistant ✅ |
| Buttons (action, volume, mic mute) | Yes | Yes, all four ✅. Mute is the hardware mute line |
| Timers | Yes | Yes (Home Assistant timers) |
| 3.5 mm audio out | Yes | In the daemon (jack detection, headphone path). Not yet tested on this image |
| Bluetooth: phone to Dot (Dot as a speaker) | Yes | Yes ✅. Turn on **Bluetooth pairing** in Home Assistant and pick the Dot on your phone. Phone volume works, the wake word still works over the music, and whichever started last plays: phone or Home Assistant media |
| Bluetooth: Dot to a speaker or headphones | Yes | Yes ✅. If no phone pairs within 20 s, pairing mode connects the strongest speaker it hears, since there's no screen to choose on |
| Home Assistant Bluetooth proxy | No | Yes ✅, alongside Bluetooth audio |
| Multi-room music | Alexa groups | Sendspin (Music Assistant) client on port 8928. Built in, not yet tested on this image |
| Calling, Drop In, announcements, skills, shopping | Yes | **No.** Those are Alexa cloud services |
| Routines and smart home control | Alexa | Whatever Home Assistant does ✅ |
| Updates | Amazon, automatic | Signed releases from this repo, offered in Home Assistant, A/B slots with automatic rollback ✅ |
| Remote access | None | SSH, keys only, behind a Home Assistant switch that starts off ✅ |
| Network exposure | Amazon's | Firewall: inbound only the Home Assistant API, mDNS, SSH and Sendspin ✅ |
| Changing Wi-Fi | Alexa app | `wifi-set` on the Dot, or `tools/set-wifi.ps1` from a PC over USB ✅ |
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

- An Echo Dot 2nd gen, **already unlocked** with amonet and sitting in TWRP, or running Fire OS 6
  with root adb (for example with [EchoLocal](https://github.com/ygelfand/echolocal) installed).
  Unlocking isn't part of this project.
- The Dot joined to Wi-Fi once in Fire OS. If it hasn't been, the installer asks for a network.
- A Windows PC with adb, Python 3, Go and WSL, plus Home Assistant.

## Installing

```powershell
.\tools\install-dot.ps1 -Serial <adb serial> -DryRun   # checks, backups and builds; writes nothing
.\tools\install-dot.ps1 -Serial <adb serial>
```

The installer:
1. Backs up every boot-critical partition to the PC and checks each copy against the device. On
   TWRP it reads the real bootloader partitions, not amonet's decoys.
2. Builds this unit's boot image from **its own** recovery backup. No Amazon binary is ever
   downloaded or published.
3. Writes the image to recovery, and the root filesystem into slot a.
4. Keeps an existing Home Assistant name and key, or asks for a name and makes a key.
5. Reboots, then waits on the Dot's USB console until the boot reports healthy.

Home Assistant then finds the Dot as an ESPHome device.

Updates after that come from this repo's releases, through Home Assistant's update card.

## Where things are

- [docs/porting-plan.md](docs/porting-plan.md): how it was built, milestone by milestone, and what
  each step proved.
- [docs/hardware.md](docs/hardware.md): the board, partitions, kernels, and the Bluetooth kernel's
  security backports.
- [docs/microphones.md](docs/microphones.md): the array measurements, echo cancellation, and the
  LED whine that turned out to be Fire OS's.
- `tools/`: the installer, the Wi-Fi tool, the boot image updater, and `tools/linux/` (initramfs,
  root filesystem overlay, slot tool, firewall, kernel and bluez-alsa build scripts and patches).
- `cmd/wmtup`: Wi-Fi chip bring-up without Android.
- The daemon's source: TECHO5, branch
  [`dot/mic-average`](https://github.com/HuskerMinion/techo5/tree/dot/mic-average), built with
  `-tags dot`.

## Credits

See [NOTICE](NOTICE) for the full list.
- [EchoLocal](https://github.com/ygelfand/echolocal) (MIT, Yuri Gelfand): the Dot daemon TECHO5's
  `echod` grew from.
- [EchoMuse / emOS](https://github.com/wilbowes/EchoMuse) (MIT, Wil Bowes): the first Linux
  userspace on this hardware. Its notes on the USB gadget, Wi-Fi patch download and the mic array
  saved days.
- amonet and kaeru ([R0rt1z2](https://github.com/R0rt1z2)): the unlock.
- [jxlarrea](https://github.com/jxlarrea/lineageos-echo-show-camera): echo cancellation
  measurements on the Echo Show family.
- [bluez-alsa](https://github.com/arkq/bluez-alsa) (arkq): the upstream fix for the fdk-aac
  capability crash (6ccf455).
- The Linux kernel's Bluetooth developers: every backported fix is listed with its commit.

## License

MIT for this project. See [LICENSE](LICENSE) and [NOTICE](NOTICE). The kernel patches in
`tools/linux/kernel-patches` and the bluez-alsa patch keep their upstream licenses (GPL-2.0 and
MIT).

TECHO5 Dot isn't affiliated with Amazon. Echo and Alexa are trademarks of Amazon.com, Inc.
