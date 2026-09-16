# Microphones and beamforming on the Echo Dot 2

The Dot is the device the daemon's array code was written for: EchoLocal developed it on
`biscuit`, and TECHO5 inherited it. This document records four things: what that code already does,
what two other projects measured on this exact array and its Echo Show cousins, what that means
for "beamforming", and the order the work should take. Nothing here has been measured by this
project yet. Every number is quoted from EchoLocal (code comments), EchoMuse (`SETUP.md` and its device notes) or jxlarrea (`docs/echo-cancellation.md`, on the Echo Show 8 `crown`).

## The array

| Item | Value | Source |
|---|---|---|
| Capture device | `pcmC0D24c`, only 16 kHz, S24_3LE, **9 channels** | EchoLocal `device_dot.go`, EchoMuse |
| Channel map | ch 0–5 perimeter ring, ch 6 **centre**, ch 7–8 playback loopback L/R | both |
| Geometry | six mics on a **36 mm radius** ring, 60° apart, plus the centre (PCB-measured) | EchoMuse, EchoLocal `beam.go` |
| Bearings | EchoMuse, by tone at each hole: ch 0–5 at 330°, 30°, 90°, 150°, 210°, 270°. EchoLocal: ch 0 at 108° in its own frame, beam 0 at 320° from LED segment 0 | `SETUP.md`, `beam.go`, `facing.go` |
| Arrival differences | ≈105 µs centre to ring (1.7 samples at 16 kHz), ≈210 µs across (3.4 samples) | geometry |
| ADCs | 4× TLV320ADC3101 on one TDM bus, `ADC_A`–`ADC_D`, inputs on `DIF1_L`/`DIF1_R` | EchoMuse, `gain.go` |
| Analog gain | `ADC_x MICPGA Volume Ctrl`, 0.5 dB steps, real to 119 (59.5 dB). EchoLocal's default is the vendor's 20 dB; EchoMuse runs digital 88 / PGA 40 | `gain.go`, EchoMuse |
| Raw speech level | about −70 dBFS in the 24-bit sample, so **taking the top 16 bits throws the signal away**; gain must be applied before narrowing | EchoMuse v2.7.1 |
| Loopback | the digital stream itself, pre-volume, bit-exact zero in silence; mics lag it by ~33 samples, polarity inverted | EchoMuse `aec_map.sh` |

## What the daemon does today

- **Mix setting** (Home Assistant select): `Center mic` (ch 6 alone, **the Dot's default**),
  `All microphones` (average), `Delay and sum` (`beam.go`: six steered beams, interpolated
  fractional delays, the loudest beam in high frequencies wins after 8 frames ≈160 ms), and
  `Beamformer` when Amazon's tuning is on the device.
- **Amazon's fixed beamformer**, re-implemented from the firmware's coefficient files in
  `/vendor/etc/audio-algorithms/` (`lib/subband`). Two front ends are recognised:
  - `FilterBank_640 + FBF`: 64 bands, 6 beams, all 7 mics.
  - `FilterBank_768cvxGLow + FBFV2`: 128 bands, 8 beams, 4 of 7 mics.
- **Echo cancellation** runs on the **centre mic only** and replaces the mix while anything plays.
  The built-in NLMS filter uses 1024 taps; EchoLocal measured 16.5 → 27.2 dB of cancellation from
  256 → 2048 taps. The WebRTC helper (`techo5-aec`, from the Show) is selectable, but its binary is
  built for the Show rootfs and has never run on a Dot.
- **Direction finding** (`facing.go`) runs delay-and-sum only when the LED ring asks.
- **24 → 16 bit narrowing:** `decode` in `hardware/mic/mic.go` drops the low 8 bits (`>> 8`)
  before any processing. Measured 2026-09-15 (below), this **does not matter** on this unit: the
  truncation noise is −95 dBFS, and the quietest mic's room floor is −76 dBFS, so it sits 19–30 dB
  under the floor and costs well under 0.1 dB of SNR.

## What has already been measured

### EchoMuse, on biscuit: summing beamformers are marginal here, and why

EchoMuse built a frequency-domain delay-and-sum (exact FFT phase shifts, no interpolation) in
`device/tools/bf_capture`. It works, with a flat response and no artefacts, but at conversational
distance it was **only marginally better than picking the best single mic**. They recorded the
reason as structural: in a diffuse room field, the noise coherence between mics `d` apart is
`sinc(2fd/c)`.

| Freq | Coherence, adjacent (36 mm) | Coherence, opposite (72 mm) |
|---|---|---|
| 300 Hz | 0.99 | 0.97 |
| 500 Hz | 0.98 | 0.93 |
| 1 kHz | 0.93 | 0.73 |
| 2 kHz | 0.73 | 0.18 |
| 4 kHz | 0.18 | −0.16 |

- Below ~1.5 kHz, where most speech energy is, the mics hear 84–99% the same noise, so a sum has
  almost nothing to cancel.
- The 36 mm adjacent spacing puts spatial aliasing at `c/2d` = 4.76 kHz.
- That leaves a useful window of about **2–4.7 kHz**.

Their other findings:

- **Superdirective / differential** beamforming is the only class that gets directivity from a
  sub-wavelength aperture. It costs **white-noise gain**: 20 dB or more of mic self-noise
  amplification at low frequencies, and it needs per-capsule magnitude and phase calibration. The
  capsules are spread over four unmatched ADCs. EchoMuse filed it as research, not roadmap.
- **Production is a selector**: the centre mic for the wake word (the same from every direction),
  then the perimeter mic with the best onset energy locked for the voice turn, scored over a ~2 s
  history that covers the wake word. It is **off by default**: within 1.5 m the SNR gain was
  marginal, and a wrong lock is worse than none.
- Far-field reach "is not a beamforming problem on this hardware": room noise floor, distance and
  placement dominate. They measured 8.7 dB of noise-floor drift between two takes of the same
  phrase at 1.3 m, enough on its own to flip a transcript.

### jxlarrea, on the Echo Show 8 (same codec family, FPGA front end)

1. **Clipping kills everything downstream.** At +40 dB PGA, 24% of samples hit full scale during
   loud playback, and no canceller recovers that. Keep analog gain modest and add loudness
   digitally after cancellation.
2. **The loopback is a sample-aligned far end.** WebRTC's full linear canceller removed 38–44 dB at
   ~13% of a core (all four Show 8 mics: ~30%).
3. **Low suppression is the double-talk balance.** High suppression cost 19 dB of the talker during
   music and broke the wake word.
4. On the Show 8's 2.5 cm array, averaging gained 0.1–0.3 dB and WebRTC's nonlinear beamformer lost
   9–21 dB. The Dot's ring is wider, but EchoMuse's table above shows it is still small against
   speech wavelengths.

## First measurements: the bench unit, 2026-09-15

Session: `tools/micbench` + `micsession.sh` (EchoLocal's daemon stopped, the TECHO5 `dot` build's
`tools mic --raw`). Nine channels at the mixer settings EchoLocal leaves (PGA 26 = 13 dB, digital
88), in a home room with the talker seated about 1 m away. The Dot was turned between takes (LED
segment 0 or 6 toward the talker), plus one take at about 3 m and one with a pink-noise-plus-tones
test track at −22 dBFS RMS through the speaker. Raw files and reports:
`D:\platform-tools\echodot\<serial>\captures\cap-full\` (`report.md`, `report-aec.md`,
`report-notched.md`).

**Levels.**
- Speech at 1 m peaks around −45 dBFS.
- The quiet floor is −65 to −76 dBFS, depending on the mic.
- Nothing clipped.
- The floor carries a strong **3152 Hz tone (+48 dB over the median bin) with a 6305 Hz
  harmonic**, plus mains-like low tones. This is probably the LED ring or a regulator: the ring was
  lit throughout. Notching the tone did not change any mix ranking.

**Geometry works.** GCC-PHAT against the centre mic gives clean, sign-consistent lags. Facing
segment 0, mics 4/5 lead by about 1 sample and mics 1/2 lag by about 1 sample. Turning the Dot half
a turn flips every sign. Seated above the Dot, the talker's elevation shrinks the ±1.68-sample ring
limit to about ±1.1.

**Mix SNR** (speech frames against the quietest fifth of the same take, dB):

| take | centre | best single mic | average of 7 | delay-and-sum (measured lags) |
|---|---|---|---|---|
| 1 m, segment 0 | 17.8 | 19.2 | 18.7 | 18.8 |
| 1 m, segment 6 | 13.0 | 14.2 | **15.5** | 14.7 |
| ~3 m | 14.5 | 14.7 | **15.4** | 15.4 |
| 1.5–4.7 kHz band, the three takes | 12.7 / 8.9 / 8.5 | 13.0 / 9.5 / 6.9 | **15.7 / 12.8 / 10.4** | 15.6 / 10.2 / 8.8 |

The **plain average of all seven beats the centre mic in every take**: +0.9 to +2.5 dB overall
and **+2 to +4 dB in 1.5–4.7 kHz**, where consonants live. Steering added nothing over the plain
average at this aperture, as EchoMuse found. Why averaging helps here, against EchoMuse's
coherence argument: this quiet room's floor is mostly per-channel self-noise and interference.
The average's floor is −78.7 dBFS against the centre's −72.2, nearly the 8.5 dB that seven
independent noises allow. **Against a loud diffuse source (TV, fan) the gain will shrink**,
especially below 1.5 kHz. That needs its own take.

**Level matching:** the mics agree on speech level within ±1.5 dB. The `miccal` values
(+1.0 to +2.5 dB relative to the centre) don't track that. Either they are already applied
upstream or they mean something else, so they are not used.

**Echo.**
- The centre mic lags the loopback by **52 samples (3.25 ms), polarity inverted** (EchoMuse: 33
  on their unit).
- The speaker-to-mic coupling is about −34 dB at this volume.
- A 768-tap least-squares FIR fitted on 10 s of music removes 11–13 dB per mic. That takes the
  residual to −68 dBFS, within about 4 dB of the room floor: the echo was only about 12 dB above
  the floor to begin with.
- A louder take is needed to measure real ERLE.

**Speech over music** (speech frames against the cancelled music-only residual):

| front end | 100–8k | <1.5k | 1.5–4.7k |
|---|---|---|---|
| centre, no AEC (what a wake word hears today without cancelling) | −2.6 | −2.0 | −9.2 |
| centre + AEC (the daemon's current design) | 9.1 | 15.3 | −3.3 |
| **one AEC on the 7-mic average** | **14.5** | **16.3** | **2.3** |
| seven AECs, then averaged | 14.5 | 16.3 | 2.3 |

**One canceller on the averaged mic is exactly as good as seven.** The mics don't move, so the
echo path to their average is as fixed as any single mic's. That gives **+5.4 dB over the current
centre-mic canceller at the same CPU cost**.

**Through the daemon's own code** (TECHO5 branch `dot/mic-average`, replay tests in
`echod/internal/hardware/mic/replay_test.go` and `replay_wake_test.go`, run in WSL against these
captures):

- The built-in NLMS canceller (1024 taps), on the music take:

  | front end | residual | speech over it |
  |---|---|---|
  | centre (old) | −65.5 dBFS | −3.6 dB |
  | average of 7 (new) | −72.9 dBFS | +4.1 dB |

  That is **+7.7 dB** for the new front end.
- microWakeWord `okay_nabu` (cutoff 0.97), full path (mix, cancel, leveler, detector), best
  sliding-window score, old / new:

  | take | score, old | score, new |
  |---|---|---|
  | 1 m, segment 0 | 0.742 | **0.786** |
  | 1 m, segment 6 | 0.875 | 0.878 |
  | ~3 m | 0.956 | 0.960 |
  | music | 0.024 | 0.024 |

  - **No detection with either front end**, and no take with the old front end detected either.
  - Extra gain ahead of the leveler (×2, ×4, ×8) doesn't move the scores. Level is not the
    limit; the leveler already normalises.
  - The `hey_jarvis` model, never said, peaked at 0.634 (old) / 0.755 (new) on the music take,
    still under its cutoff. Watch false-accept scores in the next session.
  - These takes were not designed for wake-word counting: how many times "Okay Nabu" was said,
    and whether it was said over the music, is unknown. They can't say which front end
    *detects* better. A dedicated take can (below).
- Pre-existing on `main`, found here:
  - Since `6340795`, the mic package's tests don't compile: `level()` in `cancel.go` clashes with
    a test helper. The branch renames the logging helper `blockDBFS`.
  - On cronos, `TestBeamformerSteersTowardTheSource` fails, because the test assumes the Dot's
    seven-mic ring. `beam.go` and its test are untouched by the branch.

### Wake-word session, 2026-09-15 (`captures/cap-wake`)

Five "Okay Nabu" prompts per take (the LED segment flashed white; flash times in `session.log`).
Scored per prompt through the daemon's path with `replay_wake_test.go` at the model's cutoff 0.97.
The results were identical at 0.90 and 0.80.

| take | centre (old) | average of 7 (new) | false accepts |
|---|---|---|---|
| 1 m, segment 0 | 4/5 | 4/5 | 1 each, right after the missed prompt's window (a late utterance, not a false accept) |
| 1 m, segment 6 | 5/5 | 5/5 | 0 |
| ~3 m | 5/5 | 5/5 | 0 |
| **loud music from the Dot** (loopback −15 dBFS RMS) | **0/5** (best 0.20) | **0/5** (best 0.19) | 0 |
| **phone playing speech ~1 m to one side** | **0/5** (best 0.53) | **0/5** (best 0.52) | 0 |

`hey_jarvis` (never said) crossed nothing in any take.

**What this says.**
- In a quiet room the wake word is **already saturated** at 1 m and 3 m with either front end, so
  averaging can't show a detection gain there.
- **The failures are loud playback and a talking source nearby, and neither front end survives
  them.** That's where the work is.

**Why each fails** (`tools/micbench/hard_cases.py`, report in `report-hard.md`):

1. **The LED ring puts noise into the mics.**
   - Ring dark: floor −79.9 dBFS, and only mains-like low tones.
   - Ring lit dim red: floor **−71.6 dBFS**, plus a **3176 Hz tone at +46 dB** with its 6355 Hz
     harmonic.
   - The ring costs 8 dB of floor. The fix is at the source (LED driver PWM frequency, current, or
     rail filtering) or a notch while the ring is lit.
   - **Gone under Linux (2026-09-16).** The same captures on TECHO5 Linux (7-mic average, 4–6 s each,
     `echod tools mic` with the daemon held) show no tone at 3176 Hz or 6355 Hz in any case:
     - dark
     - dim red, bright white, grey and cyan, each at full and quarter drive current
     - the ring rewritten many times a second as a pulse, the way the daemon animates it

     The loudest bin in 3.0–3.35 kHz wanders (3043–3344 Hz) at 3–15 dB over the median bin, which
     is noise rather than a line. A lit ring raises the 100 Hz–8 kHz total by about 2 dB. Setting the
     IS31FL3236 output frequency to 22 kHz (register 0x4B = 1, through i2c) changed nothing
     measurable, so it is not applied.

     The tone was something Fire OS did while it ran, not the ring itself, and nothing is needed on
     the Linux image.
2. **Loud music: the linear canceller cannot follow the speaker.**
   - A least-squares FIR fitted on the gaps between prompts removes 19 dB below 1 kHz, but only
     **6 dB at 1–4 kHz** and 3 dB above.
   - The residual stays 11 dB over the room floor, and the talker lands at −4.6 dB (centre) or
     0.1 dB (average) against it.
   - Nothing clipped at the ADC (mic peak −30.6 dBFS). That points to **speaker/amplifier
     distortion**, which a DAC-side reference can't predict (as jxlarrea and EchoMuse found).
   - Candidates:
     - a nonlinear residual echo suppressor after the linear stage (WebRTC AEC3's);
     - a playback limiter or volume cap that keeps the speaker linear;
     - ducking playback while a wake score rises.
3. **Phone speech to one side: this is where spatial filtering earns its place.**
   - Talker windows against between-prompt audio: centre **2.0 dB**, average of 7 **2.8 dB**,
     delay-and-sum toward the talker **2.7 dB**.
   - **MVDR toward the talker, with the noise covariance taken from between prompts:
     6.8 dB (+4.8 dB)**.
   - That MVDR had an oracle: it knew which audio was noise. A device doesn't, before the wake
     word.
   - Blind options: parallel wake detection on a few fixed superdirective or MVDR beams; or noise
     statistics tracked from frames with a low wake score.
   - The GCC-PHAT lags in the talker windows nearly match the phone's, so the phone dominates even
     there. Direction has to come from the wake event, not from loudness.

### WebRTC's canceller on these takes, 2026-09-15

`tools/aec/techo5-aec.cpp` built for x86_64 the same way as the Dot's build (Alpine root under WSL,
no sudo; `scratchpad/build-aec-x86.sh`), run against the same captures through the daemon
(`TECHO5_AEC=webrtc`, `TECHO5_AEC_ARGS`). Only dynamic linking works: Alpine ships no static
webrtc/absl, so the test binary runs inside the Alpine root.

| engine | moderate music: speech over residual (centre / average) | loud music: wake scores |
|---|---|---|
| built-in NLMS, 1024 taps | −3.6 / **+4.1 dB** | 0.00 0.01 0.02 0.06 **0.20** |
| WebRTC, `--ns low` | −1.1 / +0.9 dB | 0.00 ×5 |
| WebRTC, `--ns off` | −1.1 / +0.9 dB | 0.00 ×5 |

**WebRTC as packaged is worse for the wake word**, and noise suppression is not the reason: its
nonlinear suppressor after the linear filter takes the talker with the echo, exactly the double-talk
failure jxlarrea measured on the Show 8. The library can export the linear filter output before the
suppressor (`export_linear_aec_output`), which is what the wake path wants, but calling
`GetLinearAecOutput` in this build segfaults; the experiment was reverted rather than left in the
shared helper.

**A better linear filter cannot fix the loud case either.** The offline least-squares fit
(`hard_cases.py`) is close to the best any linear canceller can do on this recording, and it still
leaves the talker only 0.1 dB above the residual on the averaged mics. The limit is what the
loudspeaker adds that no DAC-side reference contains. So the remaining levers for barge-in over loud
playback are:

- a **playback ceiling** that keeps the speaker linear (find it by measurement, below);
- **ducking** while a low-threshold wake score rises, then confirming;
- accepting that the button (not the wake word) is the way in over loud music, as the stock device
  largely is too.

**Next measurement:** a volume sweep — the same five prompts with the music at, say, −30, −24, −18
and −12 dBFS RMS — to find the loudest playback at which "Okay Nabu" still detects, for the old and
new front ends. That number is what a ceiling or a ducking rule would be built on.

### Volume sweep, 2026-09-15 (`captures/cap-sweep`)

Five prompts per level, talker at 1 m, the Dot untouched; only the playback level changed
(`micsession.sh sweep`). Detections through the daemon's path, at the published cutoff 0.97 and at
0.83, which is what the Echo Show 5 runs with in Home Assistant.

| file level | echo at the mics | ERLE (best linear fit) | talker over residual | hits @0.97 | hits @0.83 |
|---|---|---|---|---|---|
| −33 dBFS | −66 dBFS | 11.4 dB | +12.4 dB | 3/5 | **5/5** |
| −27 dBFS | −61 dBFS | 15.2 dB | +10.9 dB | 3/5 (avg) / 2/5 (centre) | **4/5** |
| −21 dBFS | −55 dBFS | 16.2 dB | +6.6 dB | 0/5 | 2/5 |
| −15 dBFS | −49 dBFS | 15.9 dB | +1.4 dB | 0/5 | 0/5 |

- **The ceiling is the echo level at the microphones, and it is sharp.** Reliable to about
  −60 dBFS, marginal at −55, gone by −49. Detection tracks the talker's margin over the residual
  almost exactly.
- **Lowering the cutoff to 0.83 is the cheapest win**: 5/5 and 4/5 at the two lower levels where
  0.97 gave 3/5. No false accepts appeared in any take at 0.83, and `hey_jarvis` (never said)
  peaked at 0.76 over music, so the margin is thin but real. 0.80 would be pushing it.
- **The average of seven scores at or above the centre mic at every level** (e.g. 0.86/0.70/0.98
  against 0.84/0.63/0.97 at −27 dBFS), and gained a detection at −27 dBFS.
- **Cancellation plateaus at ~16 dB, and filter length is not why.** ERLE against filter length on
  the −21 dBFS take: 14.1 dB at 256 taps, 14.7 at 768, 14.5 at 1536, 14.5 at 3072, 14.6 at 6144.
  The residual is not a room tail the filter fails to reach; it is what the speaker adds that the
  DAC-side reference cannot contain.
- **Against an Echo Show 5** (user's measurement, same daemon, built-in filter): 31 dB removed with
  the radio at −55 dBFS at the mic, wake word caught at 0.90 against a 0.83 cutoff. At the same
  −55 dBFS the Dot removes 16 dB and scores 0.73–0.93. The Dot's speaker is the harder one; its
  ceiling sits about 6 dB lower in playback level.

**What follows for the Dot:** the wake word works over playback up to about −60 dBFS at the mics —
comfortable listening level — and a lower cutoff buys a level or so. Above that, the answer is
ducking or the button, not a better filter.

**Conclusions so far.**
1. Replace "centre mic, and a cancelled centre mic during playback" with **the average of seven,
   and a canceller on that average**. Same cost, +1 to +2.5 dB quiet, +5 dB over music, most of it
   in the consonant band.
2. Steered beamforming earned nothing on these takes. Before retiring it, test with a directional
   interferer (TV or a phone speaker off to one side), the only case where it can.
3. Still to measure: a **wake-word take** ("Okay Nabu" a counted number of times, each position,
   quiet and over music) to count detections per front end; a loud-music take for real ERLE and
   double talk; a take with the ring dark to confirm where the 3152 Hz tone comes from; a TV or
   phone speaker off to one side, the one case where steering could win.

## The goal (user, 2026-09-15)

Make the Dot hear better than it does today. Beamforming is a means, not a requirement: whatever
measurably improves wake-word hits and transcripts ships, and whatever doesn't is left as an option
with the numbers written here.

## What this means for the goal

"All seven mics working with beamforming" has to be judged by **wake-word hits and transcript
accuracy**, not by whether a beamformer is running. The evidence says:

- **The biggest, surest wins are not steering:** averaging all seven mics, a canceller that holds
  during music (on that average, fed from the loopback), no clipping, and a steady front end for
  the wake word. The low byte turned out not to matter (measured, above).
- **Spatial processing has a real but narrow window** (2–4.7 kHz). The two approaches with a chance
  of beating a single mic in noisy rooms, rather than quiet ones, are both unproven on this device:
  - **Best-mic selection locked per turn** (EchoMuse's): cheap, and it can't smear speech.
  - **A calibrated superdirective beam limited to that band**, blended with the centre mic below
    it, with the look direction taken from the wake word. Its white-noise gain has to be bounded
    explicitly (diagonal loading).
- A beamformer upstream of the wake word is risky: it can steer at the TV. EchoMuse and the daemon
  both keep the wake word on a fixed front end.

## Plan

Each step produces measurements on disk before the next is chosen.

### B0 — Offline bench (no hardware)

- A host tool that replays raw 9-channel captures through every front-end combination in
  `hardware/mic`: centre, average, delay-and-sum, Amazon FBF/FBFV2, best-mic selection, AEC
  engines. For each it reports band-limited SNR (below 1.5 kHz, 1.5–4.7 kHz, above), ERLE, clip
  count and CPU.
- **Wake-word scoring** of the same outputs with the daemon's microWakeWord models, since that is
  what decides.
- Synthetic ring captures (a source at a bearing, a diffuse noise field with the coherence above,
  a loopback-derived echo with the 33-sample inverted lag) to validate the tool before a Dot
  exists.

### B1 — Ground truth on a Dot

- Confirm the 24 → 16 bit path and fix it if it truncates.
- Raw captures: quiet room and TV/music, talker at 0°/90°/180°, at 1 m and 3 m, at two PGA
  settings.
- Confirm the channel map by tone at each hole (EchoMuse's method) and the geometry by GCC-PHAT.
- Per-capsule gain and phase spread (this decides whether superdirective is possible at all).
- Clipping census across the volume curve.

### B2 — Echo cancellation first

- Build `techo5-aec` for the Dot rootfs. Compare it with the built-in NLMS on the same captures:
  ERLE, and speech loss during double talk.
- If a spatial stage wins in B3, decide where cancellation goes. Per mic before the beam keeps the
  acoustic path fixed. A fixed beam cancelled once is cheaper. With 512 MB and four A53s, seven
  WebRTC instances are likely too heavy.

### B3 — Spatial stage, only if it earns it

Scored on the B0 bench against `Centre mic + AEC`, in noisy captures at 3 m:

1. Best-mic selection locked per turn.
2. Amazon's FBFV2 (4 mics, 8 beams), already implemented.
3. A band-limited superdirective beam with diagonal loading (only if B1's capsule spread allows).

Ship the winner as the Dot's default only if it improves wake-word hits or transcript accuracy
where the centre mic fails. Otherwise keep it as a Home Assistant option and say why in this file.

### B4 — On the device

- The wake word stays on a fixed front end. Any steering happens after detection, locked for the
  whole turn, so Home Assistant never hears the beam swing.
- The LED ring shows the chosen direction (the ring already has direction effects).
- CPU and memory budget: microWakeWord plus the ESPHome API must keep their headroom on 512 MB.
