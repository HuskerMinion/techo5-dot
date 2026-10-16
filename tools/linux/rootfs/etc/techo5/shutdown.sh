#!/bin/busybox.static sh
# Shutdown (see /etc/inittab). A clean stop for the daemon first, as on TECHO5's images: it gates the
# amplifier before letting go of the speaker, and its last writes reach userdata while it is still
# writable. Init sends everything else TERM, and KILL a second later, only after this.
BB=/bin/busybox.static
pid=$($BB pidof echod)
if [ -n "$pid" ]; then
	$BB kill -TERM $pid
	n=0; while [ $n -lt 10 ] && $BB pidof echod >/dev/null; do $BB sleep 1; n=$((n+1)); done
fi
$BB killall dropbear ntpd udhcpc wpa_supplicant 2>/dev/null
$BB sync
$BB umount -a -r 2>/dev/null
$BB sync
