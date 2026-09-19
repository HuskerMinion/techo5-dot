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
$BB mount -t sysfs sysfs /sys 2>/dev/null
$BB mount -t tmpfs -o mode=0755 tmpfs /dev 2>/dev/null
$BB mkdir -p /dev/pts /tmp /data /android /store
$BB mount -t devpts devpts /dev/pts 2>/dev/null
# The initramfs wrote which slot this is into /run before switching; a tmpfs over /run would hide it,
# so it is read first and put back after.
SLOT=$($BB cat /run/techo5/slot 2>/dev/null)
$BB mount -t tmpfs tmpfs /run 2>/dev/null
$BB mount -t tmpfs tmpfs /tmp 2>/dev/null
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
[ -d /data/misc/echolocal ] && $BB chmod 700 /data/misc/echolocal
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
	echo "Echo Dot 2 Linux" > $g/iProduct 2>/dev/null
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

# --- Wi-Fi firmware. The unit's own, adopted once into the store and shared by both slots, so a slot
# that arrives by update needs nothing from Android. It is Amazon's, so it is never in a published
# rootfs — every unit takes it from itself: from a slot that adopted it before the store kept it, or
# from Android's system partition, which is then never mounted again.
FW=$S/firmware
[ -d $S ] || FW=/etc/firmware.slot
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
if [ -e $FW/WIFI_RAM_CODE_8163 ]; then
	[ -L /etc/firmware ] || $BB rm -rf /etc/firmware
	$BB ln -sfn $FW /etc/firmware
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

# The clock starts in 1970 and the daemon's TLS and logs both care. pool.ntp.org by name, now that DHCP
# has written a resolver, with the gateway as a second source for networks that serve time locally.
# Bounded, and in the background, so an unreachable server never holds the boot. Then the RTC, so the
# next boot starts closer.
gw=$($BB route -n 2>/dev/null | $BB awk '$1 == "0.0.0.0" { print $2; exit }')
( $BB timeout -s KILL 40 $BB ntpd -n -q -p pool.ntp.org ${gw:+-p "$gw"} > /tmp/ntpd.log 2>&1 &&
	$BB hwclock -w 2>/dev/null
	echo "techo5-dot boot: clock $($BB date)" > /dev/kmsg ) &

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
