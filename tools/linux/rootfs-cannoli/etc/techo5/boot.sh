#!/bin/busybox.static sh
# Bring the Echo Dot 5th gen (cannoli) up, once, from a rootfs slot. Run by busybox init as its sysinit.
#
# The Dot 3's boot.sh (rootfs-donut) with the Dot 5's board in place of it: storage is raw NAND under
# UBI, so userdata is the UBIFS volume ubi0:userdata, which the ramdisk (init-cannoli) mounted and moved
# in here with the store bound at /store; there is no MISC to write breadcrumbs or a try count to, so
# the count is a file in the store. The ring, amplifier and microphones are already up: the ramdisk
# loaded cannoli_hwfixup before switching. Wi-Fi and Bluetooth are the unit's own MediaTek modules and
# firmware, which the ramdisk adopted into the store from itself (it is built from the unit's backup);
# they are never in a published rootfs. The USB console is the configfs gadget, the UART a getty of its
# own (etc/inittab).
BB=/bin/busybox.static
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export HOME=/root

STOREDIR=/data/techo5-store
S=/store/techo5

exec > /dev/kmsg 2>&1

$BB mount -o remount,rw / 2>/dev/null
mounted() { $BB grep -q " $1 " /proc/mounts; }
mounted /proc || $BB mount -t proc proc /proc
$BB mount -o remount,hidepid=2 /proc 2>/dev/null
mounted /sys || $BB mount -t sysfs sysfs /sys
mounted /dev || $BB mount -t devtmpfs devtmpfs /dev 2>/dev/null || $BB mount -t tmpfs -o mode=0755 tmpfs /dev
$BB mkdir -p /dev/pts /dev/shm /tmp /data /store /config
mounted /dev/pts || $BB mount -t devpts devpts /dev/pts
# POSIX shared memory, where nqptp keeps the clock AirPlay 2's receiver reads (feature/streaming).
$BB mount -t tmpfs -o mode=1777,size=1m tmpfs /dev/shm 2>/dev/null
mounted /config || $BB mount -t configfs configfs /config 2>/dev/null
# The ramdisk wrote which slot this is into /run before switching; read it before the tmpfs hides it.
SLOT=$($BB cat /run/techo5/slot 2>/dev/null)
$BB mount -t tmpfs tmpfs /run 2>/dev/null
$BB mount -t tmpfs tmpfs /tmp 2>/dev/null
$BB mount -t tmpfs -o mode=0755 tmpfs /var/log 2>/dev/null
$BB mount -t tmpfs tmpfs /var/tmp 2>/dev/null
[ -s /etc/hostname ] && $BB hostname -F /etc/hostname
$BB mkdir -p /run/techo5
[ -n "$SLOT" ] && echo "$SLOT" > /run/techo5/slot

$BB --install -s 2>/dev/null
for t in slotctl wifi-set to-fireos techo5-net techo5-wlan-mac; do $BB ln -sfn /usr/local/sbin/$t /usr/sbin/$t; done
$BB mdev -s
echo 0 > /sys/module/printk/parameters/disable_uart 2>/dev/null
echo 7 4 1 7 > /proc/sys/kernel/printk

say() { echo "techo5-cannoli boot: $*"; }
say "slot $SLOT release $($BB cat /etc/techo5-release 2>/dev/null)"

# --- Userdata and the store: moved in by the ramdisk; mounted here only if they were not.
mounted /data || $BB mount -t ubifs -o noatime ubi0:userdata /data 2>/dev/null
mounted /data && $BB mkdir -p /data/techo5-linux
say "userdata $([ -d /data/techo5-linux ] && echo mounted || echo unavailable)"
$BB chmod 700 /data/techo5-linux 2>/dev/null
mounted /store || $BB mount --bind $STOREDIR /store 2>/dev/null
say "store $([ -e $S/.techo5-store ] && echo mounted || echo unavailable)"
[ -d /data/techo5-linux ] && $BB mkdir -p /data/misc/echolocal
[ -d /data/misc/echolocal ] && $BB chmod 700 /data/misc/echolocal
if [ -d /data/misc/echolocal ] && { [ ! -e /data/misc/echolocal/timezone ] || [ ! -e /data/misc/echolocal/localtime ]; }; then
	$BB ln -sfn /usr/share/zoneinfo/UTC /data/misc/echolocal/localtime
	$BB rm -f /data/misc/echolocal/timezone /data/misc/echolocal/timezone-set-here
	echo UTC > /data/misc/echolocal/timezone
fi
# Why this boot happened, for Last boot reason (echod's diagnostics), as on the Dot 3.
t5_boot_reason() {
	r=$($BB cat /data/techo5-linux/restart-reason 2>/dev/null)
	$BB rm -f /data/techo5-linux/restart-reason
	case "$r" in update | restart | recovery)
		echo "$r"
		return
		;;
	esac
	if $BB ls /sys/fs/pstore/dmesg-* >/dev/null 2>&1; then
		echo panic
		return
	fi
	case " $($BB cat /proc/cmdline) " in
	*" androidboot.bootreason=kernel_panic "* | *" boot_reason=8 "*) echo panic ;;
	*" androidboot.bootreason=wdt"* | *" boot_reason=3 "* | *" boot_reason=4 "* | *" boot_reason=9 "* | *" boot_reason=10 "*) echo watchdog ;;
	*" androidboot.bootreason=power_key "* | *" androidboot.bootreason=usb "* | *" boot_reason=0 "* | *" boot_reason=1 "*) echo power ;;
	*) echo unknown ;;
	esac
}
$BB mount -t pstore pstore /sys/fs/pstore 2>/dev/null
t5_boot_reason > /run/techo5/boot-reason
if [ -d /data/techo5-linux ] && [ -d /sys/fs/pstore ]; then
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

# --- The loopback (AirPlay 2's receiver and nqptp talk over it), then inbound closed except what the
# Dot serves, before any network exists.
$BB ip link set lo up 2>/dev/null
/usr/local/sbin/techo5-firewall 2>&1 | while read -r l; do say "$l"; done

# --- USB serial console: an ACM function on the configfs gadget under a Linux Foundation id, as the
# rescue ramdisk makes it (etc/inittab runs the shell on it).
usb_console() {
	local g=/config/usb_gadget/techo5 udc sn
	[ -d /config/usb_gadget ] || return 1
	[ -n "$($BB cat $g/UDC 2>/dev/null)" ] && return 0
	$BB mkdir -p $g/strings/0x409 $g/configs/c.1/strings/0x409 $g/functions/acm.usb0 || return 1
	echo 0x1d6b > $g/idVendor
	echo 0x0104 > $g/idProduct
	echo 0x0200 > $g/bcdUSB
	echo 0xEF > $g/bDeviceClass
	echo 0x02 > $g/bDeviceSubClass
	echo 0x01 > $g/bDeviceProtocol
	sn=$($BB sed -n 's/.*androidboot.serialno=\([^ ]*\).*/\1/p' /proc/cmdline)
	[ -n "$sn" ] || sn=$($BB tr -d '\000 \n' < /proc/idme/serial 2>/dev/null)
	echo "${sn:-cannoli}" > $g/strings/0x409/serialnumber
	echo "TECHO5" > $g/strings/0x409/manufacturer
	echo "Echo Dot 5 Linux" > $g/strings/0x409/product
	echo "ACM" > $g/configs/c.1/strings/0x409/configuration
	echo 250 > $g/configs/c.1/MaxPower
	[ -e $g/configs/c.1/acm.usb0 ] || $BB ln -s $g/functions/acm.usb0 $g/configs/c.1/
	udc=$($BB ls /sys/class/udc 2>/dev/null | $BB head -1)
	[ -n "$udc" ] && echo "$udc" > $g/UDC
}
usb_console
i=0
while [ $i -lt 10 ] && [ ! -e /dev/ttyGS0 ]; do $BB mdev -s; $BB sleep 1; i=$((i + 1)); done
say "usb console $([ -e /dev/ttyGS0 ] && echo up || echo missing)"

# --- Wi-Fi: the unit's MT7663 driver and firmware from the store, loaded as Fire OS loads them
# (init.connectivity.rc, then the driver; /dev/wmtWifi powers the radio, as init.mt8519.rc writes it).
# The firmware links go in before the first insmod, and mdev answers requests that fall back to the user
# helper. Never rmmod the driver.
FW=$S/firmware
MODS=$S/modules
if [ -e $MODS/wlan_mt76x3_sdio.ko ]; then
	echo /sbin/mdev > /proc/sys/kernel/hotplug
	for l in /lib/firmware /etc/firmware /vendor/firmware; do
		[ -L $l ] || $BB rm -rf $l
		$BB mkdir -p ${l%/*}
		$BB ln -sfn $FW $l
	done
	$BB mkdir -p /system/vendor
	$BB ln -sfn $FW /system/vendor/firmware
	for m in compat cfg80211 mac80211 wlan_mt76x3_sdio_prealloc wlan_mt76x3_sdio; do
		[ -e $MODS/$m.ko ] || continue
		$BB grep -q "^$m " /proc/modules && continue
		$BB insmod $MODS/$m.ko 2>/tmp/insmod.err || say "insmod $m: $($BB cat /tmp/insmod.err)"
	done
	i=0
	while [ $i -lt 10 ] && [ ! -e /dev/wmtWifi ]; do $BB mdev -s; $BB sleep 1; i=$((i + 1)); done
	[ -e /dev/wmtWifi ] && echo 1 > /dev/wmtWifi
	i=0
	while [ $i -lt 30 ] && [ ! -e /sys/class/net/wlan0 ]; do $BB sleep 1; i=$((i + 1)); done
	say "wlan0 $([ -e /sys/class/net/wlan0 ] && echo up || echo missing) after ${i}s"
else
	say "no Wi-Fi driver in $MODS: no Wi-Fi (the rescue ramdisk adopts it there)"
fi

# --- The speaker's tuning (echod's lib/asp, asp.Cannoli): Fire OS's /vendor/etc/audio-algorithms, about
# 2 MB, adopted once into the store from system_a, a SquashFS UBI volume that a ubiblock device lets us
# mount read-only and leave as it was. Amazon's, so never in a published rootfs: every unit takes its own.
# Without it the speaker plays untuned (flat: no bass lift and no limiter).
AA=$S/audio-algorithms
ubivol() {
	local d
	for d in /sys/class/ubi/ubi*_*; do
		[ "$($BB cat $d/name 2>/dev/null)" = "$1" ] && { $BB basename $d; return 0; }
	done
	return 1
}
adopt_audio() {
	[ -e $AA/AFE.cfg ] && return 0
	[ -e $S/.techo5-store ] || return 0
	vol=$(ubivol system_a) || { say "no system_a volume: the speaker plays untuned"; return 0; }
	[ -e /dev/$vol ] || $BB mdev -s
	/usr/local/bin/ubiblock create /dev/$vol || { say "no block device for system_a: the speaker plays untuned"; return 0; }
	blk=/dev/ubiblock${vol#ubi}
	i=0
	while [ $i -lt 5 ] && [ ! -b $blk ]; do $BB mdev -s; $BB sleep 1; i=$((i + 1)); done
	$BB mkdir -p /tmp/system_a $AA.new
	if $BB mount -t squashfs -o ro $blk /tmp/system_a 2>/tmp/system_a.err; then
		$BB cp /tmp/system_a/system/vendor/etc/audio-algorithms/* $AA.new/ 2>/dev/null
		$BB umount /tmp/system_a
	else
		say "system_a will not mount: $($BB cat /tmp/system_a.err)"
	fi
	/usr/local/bin/ubiblock remove /dev/$vol
	if [ -e $AA.new/AFE.cfg ]; then
		$BB rm -rf $AA
		$BB mv $AA.new $AA
		$BB sync
		say "audio coefficients adopted from system_a: $($BB ls $AA | $BB wc -l) files"
	else
		$BB rm -rf $AA.new
		say "no audio coefficients on system_a: the speaker plays untuned"
	fi
}
adopt_audio
if [ -e $AA/AFE.cfg ]; then
	for v in /vendor /system/vendor; do
		[ -L $v ] && continue
		$BB mkdir -p $v/etc
		$BB ln -sfn $AA $v/etc/audio-algorithms
	done
	say "audio coefficients at /vendor/etc/audio-algorithms"
fi

# --- The same address every boot, before wpa_supplicant (usr/local/sbin/techo5-wlan-mac), then the
# network: the one set with wifi-set, or the one Fire OS saved (usr/local/sbin/techo5-net).
[ -e /sys/class/net/wlan0 ] && /usr/local/sbin/techo5-wlan-mac > /dev/null
IP=$(/usr/local/sbin/techo5-net)
say "address ${IP:-none}"

# --- SSH belongs to the daemon (feature/security); here it only gets its places on userdata.
KEYS=/data/misc/echolocal/ssh
HOSTKEYS=/data/techo5-linux/dropbear
if [ -d /data/techo5-linux ]; then
	$BB mkdir -p $KEYS $HOSTKEYS
	$BB chmod 700 $KEYS $HOSTKEYS
	# Keys put there by hand for the rescue ramdisk count too, until the daemon has its own.
	[ -s $KEYS/authorized_keys ] || [ ! -s /data/techo5-linux/authorized_keys ] ||
		$BB cp /data/techo5-linux/authorized_keys $KEYS/authorized_keys
	[ -L /root/.ssh ] || $BB rm -rf /root/.ssh
	$BB ln -sfn $KEYS /root/.ssh
	[ -L /etc/dropbear ] || $BB rm -rf /etc/dropbear
	$BB ln -sfn $HOSTKEYS /etc/dropbear
fi

# The clock starts in 1970: several time servers, the home's own first, in the background, then ntpd
# keeps running (as on the Dot 3).
gw=$($BB route -n 2>/dev/null | $BB awk '$1 == "0.0.0.0" { print $2; exit }')
own=$($BB head -n 1 /data/misc/echolocal/ntp-server 2>/dev/null | $BB tr -cd 'A-Za-z0-9.:-')
( n=0
	until $BB timeout -s KILL 40 $BB ntpd -n -q ${own:+-p "$own"} -p time.cloudflare.com -p time.google.com -p 0.pool.ntp.org -p 1.pool.ntp.org ${gw:+-p "$gw"} > /tmp/ntpd.log 2>&1; do
		n=$((n + 1))
		[ $n -ge 30 ] && { echo "techo5-cannoli boot: clock not set yet, no time server answered; ntpd keeps trying" > /dev/kmsg; break; }
		$BB sleep 60
	done
	if [ $n -lt 30 ]; then
		$BB hwclock -w -u 2>/dev/null
		: > /run/techo5/ntp-set
		echo "techo5-cannoli boot: clock $($BB date)" > /dev/kmsg
	fi
	exec $BB ntpd -n -S /etc/techo5/ntp-event.sh ${own:+-p "$own"} -p time.cloudflare.com -p time.google.com -p 0.pool.ntp.org -p 1.pool.ntp.org ${gw:+-p "$gw"} > /dev/null 2>&1 ) &

# --- The services' own users (as on the Dot 2 and 3).
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

# --- Bluetooth: the unit's btmtksdio from the store (MediaTek's vendor driver, WMT inside: it makes
# /dev/stpbt, raw H4), after Wi-Fi as Fire OS loads it, since the two share the chip. The stock kernel
# has no Bluetooth stack, so ours comes as modules built for it (build-cannoli-bt-modules.sh, in this
# slot's /usr/lib/techo5/modules with their load-order); btbridge turns /dev/stpbt into hci0 through
# /dev/vhci and techo5-bt starts BlueZ and bluez-alsa on it, as on the Dot 3.
BTMODS=/usr/lib/techo5/modules
if [ -e $MODS/btmtksdio.ko ] && [ -e /sys/class/net/wlan0 ]; then
	$BB grep -q '^btmtksdio ' /proc/modules || $BB insmod $MODS/btmtksdio.ko 2>/dev/null
	i=0
	while [ $i -lt 15 ] && [ ! -e /dev/stpbt ]; do $BB mdev -s; $BB sleep 1; i=$((i + 1)); done
	say "bluetooth radio $([ -e /dev/stpbt ] && echo up || echo missing)"
	if [ -e /dev/stpbt ]; then
		for m in $($BB cat $BTMODS/load-order 2>/dev/null); do
			$BB grep -q "^$m " /proc/modules && continue
			$BB insmod $BTMODS/$m.ko 2>/dev/null || say "bluetooth: $m.ko did not load"
		done
		$BB mdev -s
		if [ -e /dev/vhci ]; then
			# hci0 first: a BLE proxy that opened /dev/stpbt before btbridge did would split the
			# controller's replies with it.
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

# A boot is healthy once the daemon has stayed up for a while: then the slot is committed and the try
# count in the store (init-cannoli counts it) goes back to zero. If it is not, SSH is started here with
# the keys on userdata, so the unit can still be looked at without the rescue ramdisk.
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
		echo "0 healthy" > $S/tries.tmp && $BB mv -f $S/tries.tmp $S/tries
		$BB sync
		echo "techo5-cannoli boot: healthy; slot committed, try count reset" > /dev/kmsg
	else
		if ! $BB pidof dropbear > /dev/null && [ -s $KEYS/authorized_keys ]; then
			[ -s $HOSTKEYS/dropbear_ed25519_host_key ] ||
				/usr/bin/dropbearkey -t ed25519 -f $HOSTKEYS/dropbear_ed25519_host_key > /dev/null 2>&1
			/usr/sbin/dropbear -s -p 22 -r $HOSTKEYS/dropbear_ed25519_host_key 2>/dev/null &&
				echo "techo5-cannoli boot: the daemon is not staying up; ssh started for a look" > /dev/kmsg
		fi
		case "$($BB cat $S/slots/$SLOT.state 2>/dev/null)" in
		trial*)
			echo "techo5-cannoli boot: slot $SLOT on trial never became healthy" > /dev/kmsg
			;;
		*)
			echo "techo5-cannoli boot: daemon not staying up; this boot counts as a failed try" > /dev/kmsg
			;;
		esac
	fi
) &

# Anything of the owner's, kept on userdata (as on the Dot 3).
if [ -x /data/techo5-linux/rc.local ]; then
	$BB mv -f /data/techo5-linux/rc.local.log /data/techo5-linux/rc.local.log.1 2>/dev/null
	(/data/techo5-linux/rc.local >> /data/techo5-linux/rc.local.log 2>&1) &
	say "rc.local started"
fi
