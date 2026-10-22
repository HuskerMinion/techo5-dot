#!/bin/busybox.static sh
# Run by the long-running ntpd (boot.sh, -S) on its events. When it steps the clock or reaches a
# server, the RTC is written too, in UTC as the kernel reads it, so a clock set late, after boot's
# own tries ran out, still gives the next boot a good start. echod is told NTP has the clock, so Home
# Assistant's time leaves it alone.
case "$1" in
step|stratum)
	/bin/busybox.static hwclock -w -u 2>/dev/null
	: > /run/techo5/ntp-set 2>/dev/null
	;;
esac
