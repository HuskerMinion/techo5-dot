#!/bin/busybox.static sh
# Bring the Echo Dot up, once, from a rootfs slot. Run by busybox init as its sysinit.
#
# The same ground the initramfs covers — no devtmpfs, so /dev is made by hand; the USB gadget wants
# its function count and device class; the combo chip wants its patch searches answered — with one
# difference: this is the real root, so the daemon lives here rather than being fetched from wherever
# it happened to be.
BB=/bin/busybox.static
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export HOME=/root

MISC=/dev/mmcblk0p8
RECOVERY=/dev/mmcblk0p12
SYSTEM=/dev/mmcblk0p14   # Android's system: the Wi-Fi firmware is adopted from it, once
STORE=/dev/mmcblk0p15    # cache: the slot store, /techo5 on it
DATA=/dev/mmcblk0p16     # userdata: the daemon's state, and the logs
S=/store/techo5

exec > /dev/kmsg 2>&1

# The slot has to be writable: it keeps state. After a run of watchdog resets one boot came up with it
# read-only and no filesystem error recorded (errors_count 0); what did that is still open, so this
# says what it must be rather than trusting it.
$BB mount -o remount,rw / 2>/dev/null

$BB mount -t proc proc /proc 2>/dev/null
# Another user's processes, and their command lines, are root's to see: the services that drop root
# (avahi, the AirPlay and Spotify Connect receivers) see only their own. As on TECHO5's images.
$BB mount -o remount,hidepid=2 /proc 2>/dev/null
$BB mount -t sysfs sysfs /sys 2>/dev/null
$BB mount -t tmpfs -o mode=0755 tmpfs /dev 2>/dev/null
$BB mkdir -p /dev/pts /dev/shm /tmp /data /android /store
$BB mount -t devpts devpts /dev/pts 2>/dev/null
# POSIX shared memory, where nqptp keeps the clock AirPlay 2's receiver reads (feature/streaming).
$BB mount -t tmpfs -o mode=1777,size=1m tmpfs /dev/shm 2>/dev/null
# The initramfs wrote which slot this is into /run before switching; a tmpfs over /run would hide it,
# so it is read first and put back after.
SLOT=$($BB cat /run/techo5/slot 2>/dev/null)
$BB mount -t tmpfs tmpfs /run 2>/dev/null
$BB mount -t tmpfs tmpfs /tmp 2>/dev/null
# Logs that matter go to userdata (/data/techo5-linux); what services write to /var/log and /var/tmp
# lives in RAM rather than wearing the slot's flash.
$BB mount -t tmpfs -o mode=0755 tmpfs /var/log 2>/dev/null
$BB mount -t tmpfs tmpfs /var/tmp 2>/dev/null
[ -s /etc/hostname ] && $BB hostname -F /etc/hostname
# After /run is mounted, not before: a directory made first is hidden by the tmpfs, and then the
# marker the daemon waits for can never be written. The first slot boot waited on it for ever.
$BB mkdir -p /run/techo5
[ -n "$SLOT" ] && echo "$SLOT" > /run/techo5/slot

# Every command by name, not only through $BB. The unpacked slot can arrive without Alpine's /bin
# applet links — the first one did, and a bare `cat` writing the DHCP lease script then failed, so a
# lease was obtained and thrown away. Installing them here costs nothing when they are already there.
$BB --install -s 2>/dev/null
# SSH sessions get dropbear's own PATH, which has no /usr/local: the unit's tools are linked where it looks.
for t in slotctl wifi-set to-twrp techo5-net; do $BB ln -sfn /usr/local/sbin/$t /usr/sbin/$t; done
$BB mknod -m 600 $MISC b 179 8 2>/dev/null
$BB mknod -m 600 $RECOVERY b 179 12 2>/dev/null
$BB mknod -m 600 $SYSTEM b 179 14 2>/dev/null
$BB mknod -m 600 $STORE b 179 15 2>/dev/null
$BB mknod -m 600 $DATA b 179 16 2>/dev/null
$BB mdev -s
echo 7 4 1 7 > /proc/sys/kernel/printk

say() { echo "techo5-dot boot: $*"; }
say "slot $($BB cat /run/techo5/slot 2>/dev/null) release $($BB cat /etc/techo5-release 2>/dev/null)"

# The same image runs on the Echo 2nd gen (radar), a biscuit with a bigger speaker. Amazon's device type
# id tells them apart; the idme driver ends it with a NUL, and Linux 6.1 (LibreEcho's) has it in the
# device tree instead. Anything else is taken for a biscuit.
BOARD=biscuit
[ "$($BB cat /proc/idme/device_type_id /proc/device-tree/idme/device_type_id/value 2>/dev/null | $BB tr -d '\000\n')" = A7WXQPH584YP ] && BOARD=radar
say "board $BOARD"

# The bootloader message is the initramfs's to manage, and it has already asked for the next boot to
# be Linux too (tools/linux/init). This script used to clear it, which silently undid that: a power
# cut then came back in Android.

# The store, which the initramfs mounted for itself and which does not survive the switch into the
# slot. The slot is a directory on the same filesystem, so this is a second mount of one filesystem:
# updates unpack into the other slot through it, and the slot state lives there.
$BB mount -t ext4 -o noatime $STORE /store 2>/dev/null
say "store $([ -e $S/.techo5-store ] && echo mounted || echo unavailable)"

$BB mount -t ext4 -o noatime $DATA /data 2>/dev/null && $BB mkdir -p /data/techo5-linux
say "userdata $([ -d /data/techo5-linux ] && echo mounted || echo unavailable)"
# The daemon's state (the Home Assistant key and token, SSH keys) and the logs are root's alone.
# EchoLocal's install left the state directory writable by everyone.
$BB chmod 700 /data/techo5-linux 2>/dev/null
# The installer makes it; made here too, so a unit without one still finds the zone files below.
[ -d /data/techo5-linux ] && $BB mkdir -p /data/misc/echolocal
[ -d /data/misc/echolocal ] && $BB chmod 700 /data/misc/echolocal
# The clock's zone is the unit's, not the image's: /etc/localtime and /etc/timezone link (mkrootfs.py)
# to the files echod writes there from Home Assistant or the setup page (feature/timezone). A unit
# that has none yet starts on UTC.
if [ -d /data/misc/echolocal ] && { [ ! -e /data/misc/echolocal/timezone ] || [ ! -e /data/misc/echolocal/localtime ]; }; then
	$BB ln -sfn /usr/share/zoneinfo/UTC /data/misc/echolocal/localtime
	# Removed first: written through, a link left there would put the write somewhere else. A zone
	# chosen on the device that is gone is forgotten too, so Home Assistant's applies again.
	$BB rm -f /data/misc/echolocal/timezone /data/misc/echolocal/timezone-set-here
	echo UTC > /data/misc/echolocal/timezone
fi
# The kernel keeps its last words in RAM across a restart (pstore), where it has it. Mounting it
# makes them readable, and a copy on userdata outlives the next boot; a power cut clears the RAM, so
# this only catches a device that restarted itself.
if [ -d /data/techo5-linux ] && { $BB mount -t pstore pstore /sys/fs/pstore 2>/dev/null || [ -d /sys/fs/pstore ]; }; then
	for f in /sys/fs/pstore/*; do
		[ -e "$f" ] || continue
		$BB mkdir -p /data/techo5-linux/crash
		$BB cp "$f" "/data/techo5-linux/crash/$($BB date +%Y%m%d-%H%M%S)-${f##*/}" 2>/dev/null &&
			say "kernel crash record kept: ${f##*/}" && $BB rm -f "$f"
	done
fi
# The wake word models the image carries, any this unit does not have yet: the installer copied
# a set once, and a model added to the image since reaches the unit at its next update, so every
# unit offers the same words. Nothing on the unit is replaced or removed.
if [ -d /usr/share/techo5/models ] && [ -d /data/misc/echolocal ]; then
	$BB mkdir -p /data/misc/echolocal/models
	n=0
	for f in /usr/share/techo5/models/*; do
		[ -e "/data/misc/echolocal/models/${f##*/}" ] && continue
		$BB cp "$f" /data/misc/echolocal/models/ && n=$((n + 1))
	done
	[ $n -gt 0 ] && say "wake word models: $n files added from the image"
fi

# --- The loopback, which nothing else brings up: AirPlay 2's receiver and nqptp talk over it (localhost:9000).
# Without it the daemon keeps the classic AirPlay receiver (feature/streaming).
$BB ip link set lo up 2>/dev/null

# --- Inbound closed except what the Dot serves, before any network exists (usr/local/sbin/techo5-firewall).
/usr/local/sbin/techo5-firewall 2>&1 | while read -r l; do say "$l"; done

# --- USB serial console.
g=/sys/class/android_usb/android0
if [ -d $g ]; then
	echo 1 > /sys/devices/platform/mt_usb/cmode 2>/dev/null
	echo 0 > $g/enable 2>/dev/null
	echo 18d1 > $g/idVendor 2>/dev/null
	echo 4ee7 > $g/idProduct 2>/dev/null
	echo "TECHO5" > $g/iManufacturer 2>/dev/null
	if [ $BOARD = radar ]; then echo "Echo 2 Linux"; else echo "Echo Dot 2 Linux"; fi > $g/iProduct 2>/dev/null
	# The unit's own serial, so the PC tells Dots apart: the installer finds this unit's COM port by it.
	sn=$($BB sed -n 's/.*androidboot.serialno=\([^ ]*\).*/\1/p' /proc/cmdline)
	[ -n "$sn" ] && echo "$sn" > $g/iSerial 2>/dev/null
	echo 1 > $g/f_acm/instances 2>/dev/null
	echo acm > $g/functions 2>/dev/null
	echo 02 > $g/bDeviceClass 2>/dev/null
	echo 1 > $g/enable 2>/dev/null
	i=0
	while [ $i -lt 15 ] && [ ! -e /dev/ttyGS0 ]; do $BB mdev -s; $BB sleep 1; i=$((i + 1)); done
	say "usb console $([ -e /dev/ttyGS0 ] && echo up || echo missing)"
fi

# --- The unit's audio coefficients: the speaker's tuning (lib/asp) and the microphones' beamformer
# (lib/subband), which Fire OS keeps beside the firmware and which nothing carried across until now.
# Without them the speaker plays untuned, the Speaker EQ switch cannot turn on because there is no
# chain for it to enable, and the seven microphones run without the vendor's beamforming. Amazon's,
# so never in a published rootfs: every unit takes its own, exactly as it does its firmware.
adopt_audio() {
	[ -e $AA/MBCL.cfg ] && return 0
	$BB mkdir -p $AA
	if [ -d /etc/audio-algorithms ] && [ ! -L /etc/audio-algorithms ]; then
		$BB cp /etc/audio-algorithms/* $AA/ 2>/dev/null
		say "audio coefficients taken from this slot into $AA"
	elif $BB mount -t ext4 -o ro,noatime $SYSTEM /android 2>/dev/null; then
		$BB cp /android/system/vendor/etc/audio-algorithms/* $AA/ 2>/dev/null
		$BB umount /android
		say "audio coefficients adopted from the system partition: $($BB ls $AA 2>/dev/null | $BB wc -l) files"
	else
		say "no audio coefficients to adopt: the speaker plays untuned"
	fi
	$BB sync
}

# --- The codec paths Fire OS sets for each output, /system/etc/audio_device.xml. The Echo 2nd gen's
# speaker EQ (the biquad coefficients of its ext_speaker_output path) is in it, and without them its
# speaker is silent; echod reads them from the store. Amazon's too, so adopted the same way, once. It is
# checked by its own presence, not MBCL.cfg's: a unit that adopted the coefficients above before this
# existed has those and not this. A biscuit takes its copy as well; nothing there needs it.
adopt_audio_device() {
	[ -d $S ] || return 0
	{ [ -e $S/audio_device.xml ] || [ -e $S/audio_device.xml.missing ]; } && return 0
	if $BB mount -t ext4 -o ro,noatime $SYSTEM /android 2>/dev/null; then
		if $BB cp /android/system/etc/audio_device.xml $S/audio_device.xml.new 2>/dev/null; then
			$BB mv $S/audio_device.xml.new $S/audio_device.xml
			say "audio_device.xml adopted from the system partition"
		else
			# Not looked for again on every boot: the system partition is not mounted for nothing.
			$BB rm -f $S/audio_device.xml.new
			: > $S/audio_device.xml.missing
			say "no audio_device.xml on the system partition"
		fi
		$BB umount /android
		$BB sync
	else
		say "no system partition to adopt audio_device.xml from"
	fi
}

# --- Wi-Fi firmware. The unit's own, adopted once into the store and shared by both slots, so a slot
# that arrives by update needs nothing from Android. It is Amazon's, so it is never in a published
# rootfs — every unit takes it from itself: from a slot that adopted it before the store kept it, or
# from Android's system partition, which is then never mounted again.
FW=$S/firmware
AA=$S/audio-algorithms
[ -d $S ] || FW=/etc/firmware.slot
[ -d $S ] || AA=/etc/audio-algorithms.slot
if [ ! -e $FW/WIFI_RAM_CODE_8163 ]; then
	$BB mkdir -p $FW
	if [ -e /etc/firmware/WIFI_RAM_CODE_8163 ] && [ ! -L /etc/firmware ]; then
		$BB cp /etc/firmware/* $FW/ 2>/dev/null
		say "firmware taken from this slot into $FW"
	elif $BB mount -t ext4 -o ro,noatime $SYSTEM /android 2>/dev/null; then
		$BB cp /android/system/vendor/firmware/* $FW/ 2>/dev/null
		$BB umount /android
		say "firmware adopted from the system partition into $FW: $($BB ls $FW | $BB tr '\n' ' ')"
	else
		say "no firmware anywhere to adopt"
	fi
	$BB sync
fi
adopt_audio_device
if [ -e $FW/WIFI_RAM_CODE_8163 ]; then
	[ -L /etc/firmware ] || $BB rm -rf /etc/firmware
	$BB ln -sfn $FW /etc/firmware
	# The Linux 6.1 kernel's connectivity driver (the Echo 2nd gen's, from LibreEcho) reads WMT_SOC.cfg and
	# the Wi-Fi image from /lib/firmware: without them its /dev/stpwmt refuses wmtup's first open with
	# EPERM. Links to the unit's copy, beside whatever else the slot keeps there; harmless on 3.18.
	$BB mkdir -p /lib/firmware
	for f in $FW/*; do [ -e /lib/firmware/${f##*/} ] || $BB ln -s $f /lib/firmware/; done
	# The driver's module init reads WMT_SOC.cfg from a path compiled into the kernel,
	# /system/vendor/firmware/, whatever anyone tells it. Missing, wmt_lib_init fails and the kernel's
	# own cleanup of that failure dereferences NULL — a panic and a watchdog reset, which is how this
	# was found. So that path exists here, and it is the unit's firmware.
	[ -L /system ] && $BB rm -f /system
	$BB mkdir -p /system/vendor
	$BB ln -sfn $FW /system/vendor/firmware
	# And the Wi-Fi driver looks for WIFI_RAM_CODE_8163 under /vendor/firmware, a second path of its own.
	# Without it the chip powers on and wlanProbe fails with "Open FW image: WIFI_RAM_CODE failed" — the
	# write to /dev/wmtWifi returns EIO a second later and nothing else says why.
	[ -e /vendor/firmware/WIFI_RAM_CODE_8163 ] || $BB ln -sfn /system/vendor /vendor
	adopt_audio
	if [ -e $AA/MBCL.cfg ]; then
		# lib/asp and lib/subband read /vendor/etc/audio-algorithms. /vendor is usually the symlink to
		# /system/vendor made just above; where it is a directory of its own, the link goes in it.
		$BB mkdir -p /system/vendor/etc
		$BB ln -sfn $AA /system/vendor/etc/audio-algorithms
		if [ ! -L /vendor ]; then
			$BB mkdir -p /vendor/etc
			$BB ln -sfn $AA /vendor/etc/audio-algorithms
		fi
		say "audio coefficients at /vendor/etc/audio-algorithms"
	fi
	/usr/local/bin/wmtup -patches $FW/ -power >> /data/techo5-linux/wmtup.log 2>&1 &
	i=0
	while [ $i -lt 45 ] && [ ! -e /sys/class/net/wlan0 ]; do $BB sleep 1; i=$((i + 1)); done
	say "wlan0 $([ -e /sys/class/net/wlan0 ] && echo up || echo missing) after ${i}s"
else
	say "no Wi-Fi firmware: no Wi-Fi"
fi

# --- The network: the one set with wifi-set, or the one Fire OS saved (usr/local/sbin/techo5-net).
IP=$(/usr/local/sbin/techo5-net)
say "address ${IP:-none}"

# --- SSH belongs to the daemon (TECHO5 feature/security): it starts dropbear only while the SSH
# switch in Home Assistant is on, with keys Home Assistant sent over its encrypted link. Here it only
# gets its places on userdata, which outlast slots and updates: the keys in the daemon's state
# directory, the host keys (dropbear -R) in /data/techo5-linux/dropbear.
KEYS=/data/misc/echolocal/ssh
HOSTKEYS=/data/techo5-linux/dropbear
if [ -d /data/techo5-linux ]; then
	$BB mkdir -p $KEYS $HOSTKEYS
	$BB chmod 700 $KEYS $HOSTKEYS
	[ -L /root/.ssh ] || $BB rm -rf /root/.ssh
	$BB ln -sfn $KEYS /root/.ssh
	[ -L /etc/dropbear ] || $BB rm -rf /etc/dropbear
	$BB ln -sfn $HOSTKEYS /etc/dropbear
	# Once, from before the daemon ran SSH: the key and host key boot.sh used, and the switch left on so
	# the unit stays reachable the way it was (a new install starts with it off).
	OLD=/data/techo5-linux/ssh
	if [ -s $OLD/authorized_keys ] && [ ! -e $KEYS/authorized_keys ]; then
		$BB cp $OLD/authorized_keys $KEYS/authorized_keys
		$BB chmod 600 $KEYS/authorized_keys
		[ -s $OLD/dropbear_ed25519_host_key ] && [ ! -e $HOSTKEYS/dropbear_ed25519_host_key ] &&
			$BB cp $OLD/dropbear_ed25519_host_key $HOSTKEYS/dropbear_ed25519_host_key
		STATE=/data/misc/echolocal/state.json
		if [ -s $STATE ] && ! $BB grep -q '"security"' $STATE && [ "$($BB head -1 $STATE)" = "{" ]; then
			$BB sed -i '1s/^{$/{\n  "security": {"ssh": true},/' $STATE
		fi
		$BB mv $OLD $OLD.migrated
		$BB sync
		say "ssh keys moved to $KEYS; the SSH switch stays on"
	fi
fi

# The clock starts in 1970 and the daemon's TLS and logs both care. Several servers by name, now that DHCP
# has written a resolver, with the gateway as one more for networks that serve time locally: busybox ntpd
# takes one address for each name, so pool.ntp.org alone is one pool member, and one that never answers
# left the clock unset (techo5#77). Bounded, and in the background, so an unreachable server never holds
# the boot; tried again each minute for half an hour if none answers. Then the RTC, in UTC, which is how
# the kernel reads it at boot, so the next boot starts closer. After that ntpd keeps running, as on
# TECHO5's images, so a Dot that stays up for months stays on time, and one whose first half hour had
# no time server gets its clock when one answers.
gw=$($BB route -n 2>/dev/null | $BB awk '$1 == "0.0.0.0" { print $2; exit }')
# The home's own time server, set on the setup page (echod feature/timezone), is asked first.
own=$($BB head -n 1 /data/misc/echolocal/ntp-server 2>/dev/null | $BB tr -cd 'A-Za-z0-9.:-')
( n=0
	until $BB timeout -s KILL 40 $BB ntpd -n -q ${own:+-p "$own"} -p time.cloudflare.com -p time.google.com -p 0.pool.ntp.org -p 1.pool.ntp.org ${gw:+-p "$gw"} > /tmp/ntpd.log 2>&1; do
		n=$((n + 1))
		[ $n -ge 30 ] && { echo "techo5-dot boot: clock not set yet, no time server answered; ntpd keeps trying" > /dev/kmsg; break; }
		$BB sleep 60
	done
	if [ $n -lt 30 ]; then
		$BB hwclock -w -u 2>/dev/null
		: > /run/techo5/ntp-set # echod: NTP has the clock, Home Assistant's time leaves it alone
		echo "techo5-dot boot: clock $($BB date)" > /dev/kmsg
	fi
	exec $BB ntpd -n -S /etc/techo5/ntp-event.sh ${own:+-p "$own"} -p time.cloudflare.com -p time.google.com -p 0.pool.ntp.org -p 1.pool.ntp.org ${gw:+-p "$gw"} > /dev/null 2>&1 ) &

# --- The services' own users, which their packages' install scripts would have made. Before the system
# bus starts, whoever starts it (techo5-bt, or the daemon for AirPlay and Spotify Connect): a bus policy
# naming a user that does not exist yet is dropped as the bus loads it.
$BB grep -q '^messagebus:' /etc/group || echo 'messagebus:x:101:messagebus' >> /etc/group
$BB grep -q '^messagebus:' /etc/passwd || echo 'messagebus:x:100:101:messagebus:/run/dbus:/sbin/nologin' >> /etc/passwd
$BB grep -q '^bluealsa:' /etc/passwd || echo 'bluealsa:x:120:18:bluealsa:/var/lib/bluealsa:/sbin/nologin' >> /etc/passwd
$BB grep -q '^avahi:' /etc/group || echo 'avahi:x:86:' >> /etc/group
$BB grep -q '^avahi:' /etc/passwd || echo 'avahi:x:86:86:avahi:/var/lib/avahi:/sbin/nologin' >> /etc/passwd
# avahi and the receivers (AirPlay, Spotify Connect; feature/streaming): these Android kernels give a
# network socket only to a member of the inet group (3003). avahi drops root for its own user; the
# receivers run as their own, streaming, which owns nothing else. (build-dot-rootfs.py leaves out avahi's SSH
# service files.)
$BB grep -q '^streaming:' /etc/group || echo 'streaming:x:88:' >> /etc/group
$BB grep -q '^streaming:' /etc/passwd || echo 'streaming:x:88:88:streaming:/var/empty:/sbin/nologin' >> /etc/passwd
$BB grep -q '^inet:' /etc/group || echo 'inet:x:3003:' >> /etc/group
for u in avahi streaming; do
	$BB grep -Eq "^inet:.*[:,]$u(,|\$)" /etc/group || $BB sed -i -E "/^inet:/{s/:\$/:$u/;t;s/\$/,$u/}" /etc/group
done

# --- Bluetooth, in the background: the daemon does not wait for it (usr/local/sbin/techo5-bt).
/usr/local/sbin/techo5-bt > /dev/null 2>&1 &

# --- Everything the daemon needs is there: let it start (techo5-run waits on this).
$BB touch /run/techo5/ready
say "ready at $($BB date)"

# A boot is healthy once the daemon has stayed up for a while. Then the slot is committed (a slot fresh
# from an update stops being on trial) and the try count the initramfs keeps in MISC goes back to zero
# (tools/linux/init). A boot that never gets here spends one of the tries of both.
#
# The daemon has to have stayed up, not merely be up: init restarts one that crashes, so it is checked
# twice, 30 s apart, as the same process. A slot on trial that is not healthy by three minutes reboots,
# so its next try is spent and a broken update falls back to the good slot without anybody pulling
# the plug. A committed slot never reboots itself; its tries are spent only by real reboots.
(
	$BB sleep 60
	healthy=
	n=0
	while [ $n -lt 5 ]; do
		p1=$($BB pidof echod)
		$BB sleep 30
		p2=$($BB pidof echod)
		if [ -n "$p1" ] && [ "$p1" = "$p2" ]; then healthy=1; break; fi
		n=$((n + 1))
	done
	if [ -n "$healthy" ]; then
		/usr/local/sbin/slotctl commit > /dev/kmsg 2>&1
		printf 'TECHO5-TRIES 0 healthy\n' | $BB dd of=$MISC bs=512 seek=14 count=1 conv=notrunc,sync 2>/dev/null
		echo "techo5-dot boot: healthy; slot committed, try count reset" > /dev/kmsg
	else
		case "$($BB cat $S/slots/$SLOT.state 2>/dev/null)" in
		trial*)
			echo "techo5-dot boot: slot $SLOT on trial never became healthy; rebooting to spend a try" > /dev/kmsg
			$BB sync
			$BB reboot
			;;
		*)
			echo "techo5-dot boot: daemon not staying up; this boot counts as a failed try" > /dev/kmsg
			;;
		esac
	fi
) &
