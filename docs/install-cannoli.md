# Installing on the Echo Dot 5th gen (cannoli)

`tools/install-cannoli.py` takes an Echo Dot 5th gen (2022, `cannoli`, MT8519, NAND with UBI) from
stock or rooted Fire OS to TECHO5 Linux in one run. It works on one unit so far.
Keep its backups, and please open an issue with what worked and what didn't.

[hardware-cannoli.md](hardware-cannoli.md) has the board, why the unit is pinned to Fire OS 8349, and which
other projects and sources the install depends on. The device's exploration is in TECHO5's discussion
[#610](https://github.com/HuskerMinion/techo5/discussions/610).

The Dot 5 has two boot slots and keeps both:

- **Slot A** is Fire OS, rooted by [firebreak](https://github.com/Gamer92000/firebreak)'s cannoli route.
  Every ordinary power-on starts it, so the way back is always there.
- **Slot B** is TECHO5 Linux. It starts when **Volume-Up is held at power-on**. Its boot image is the
  unit's own signed kernel and device tree with TECHO5's ramdisk. The ramdisk boots the root filesystem
  from a store on userdata, or stays up as a rescue environment when there is none.

Amazon's kernel, drivers and firmware are never downloaded from here or published. The installer builds
the slot-B image on your computer from the backup it takes of the unit it runs on.

## What you need

- USB on the board: D+, D- and GND soldered from the 3×3 test pads under the rubber foot to a USB cable,
  with its 5 V wire left off. firebreak's [cannoli guide](https://github.com/Gamer92000/firebreak/blob/main/docs/cannoli.md)
  has the pads. The Dot's own 15 V adapter powers it.
- Fire OS **NS6575/8349** (6.5.7.5, build 8349), or an older build that the installer upgrades to it
  (see below). TECHO5's slot B is built for 8349: the kernel modules' CRCs and the stock boot FIT are
  that build's. Which Fire OS builds firebreak's unlock works on is in firebreak's
  [cannoli guide](https://github.com/Gamer92000/firebreak/blob/main/docs/cannoli.md). **Keep the unit off Wi-Fi** until it is installed, so it takes no
  update past 8349.
- firebreak's per-unit bundle, for a unit that isn't rooted yet. It is built for this unit's serial on an
  x86-64 Linux host (WSL is fine) with
  [cannoli-os-builder](https://github.com/Gamer92000/cannoli-os-builder) and the
  [TWRP builder](https://github.com/Gamer92000/twrp_device_amazon_cannoli). Their READMEs have the
  commands. The installer looks for it in `<unit>/bundle` (`--bundle` points elsewhere). Its
  `bundle.json` has to say `"adb": "keyless"`: a bundle from a cannoli-os-builder before `74903ad` baked
  the build host's adb key into slot A, and firebreak refuses it. The installer checks this before it asks
  anything or starts firebreak. If it stops there, rebuild the bundle with cannoli-os-builder, without
  `--adb-key` and into a fresh `--output`.

  **Build it without `--port`.** cannoli-os-builder's `--port` bakes the Echo Dot Assist port (hassmic)
  into slot A's `system_a`, and firebreak's unlock writes the bundle's slot A as it is. TECHO5 only needs
  rooted stock Fire OS there. With the port in, hassmic runs on every ordinary power-on and holds the
  speaker and microphones. Restoring a clean firebreak backup doesn't help: the next unlock with that
  bundle puts hassmic back. `ls /system/hassmic` from adb shows which one a unit has. If it's there,
  rebuild the bundle without `--port`, run `firebreak unlock` with it again, and remove the port's data
  with `rm -rf /data/local/hassmic`.
- A firebreak checkout with the cannoli route next to this one (`../firebreak`, or `--firebreak`), with
  [uv](https://docs.astral.sh/uv/). Run the installer from **Windows' own Python** when it has to unlock:
  firebreak catches the unit's preloader from native Windows, not from WSL, where the USB window is shorter
  than usbipd forwards it.
- The pieces of the slot-B build, from [building.md](building.md): the inputs (`tools/fetch-inputs.py` in
  TECHO5), `cannoli_hwfixup.ko`, the patched `wpa_supplicant`, and the root filesystem
  (`build-dot-rootfs.py --board cannoli`, to `build/techo5-cannoli-rootfs.tar.gz`). The Bluetooth payload
  for the rescue environment is built too, when its modules and `btbridge` are there.

The unit's folder (`--unit`, default `../cannoli-<serial>`) is the one you build the bundle in:
`bundle/`, `stock-8349-parts/` (the unpacked 8349 OTA, for an upgrade) and `builds/`.

## Run it

```
python3 tools/install-cannoli.py --serial <serial> --dry-run
python3 tools/install-cannoli.py --serial <serial> --name Kitchen
```

`--dry-run` does the checks, the backups and the builds, and writes nothing to the unit. On a unit that
isn't rooted yet, it stops after the checks and prints the firebreak commands it would run.

1. **The unit's state.** The installer reads what the unit is running:

   | adb / fastboot shows | the unit runs | the installer |
   |---|---|---|
   | `device`, adb not root, orange ring | stock Fire OS, setup mode | unlocks it with firebreak |
   | `unauthorized` or `offline` | stock Fire OS whose secure adb has no screen to accept the key (a factory-reset unit) | unlocks it with firebreak, whose BL2 and fastboot path needs no adb, and reads the build from the rooted slot A afterwards |
   | `device`, root adb | firebreak's rooted Fire OS in slot A | goes on |
   | `recovery` | TWRP in slot B (Volume-Up held) | checks 8349's volumes, then boots Fire OS |
   | `fastboot` | the RAM fastboot (action button held) | stops: the build can't be read there |
   | nothing | no USB | stops: check the solder joints |

2. **The build.** It reads `ro.build.display.id` and `ro.build.version.number`. From rooted Fire OS or
   TWRP it also checks that the TEE, SCP and MCUPM volumes hash to 8349's, as firebreak does.
   - **8349** goes on.
   - **An older build** (the 2022 launch build, 6.5.6.1/4936, 6.5.7.3/6562 and 7577, 6574.1/8149,
     6.5.7.5/8338 and 8344) needs the upgrade to 8349 first. The installer says so, asks you to type
     `UPGRADE`, and then runs `firebreak cannoli-upgrade` with the unpacked 8349 OTA
     (`update-kindle-cannoli-NS6575_user_8349_0013323246980.bin`, MD5
     `0bf96b3cd8fca30fdd12f61208e44d76`, from
     [FTVDB](https://ftvdb.com/echo/firmware/com.amazon.cannoli.android.os/0bf96b3cd8fca30fdd12f61208e44d76-13323246980-fire-os-6-5-7-5-ns6575-8349-2026-10-27/),
     unpacked with `payload_dump.py`). Then it runs firebreak's unlock again and writes the four
     `brhgptlk` BL2 copies from TWRP, each read back over the image's 249856 bytes. **This upgrade is
     one-way:** the first 8349 TEE boot burns the anti-rollback eFuse, and the 2022 firmware can never
     come back. An Amazon OTA to 8349 does the same. Before the upgrade it backs up the launch volumes and
     BL2 copies from TWRP.
   - **A newer build** stops with nothing written: TECHO5's slot B is built for 8349's kernel. firebreak's
     [cannoli guide](https://github.com/Gamer92000/firebreak/blob/main/docs/cannoli.md) says which builds its unlock works on.
3. **The unlock.** A stock unit is unlocked with `firebreak unlock --device cannoli`, with a fresh
   `--backup` folder in `backups/<serial>/`. Follow its power prompts (unplug the 15 V adapter, Enter,
   plug it back in). The installer doesn't do any of firebreak's work itself.
4. **The backups**, to `backups/<serial>/m0/`, before anything of TECHO5's is written: every UBI volume
   but `cache` and `userdata` (`u_<N>.bin`), the four `brhgptlk` copies, and the Wi-Fi and Bluetooth
   drivers and firmware from Fire OS (`modules/`, `firmware/`). Each copy is checked against the sha256
   the unit computes itself, and `ubi-sha256.txt` lists them with their volume names. A copy already
   there and the same is kept. If a volume has changed, the new copy is saved beside the old one with a
   time stamp. The TWRP that was in slot B is kept as `backups/<serial>/twrp-boot_b.img`: it's the way
   back to recovery. If a rooted 8349 unit's `brhgptlk` copies still hold the launch BL2, the installer
   checks that `stock-8349-parts/brhgptlk_0.img` (`--ota-parts`) is there with 8349's hash, then asks
   before it writes it to them (type `BL2`, or pass `--accept-bl2`). They are written from TWRP before
   `boot_b` is (step 7), while slot B still holds TWRP; on a reinstall over TECHO5 the installer puts the
   kept TWRP back into `boot_b` first.
5. **The build.** `tools/linux/build-cannoli-image.py` builds slot B's image from that backup: boot_a's
   stock 8349 FIT with its ramdisk replaced, checked to be byte-for-byte the stock FIT everywhere else and
   to fit what the unit's U-Boot reads of `boot_b`. Then the Bluetooth payload.
6. **Name, Wi-Fi and key**, the same as `install-dot.py`: the network Fire OS saved (asked about first) or
   `--wifi`, a name for Home Assistant, and a new key in `backups/<serial>/home-assistant.key`, or the
   ones a unit already has.
7. **The writes.** First any `brhgptlk` copies from TWRP (the unit is powered on with Volume-Up held for
   it, then rebooted into Fire OS). Then, from Fire OS in slot A: `boot_b` with `ubiupdatevol`, read back
   over the image's length. Then the root filesystem into the store's slot a (`/data/techo5-store/techo5`,
   slotctl's layout). Any earlier slot a is marked bad and removed first, so a run cut short leaves no
   half-written slot to boot, and the installer reads how much room userdata has. The
   slot takes about 63 MiB unpacked and the tarball is about 32 MB. After a factory reset userdata has
   about 155 MB free, so the whole tarball fits beside the slot. With less room, the tarball is split on
   this computer into parts that each fit and are each a whole tarball. Every part is pushed, checked by
   sha256 on the unit, unpacked with tar's exit status and errors read back, and deleted before the next.
   The slot becomes active only once it has every path the tarball has, the four files init-cannoli boots,
   and its `/bin/sh` symlink. Then come the Bluetooth payload and the name, key, Wi-Fi and wake words.
   Fire OS's own files on userdata are left as they are.

   An earlier version streamed the tarball through `adb exec-in`. On the first unit that stream lost its
   tail without a word, and busybox's tar, which makes symlinks last, left most of them out. A copy cut
   short is now caught by its sha256 before tar sees it.
8. **The first boot.** Unplug the power, hold Volume-Up, plug it back in and keep holding about 5 s. The
   installer waits on the unit's USB console (`1d6b:0104`, "Echo Dot 5 Linux") until a slot has booted
   and its `boot.sh` has counted it healthy. Home Assistant then discovers it as an ESPHome device.

## Setting it up over Bluetooth (`--for-someone-else`)

```
python3 tools/install-cannoli.py --serial <serial> --for-someone-else
```

This is for a Dot you're giving away, and for one you'd rather set up from Home Assistant than from
this computer, for example after a factory reset. It works as `install-dot.py --for-someone-else` does:

- It asks for no Wi-Fi network, name or Home Assistant key, and writes none. A unit with no network
  saved in Fire OS, such as one that was just factory-reset, is fine.
- It clears any name, key and network an earlier install left. It writes a supplicant configuration with
  no network, so Linux doesn't fall back to the network Fire OS saved, and the first-run marker
  (`/data/misc/echolocal/firstrun`).
- On its first slot-B boot the Dot starts its own setup. About 45 seconds after it starts, its ring
  breathes and it offers Improv over Bluetooth. Give it a network from Home Assistant (or
  [improv-wifi.com](https://www.improv-wifi.com)), and that Home Assistant then adds it with nothing typed.

The backups, the build and the writes are the same as without it. It can't be used with `--name`,
`--wifi`, `--wifi-passphrase-file`, `--key-file` or `--ssh-key`, or with `--rescue-only` (the rescue
environment has no setup of its own).

**No SSH key with it.** The first time the daemon sees the first-run marker, it wipes what a giver left
in its state directory, `ssh/` (the allowed SSH keys) included. So a key written alongside the marker
would be gone before the first login, and the installer refuses `--ssh-key` with it. For a unit you're
developing on, install without `--for-someone-else` and pass `--wifi` and `--ssh-key`.

## Rehearsing without a unit

`tools/sim-cannoli-adb.py` stands in for adb. It answers the installer's commands from a unit's `m0/`
backup, so a dry run checks, backs up and builds exactly as it would on that unit:

```
TECHO5_SIM_M0=../cannoli-<serial>/m0 TECHO5_SIM_SERIAL=<serial> \
  python3 tools/install-cannoli.py --adb tools/sim-cannoli-adb.py --serial <serial> --dry-run
```

`TECHO5_SIM_STATE` (`rooted`, `stock`, `recovery`, `none`) and `TECHO5_SIM_BUILD` try the other paths.
`tools/test_install_cannoli.py` runs them.

## Going back

- **To Fire OS:** power on without Volume-Up. Slot A is never written by this installer.
- **To TWRP in slot B:** from Fire OS, push `backups/<serial>/twrp-boot_b.img` to `/data/local/tmp/` and
  run `ubiupdatevol /dev/ubi0_<boot_b> /data/local/tmp/twrp-boot_b.img` (`boot_b` is volume 8 on the
  first unit; `cat /sys/class/ubi/ubi0_8/name` says).
- **To stock slot A:** `firebreak restore` with the backup firebreak made (its cannoli guide).
