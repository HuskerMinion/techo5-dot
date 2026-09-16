#!/system/bin/sh
# Raw 9-channel mic captures for the TECHO5 Dot bench, with LED ring cues.
# Usage: micsession.sh smoke|full   (run detached; progress in $OUT/session.log)
# EchoLocal's echod (service ledcontroller) is stopped for the session and always restarted.

T=/data/local/tmp/t5dot
E=$T/echod
MODE=${1:-smoke}
OUT=$T/cap-$MODE
mkdir -p $OUT
LOG=$OUT/session.log
: > $LOG

log() { echo "$(cat /proc/uptime | cut -d' ' -f1) $*" >> $LOG; }

restore() {
	[ -n "$PLAYER" ] && kill $PLAYER 2>/dev/null
	$E tools led off >/dev/null 2>&1
	setprop ctl.start ledcontroller
	log "restored ledcontroller"
	log DONE
}
trap restore EXIT
trap 'exit 1' INT TERM HUP

ring() { $E tools led "$@" >/dev/null 2>&1; }

# cap NAME SECONDS: one raw capture plus the tool's level report
cap() {
	log "start $1 ${2}s"
	$E tools mic -t "$2" --raw $OUT/$1.s24 > $OUT/$1.txt 2>&1
	log "end $1 rc=$?"
}

# play the test track in the background for about SECONDS
play() {
	/system/bin/tinyplay $T/${1:-music48.wav} -D 0 -d 23 > $OUT/tinyplay.txt 2>&1 &
	PLAYER=$!
	log "music pid $PLAYER"
}

stopplay() {
	[ -n "$PLAYER" ] && kill $PLAYER 2>/dev/null
	wait $PLAYER 2>/dev/null
	PLAYER=
	log "music stopped"
}

blink() { # blink COLOR TIMES
	i=0
	while [ $i -lt $2 ]; do ring all $1; sleep 1; ring off; sleep 1; i=$((i + 1)); done
}

segblink() { # segblink SEGMENT TIMES: that segment blinks amber, 2 s per blink
	i=0
	while [ $i -lt $2 ]; do ring seg $1 ff8000; sleep 1; ring off; sleep 1; i=$((i + 1)); done
}

log "mode $MODE"
setprop ctl.stop ledcontroller
sleep 3
log "ledcontroller=$(getprop init.svc.ledcontroller)"

case $MODE in
smoke)
	ring seg 0 ffffff
	play
	sleep 1
	cap smoke 3
	stopplay
	;;
full)
	# Get ready: whole ring blinks amber for 20 s.
	log "cue ready"
	blink ff8000 10

	# 1. Quiet room. Whole ring dim red: stay silent.
	log "cue quiet"
	ring all 300000
	cap quiet 20

	# 2. Turn segment 0 toward you, talk at 1 m.
	log "cue turn seg0"; segblink 0 6
	ring seg 0 00ff00
	cap front1m_seg0 25

	# 3. Turn the Dot half a turn: segment 6 toward you, talk at 1 m.
	log "cue turn seg6"; segblink 6 6
	ring seg 6 00ff00
	cap back1m_seg6 25

	# 4. Segment 0 toward you again, talk from as far as you can (about 3 m). Blue.
	log "cue turn seg0 far"; segblink 0 6
	ring seg 0 0000ff
	cap far_seg0 25

	# 5. Music from the Dot. Purple: stay quiet for 10 s, then green segment 0: talk at 1 m over it.
	log "cue music"; segblink 0 6
	ring all 400040
	play
	sleep 1
	( sleep 11; $E tools led seg 0 00ff00 >/dev/null 2>&1; echo "$(cut -d' ' -f1 /proc/uptime) cue talk-over-music" >> $LOG ) &
	cap music_front1m_seg0 35
	stopplay
	;;
wake)
	# Wake-word takes: every take gets five prompts. The lit segment flashes white; say "Okay Nabu" once
	# per flash. Prompt times are logged against the capture's start.
	log "cue ready"
	blink ff8000 45

	# Quiet, ring dark, then quiet with the ring lit as in the first session: where the 3152 Hz tone comes from.
	log "cue quiet ring off"
	ring off
	cap quiet_ringoff 15
	log "cue quiet ring on"
	ring all 300000
	cap quiet_ringon 15

	wake_take() { # wake_take NAME SEGMENT COLOR
		log "cue turn $1"; segblink $2 6
		ring seg $2 $3
		( sleep 3; k=1; while [ $k -le 5 ]; do
			$E tools led seg $2 ffffff >/dev/null 2>&1
			echo "$(cut -d' ' -f1 /proc/uptime) prompt $1 $k" >> $LOG
			sleep 1
			$E tools led seg $2 $3 >/dev/null 2>&1
			sleep 5
			k=$((k + 1))
		done ) &
		cap $1 34
	}

	wake_take wake_1m_seg0 0 00ff00
	wake_take wake_1m_seg6 6 00ff00
	wake_take wake_far_seg0 0 0000ff

	# Louder music from the Dot, talker at 1 m on segment 0. The music runs 8 s alone before the prompts.
	log "cue turn music"; segblink 0 6
	ring seg 0 400040
	play music48_loud.wav
	sleep 8
	wake_take wake_music_seg0 0 400040
	stopplay

	# A phone playing speech or music off to one side while the talker is on segment 0. Cyan: start it.
	log "cue phone"; segblink 3 6
	ring seg 3 00ffff
	sleep 8
	wake_take wake_phone_seg0 0 00ffff
	;;
sweep)
	# Music volume sweep: the same five prompts at four playback levels, the talker at 1 m on
	# segment 0 throughout. The Dot stays put; only the music level changes.
	log "cue ready"
	blink ff8000 30

	wake_take() { # wake_take NAME SEGMENT COLOR
		ring seg $2 $3
		( sleep 3; k=1; while [ $k -le 5 ]; do
			$E tools led seg $2 ffffff >/dev/null 2>&1
			echo "$(cut -d' ' -f1 /proc/uptime) prompt $1 $k" >> $LOG
			sleep 1
			$E tools led seg $2 $3 >/dev/null 2>&1
			sleep 5
			k=$((k + 1))
		done ) &
		cap $1 34
	}

	for lvl in 33 27 21 15; do
		log "cue level $lvl"
		segblink 0 4
		play music_l$lvl.wav
		sleep 6
		wake_take sweep_l$lvl 0 400040
		stopplay
		sleep 1
	done
	;;
esac
exit 0
