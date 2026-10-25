#!/bin/busybox.static sh
# Bring the Echo Dot 3 (donut) up, once, from a rootfs slot. Run by busybox init as its sysinit.
#
# The Dot 2's boot.sh (techo5-dot) with the Dot 3's board in place of it: the store is a directory on
# userdata, not the cache partition (32 MB here); Wi-Fi is Amazon's MT7668 SDIO modules and firmware,
# adopted once from Fire OS's system partition, where the Dot 2 has its combo chip and wmtup; the USB
# console has a Linux Foundation id; Bluetooth is not brought up yet.
BB=/bin/busybox.static
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export HOME=/root

MISC=/dev/mmcblk0p8
RECOVERY=/dev/mmcblk0p12 # swdl, which kaeru boots as recovery: this Linux image
DATA=/dev/mmcblk0p16     # userdata: the store, the daemon's state, and the logs
STOREDIR=/data/techo5-store
S=/store/techo5

exec > /dev/kmsg 2>&1

$BB mount -o remount,rw / 2>/dev/null
$BB mount -t proc proc /proc 2>/dev/null
$BB mount -o remount,hidepid=2 /proc 2>/dev/null
$BB mount -t sysfs sysfs /sys 2>/dev/null
$BB mount -t tmpfs -o mode=0755 tmpfs /dev 2>/dev/null
$BB mkdir -p /dev/pts /dev/shm /tmp /data /android /store
$BB mount -t devpts devpts /dev/pts 2>/dev/null
# POSIX shared memory, where nqptp keeps the clock AirPlay 2's receiver reads (feature/streaming).
$BB mount -t tmpfs -o mode=1777,size=1m tmpfs /dev/shm 2>/dev/null
# The initramfs wrote which slot this is into /run before switching; read it before the tmpfs hides it.
SLOT=$($BB cat /run/techo5/slot 2>/dev/null)
$BB mount -t tmpfs tmpfs /run 2>/dev/null
$BB mount -t tmpfs tmpfs /tmp 2>/dev/null
$BB mount -t tmpfs -o mode=0755 tmpfs /var/log 2>/dev/null
$BB mount -t tmpfs tmpfs /var/tmp 2>/dev/null
[ -s /etc/hostname ] && $BB hostname -F /etc/hostname
$BB mkdir -p /run/techo5
[ -n "$SLOT" ] && echo "$SLOT" > /run/techo5/slot

$BB --install -s 2>/dev/null
for t in slotctl wifi-set to-twrp techo5-net; do $BB ln -sfn /usr/local/sbin/$t /usr/sbin/$t; done

# Fire OS's system slot is the active A/B one, which the bootloader names on the command line.
SYSNUM=13
[ "$($BB sed -n 's/.*androidboot.slot_suffix=_\([ab]\).*/\1/p' /proc/cmdline)" = b ] && SYSNUM=14
SYSTEM=/dev/mmcblk0p$SYSNUM
$BB mknod -m 600 $MISC b 179 8 2>/dev/null
$BB mknod -m 600 $RECOVERY b 179 12 2>/dev/null
$BB mknod -m 600 $SYSTEM b 179 $SYSNUM 2>/dev/null
$BB mknod -m 600 $DATA b 179 16 2>/dev/null
$BB mdev -s
echo 7 4 1 7 > /proc/sys/kernel/printk

say() { echo "techo5-donut boot: $*"; }
say "slot $($BB cat /run/techo5/slot 2>/dev/null) release $($BB cat /etc/techo5-release 2>/dev/null)"

# --- Userdata first: the store is on it.
$BB mount -t ext4 -o noatime $DATA /data 2>/dev/null && $BB mkdir -p /data/techo5-linux
say "userdata $([ -d /data/techo5-linux ] && echo mounted || echo unavailable)"
$BB chmod 700 /data/techo5-linux 2>/dev/null
$BB mount --bind $STOREDIR /store 2>/dev/null
say "store $([ -e $S/.techo5-store ] && echo mounted || echo unavailable)"
[ -d /data/techo5-linux ] && $BB mkdir -p /data/misc/echolocal
[ -d /data/misc/echolocal ] && $BB chmod 700 /data/misc/echolocal
if [ -d /data/misc/echolocal ] && { [ ! -e /data/misc/echolocal/timezone ] || [ ! -e /data/misc/echolocal/localtime ]; }; then
	$BB ln -sfn /usr/share/zoneinfo/UTC /data/misc/echolocal/localtime
	$BB rm -f /data/misc/echolocal/timezone /data/misc/echolocal/timezone-set-here
	echo UTC > /data/misc/echolocal/timezone
fi
if [ -d /data/techo5-linux ] && { $BB mount -t pstore pstore /sys/fs/pstore 2>/dev/null || [ -d /sys/fs/pstore ]; }; then
	for f in /sys/fs/pstore/*; do
		[ -e "$f" ] || continue
		$BB mkdir -p /data/techo5-linux/crash
		$BB cp "$f" "/data/techo5-linux/crash/$($BB date +%Y%m%d-%H%M%S)-${f##*/}" 2>/dev/null &&
			say "kernel crash record kept: ${f##*/}" && $BB rm -f "$f"
	done
fi
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

# --- Inbound closed except what the Dot serves, before any network exists.
/usr/local/sbin/techo5-firewall 2>&1 | while read -r l; do say "$l"; done

# --- USB serial console. A Linux Foundation id, not a Google one: Windows binds 18d1:4ee7 to its ADB
# driver and the console never becomes a COM port.
g=/sys/class/android_usb/android0
if [ -d $g ]; then
	echo 1 > /sys/devices/platform/mt_usb/cmode 2>/dev/null
	echo 0 > $g/enable 2>/dev/null
	echo 1d6b > $g/idVendor 2>/dev/null
	echo 0104 > $g/idProduct 2>/dev/null
	echo "TECHO5" > $g/iManufacturer 2>/dev/null
	echo "Echo Dot 3 Linux" > $g/iProduct 2>/dev/null
	sn=$($BB sed -n 's/.*androidboot.serialno=\([^ ]*\).*/\1/p' /proc/cmdline)
	[ -n "$sn" ] && echo "$sn" > $g/iSerial 2>/dev/null
	echo 1 > $g/f_acm/instances 2>/dev/null
	echo acm > $g/functions 2>/dev/null
	echo 02 > $g/bDeviceClass 2>/dev/null
	echo 1 > $g/enable 2>/dev/null
	i=0
	while [ $i -lt 15 ] && [ ! -e /dev/ttyGS0 ]; do $BB mdev -s; $BB sleep 1; i=$((i + 1)); done
	[ -e /dev/ttyGS0 ] || $BB mknod -m 600 /dev/ttyGS0 c 246 0 2>/dev/null
	say "usb console $([ -e /dev/ttyGS0 ] && echo up || echo missing)"
fi

# --- Wi-Fi. Amazon's driver and firmware, the unit's own, adopted once into the store from Fire OS's
# system partition and shared by both slots, so a slot that arrives by update needs nothing from
# Android. They are Amazon's, so never in a published rootfs. The firmware links go in before the first
# insmod, and mdev answers the driver's requests that fall back to the user helper. Never rmmod the
# driver: a reload cannot claim its IRQ and a reset job the unloaded module left behind oopses the
# kernel.
FW=$S/firmware
MODS=$S/modules
if [ -d $S ] && { [ ! -e $FW/WIFI_RAM_CODE_MT7668.bin ] || [ ! -e $MODS/wlan_mt76x8_sdio.ko ]; }; then
	$BB mkdir -p $FW $MODS
	if $BB mount -t ext4 -o ro,noatime $SYSTEM /android 2>/dev/null; then
		$BB cp /android/system/vendor/firmware/* $FW/ 2>/dev/null
		for m in wlan_mt76x8_sdio_prealloc wlan_mt76x8_sdio btmtksdio; do
			$BB cp /android/system/lib/modules/$m.ko $MODS/ 2>/dev/null
		done
		$BB umount /android
		say "Wi-Fi driver and firmware adopted from system_$SYSNUM: $($BB ls $FW $MODS | $BB tr '\n' ' ')"
	else
		say "no system partition to adopt the Wi-Fi driver from"
	fi
	$BB sync
fi
if [ -e $MODS/wlan_mt76x8_sdio.ko ]; then
	echo /sbin/mdev > /proc/sys/kernel/hotplug
	[ -L /etc/firmware ] || $BB rm -rf /etc/firmware
	$BB ln -sfn $FW /etc/firmware
	[ -L /lib/firmware ] || $BB rm -rf /lib/firmware
	$BB ln -sfn $FW /lib/firmware
	[ -L /system ] && $BB rm -f /system
	$BB mkdir -p /system/vendor
	$BB ln -sfn $FW /system/vendor/firmware
	[ -e /vendor/firmware ] || $BB ln -sfn /system/vendor /vendor
	# Loaded by hand at about 15 s of uptime on the first tries; kept there.
	while [ "$($BB cut -d. -f1 /proc/uptime)" -lt 15 ]; do $BB sleep 1; done
	$BB insmod $MODS/wlan_mt76x8_sdio_prealloc.ko && $BB insmod $MODS/wlan_mt76x8_sdio.ko
	i=0
	while [ $i -lt 30 ] && [ ! -e /sys/class/net/wlan0 ]; do $BB sleep 1; i=$((i + 1)); done
	say "wlan0 $([ -e /sys/class/net/wlan0 ] && echo up || echo missing) after ${i}s"
else
	say "no Wi-Fi driver: no Wi-Fi"
fi

# --- The network: the one set with wifi-set, or the one Fire OS saved (usr/local/sbin/techo5-net).
IP=$(/usr/local/sbin/techo5-net)
say "address ${IP:-none}"

# --- SSH belongs to the daemon (feature/security); here it only gets its places on userdata.
KEYS=/data/misc/echolocal/ssh
HOSTKEYS=/data/techo5-linux/dropbear
if [ -d /data/techo5-linux ]; then
	$BB mkdir -p $KEYS $HOSTKEYS
	$BB chmod 700 $KEYS $HOSTKEYS
	[ -L /root/.ssh ] || $BB rm -rf /root/.ssh
	$BB ln -sfn $KEYS /root/.ssh
	[ -L /etc/dropbear ] || $BB rm -rf /etc/dropbear
	$BB ln -sfn $HOSTKEYS /etc/dropbear
fi

# The clock starts in 1970: several time servers, the home's own first, in the background (as on the
# Dot 2, techo5#77), then ntpd keeps running.
gw=$($BB route -n 2>/dev/null | $BB awk '$1 == "0.0.0.0" { print $2; exit }')
own=$($BB head -n 1 /data/misc/echolocal/ntp-server 2>/dev/null | $BB tr -cd 'A-Za-z0-9.:-')
( n=0
	until $BB timeout -s KILL 40 $BB ntpd -n -q ${own:+-p "$own"} -p time.cloudflare.com -p time.google.com -p 0.pool.ntp.org -p 1.pool.ntp.org ${gw:+-p "$gw"} > /tmp/ntpd.log 2>&1; do
		n=$((n + 1))
		[ $n -ge 30 ] && { echo "techo5-donut boot: clock not set yet, no time server answered; ntpd keeps trying" > /dev/kmsg; break; }
		$BB sleep 60
	done
	if [ $n -lt 30 ]; then
		$BB hwclock -w -u 2>/dev/null
		: > /run/techo5/ntp-set
		echo "techo5-donut boot: clock $($BB date)" > /dev/kmsg
	fi
	exec $BB ntpd -n -S /etc/techo5/ntp-event.sh ${own:+-p "$own"} -p time.cloudflare.com -p time.google.com -p 0.pool.ntp.org -p 1.pool.ntp.org ${gw:+-p "$gw"} > /dev/null 2>&1 ) &

# --- The services' own users (as on the Dot 2).
$BB grep -q '^messagebus:' /etc/group || echo 'messagebus:x:101:messagebus' >> /etc/group
$BB grep -q '^messagebus:' /etc/passwd || echo 'messagebus:x:100:101:messagebus:/run/dbus:/sbin/nologin' >> /etc/passwd
$BB grep -q '^bluealsa:' /etc/passwd || echo 'bluealsa:x:120:18:bluealsa:/var/lib/bluealsa:/sbin/nologin' >> /etc/passwd
$BB grep -q '^avahi:' /etc/group || echo 'avahi:x:86:' >> /etc/group
$BB grep -q '^avahi:' /etc/passwd || echo 'avahi:x:86:86:avahi:/var/lib/avahi:/sbin/nologin' >> /etc/passwd
$BB grep -q '^streaming:' /etc/group || echo 'streaming:x:88:' >> /etc/group
$BB grep -q '^streaming:' /etc/passwd || echo 'streaming:x:88:88:streaming:/var/empty:/sbin/nologin' >> /etc/passwd
$BB grep -q '^inet:' /etc/group || echo 'inet:x:3003:' >> /etc/group
for u in avahi streaming; do
	$BB grep -Eq "^inet:.*[:,]$u(,|\$)" /etc/group || $BB sed -i -E "/^inet:/{s/:\$/:$u/;t;s/\$/,$u/}" /etc/group
done

# --- Bluetooth's radio: Amazon's btmtksdio, after Wi-Fi as Fire OS loads it. It makes /dev/stpbt (H4).
# The stock kernel has no Bluetooth stack, so ours comes as modules built for it (tools/linux/
# build-bt-modules.sh): with them btbridge turns /dev/stpbt into hci0 through /dev/vhci, and BlueZ,
# bluez-alsa and echod's BLE proxy run on hci0 as on the Dot 2 (usr/local/sbin/techo5-bt). Without
# them echod's BLE proxy reads /dev/stpbt itself.
if [ -e $MODS/btmtksdio.ko ] && [ -e /sys/class/net/wlan0 ]; then
	$BB insmod $MODS/btmtksdio.ko 2>/dev/null
	i=0
	while [ $i -lt 10 ] && [ ! -e /dev/stpbt ]; do $BB mdev -s; $BB sleep 1; i=$((i + 1)); done
	say "bluetooth radio $([ -e /dev/stpbt ] && echo up || echo missing)"
	if [ -e /dev/stpbt ]; then
		for m in cmac bluetooth hci_vhci; do
			$BB insmod /usr/lib/techo5/modules/$m.ko 2>/dev/null || say "bluetooth: $m.ko did not load"
		done
		$BB mdev -s
		if [ -e /dev/vhci ]; then
			# In the background: the daemon picks BlueZ and bluez-alsa up whenever they arrive. But hci0
			# itself comes first: this /dev/stpbt takes a second opener, and a BLE proxy that opened it
			# before btbridge did would split the controller's replies with it.
			/usr/local/sbin/techo5-bt > /dev/null 2>&1 &
			i=0
			while [ $i -lt 20 ] && [ ! -d /sys/class/bluetooth/hci0 ]; do $BB sleep 1; i=$((i + 1)); done
			say "bluetooth hci0 $([ -d /sys/class/bluetooth/hci0 ] && echo up || echo missing)"
		fi
	fi
fi

# --- Everything the daemon needs is there: let it start (techo5-run waits on this).
$BB touch /run/techo5/ready
say "ready at $($BB date)"

# A boot is healthy once the daemon has stayed up for a while (as on the Dot 2): then the slot is
# committed and the try count in MISC goes back to zero.
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
		echo "techo5-donut boot: healthy; slot committed, try count reset" > /dev/kmsg
	else
		case "$($BB cat $S/slots/$SLOT.state 2>/dev/null)" in
		trial*)
			echo "techo5-donut boot: slot $SLOT on trial never became healthy; rebooting to spend a try" > /dev/kmsg
			$BB sync
			$BB reboot
			;;
		*)
			echo "techo5-donut boot: daemon not staying up; this boot counts as a failed try" > /dev/kmsg
			;;
		esac
	fi
) &
