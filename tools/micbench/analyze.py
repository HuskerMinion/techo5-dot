#!/usr/bin/env python3
"""Offline analysis of raw Echo Dot 2 captures (9 ch, S24_3LE, 16 kHz).

Reads the .s24 files written by `echod tools mic --raw` and reports, per capture:
levels and clipping per channel, speech SNR per microphone in three bands, what a
24->16 bit truncation costs, inter-mic arrival lags (GCC-PHAT) against the centre mic,
and the SNR of candidate mixes (centre, best single mic, average, delay-and-sum).

    python analyze.py <capture dir> [--quiet quiet.s24] [--miccal 18150,15784,...]
"""

import argparse
import glob
import os
import sys

import numpy as np

RATE = 16000
CHANNELS = 9
MICS = 7
CENTRE = 6
FRAME = 320  # 20 ms
BANDS = [("<1.5k", 100, 1500), ("1.5-4.7k", 1500, 4700), (">4.7k", 4700, 8000)]
FULL = ("100-8k", 100, 8000)


def load(path):
    raw = np.fromfile(path, dtype=np.uint8)
    raw = raw[: len(raw) // (3 * CHANNELS) * 3 * CHANNELS].reshape(-1, CHANNELS, 3).astype(np.int32)
    v = raw[..., 0] | (raw[..., 1] << 8) | (raw[..., 2] << 16)
    v = np.where(v & 0x800000, v - (1 << 24), v)
    return v.T.astype(np.float64) / (1 << 23)  # (channels, samples), full scale = 1.0


def db(p):
    return 10 * np.log10(np.maximum(p, 1e-20))


def bandpass(x, lo, hi):
    """Zero-phase FFT band limit of each row."""
    spec = np.fft.rfft(x, axis=-1)
    f = np.fft.rfftfreq(x.shape[-1], 1 / RATE)
    spec[..., (f < lo) | (f >= hi)] = 0
    return np.fft.irfft(spec, n=x.shape[-1], axis=-1)


def frame_power(x):
    n = x.shape[-1] // FRAME
    return (x[..., : n * FRAME].reshape(*x.shape[:-1], n, FRAME) ** 2).mean(axis=-1)


def masks(centre):
    """Speech and noise frames from the centre mic, band-limited to speech."""
    p = frame_power(bandpass(centre[None], 200, 4000))[0]
    order = np.sort(p)
    noise_level = order[: max(1, len(order) // 5)].mean()
    noise = p <= order[len(order) // 5]
    speech = p > noise_level * 10 ** (10 / 10)  # 10 dB over the quiet fifth
    return speech, noise


def snr(x, speech, noise, lo, hi):
    p = frame_power(bandpass(x[None] if x.ndim == 1 else x, lo, hi))
    if not speech.any() or not noise.any():
        return np.full(p.shape[0], np.nan)
    ps, pn = p[:, speech].mean(axis=1), p[:, noise].mean(axis=1)
    return db(np.maximum(ps - pn, 1e-20) / pn)


def gcc_phat(a, b, max_lag=4.0, up=16):
    """Lag of b relative to a in samples (positive: b hears it later)."""
    n = len(a)
    A, B = np.fft.rfft(a), np.fft.rfft(b)
    f = np.fft.rfftfreq(n, 1 / RATE)
    cross = B * np.conj(A)
    cross /= np.abs(cross) + 1e-12
    cross[(f < 300) | (f > 5000)] = 0
    full = np.zeros(n * up // 2 + 1, dtype=complex)
    full[: len(cross)] = cross
    cc = np.fft.irfft(full, n=n * up)
    m = int(max_lag * up)
    window = np.concatenate([cc[-m:], cc[: m + 1]])
    return (np.argmax(window) - m) / up, window.max() / (np.abs(cc).mean() + 1e-12)


def speech_only(x, speech):
    idx = np.repeat(speech, FRAME)
    return x[..., : len(idx)][..., idx]


def delay(x, lag):
    """Fractional delay by FFT phase: returns x delayed by lag samples."""
    n = len(x)
    f = np.fft.rfftfreq(n)
    return np.fft.irfft(np.fft.rfft(x) * np.exp(-2j * np.pi * f * lag), n=n)


def analyse(path, quiet_floor, miccal, out):
    x = load(path)
    name = os.path.basename(path)[:-4]
    secs = x.shape[1] / RATE
    w = out.write
    w(f"\n## {name}  ({secs:.1f} s)\n\n")

    w("| ch | rms dBFS | peak dBFS | clipped | 24->16 truncation noise vs floor |\n|---|---|---|---|---|\n")
    for c in range(CHANNELS):
        s = x[c]
        rms, peak = db((s**2).mean()), 20 * np.log10(max(np.abs(s).max(), 1e-10))
        clipped = int((np.abs(s) >= 0.999).sum())
        if c < MICS:
            t = np.floor(s * (1 << 15)) / (1 << 15)
            terr = db(((s - t) ** 2).mean())
            note = f"trunc {terr:.1f} dB vs {rms:.1f} dB -> {rms - terr:.1f} dB margin"
        else:
            note = "loopback"
        w(f"| {c} | {rms:.1f} | {peak:.1f} | {clipped} | {note} |\n")

    mics = x[:MICS]
    speech, noise = masks(mics[CENTRE])
    w(f"\nSpeech frames {speech.sum()} of {len(speech)} ({speech.mean() * 100:.0f}%), noise frames {noise.sum()}.\n")
    if speech.sum() < 25:
        w("Too little speech for SNR and direction analysis.\n")
        return {"name": name}

    w("\nSNR per microphone (dB, speech frames against the quietest fifth of the same capture)\n\n")
    w("| mic | " + " | ".join(b[0] for b in [FULL] + BANDS) + " |\n|---|" + "---|" * (len(BANDS) + 1) + "\n")
    table = np.stack([snr(mics, speech, noise, lo, hi) for _, lo, hi in [FULL] + BANDS], axis=1)
    level = db(frame_power(bandpass(mics, 200, 4000))[:, speech].mean(axis=1))
    for m in range(MICS):
        w(f"| {m}{' (centre)' if m == CENTRE else ''} | " + " | ".join(f"{v:.1f}" for v in table[m]) + " |\n")

    w("\nSpeech level per mic relative to the centre (dB), and miccal ratio if given\n\n")
    rel = level - level[CENTRE]
    w("| mic | speech level vs centre | miccal vs centre |\n|---|---|---|\n")
    for m in range(MICS):
        cal = f"{20 * np.log10(miccal[m] / miccal[CENTRE]):+.1f}" if miccal else "-"
        w(f"| {m} | {rel[m]:+.1f} | {cal} |\n")

    # Direction: lags of each perimeter mic against the centre, over speech only.
    seg = speech_only(bandpass(mics, 100, 8000), speech)
    lags = []
    w("\nArrival lag against the centre mic, speech only (samples; negative = hears it first; +-1.68 is the ring's limit)\n\n| mic | lag | peak/mean |\n|---|---|---|\n")
    for m in range(MICS):
        lag, q = gcc_phat(seg[CENTRE], seg[m])
        lags.append(lag)
        w(f"| {m} | {lag:+.2f} | {q:.0f} |\n")
    lags = np.array(lags)

    # Mixes, scored with the same frames.
    full_band = bandpass(mics, 100, 8000)
    best = int(np.argmax(table[:MICS, 0]))
    avg = full_band.mean(axis=0)
    aligned = np.stack([delay(full_band[m], -lags[m]) for m in range(MICS)])
    das = aligned.mean(axis=0)
    gains = 10 ** (-rel / 20)
    das_cal = (aligned * gains[:, None]).mean(axis=0)
    candidates = [
        ("centre mic", full_band[CENTRE]),
        (f"best single mic ({best})", full_band[best]),
        ("average of 7", avg),
        ("delay-and-sum, measured lags", das),
        ("delay-and-sum, measured lags + level match", das_cal),
    ]
    w("\nMixes (SNR dB, same speech/noise frames)\n\n| mix | " + " | ".join(b[0] for b in [FULL] + BANDS) + " |\n|---|" + "---|" * (len(BANDS) + 1) + "\n")
    scores = {}
    for label, sig in candidates:
        row = [snr(sig, speech, noise, lo, hi)[0] for _, lo, hi in [FULL] + BANDS]
        scores[label] = row
        w(f"| {label} | " + " | ".join(f"{v:.1f}" for v in row) + " |\n")
    return {"name": name, "lags": lags, "scores": scores, "floor": None}


def loopback(path, out):
    x = load(path)
    w = out.write
    ref = x[7]
    active = np.abs(ref) > 0
    if not active.any():
        return
    w("\nLoopback alignment (music-only first 10 s)\n\n")
    n = min(10 * RATE, x.shape[1])
    r, mic = ref[:n], x[CENTRE, :n]
    R, M = np.fft.rfft(r), np.fft.rfft(mic)
    cc = np.fft.irfft(M * np.conj(R), n=n)
    window = np.concatenate([cc[-200:], cc[:201]])
    k = int(np.argmax(np.abs(window))) - 200
    sign = np.sign(window[k + 200])
    w(f"Centre mic lags the loopback by {k} samples ({k / RATE * 1000:.2f} ms), polarity {'inverted' if sign < 0 else 'normal'}.\n")
    w(f"Loopback RMS {db((r**2).mean()):.1f} dBFS, echo at the centre mic {db((mic**2).mean()):.1f} dBFS.\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--miccal", default="")
    args = ap.parse_args()
    miccal = [float(v) for v in args.miccal.split(",")] if args.miccal else None

    out = sys.stdout
    out.write(f"# Mic bench: {args.dir}\n")
    quiet = os.path.join(args.dir, "quiet.s24")
    if os.path.exists(quiet):
        q = load(quiet)[:MICS]
        out.write("\n## Quiet room floor\n\n| mic | rms dBFS | " + " | ".join(b[0] for b in BANDS) + " |\n|---|---|" + "---|" * len(BANDS) + "\n")
        for m in range(MICS):
            bands = [db((bandpass(q[m][None], lo, hi) ** 2).mean()) for _, lo, hi in BANDS]
            out.write(f"| {m} | {db((q[m] ** 2).mean()):.1f} | " + " | ".join(f"{v:.1f}" for v in bands) + " |\n")
        full = bandpass(q, 100, 8000)
        coh = np.corrcoef(full)
        out.write("\nQuiet-room correlation between mics, 100 Hz-8 kHz (how much of the floor is shared)\n\n")
        out.write("| | " + " | ".join(str(m) for m in range(MICS)) + " |\n|---|" + "---|" * MICS + "\n")
        for a in range(MICS):
            out.write(f"| {a} | " + " | ".join(f"{coh[a, b]:.2f}" for b in range(MICS)) + " |\n")

    for path in sorted(glob.glob(os.path.join(args.dir, "*.s24"))):
        if os.path.basename(path) == "quiet.s24":
            continue
        analyse(path, None, miccal, out)
        if "music" in os.path.basename(path):
            loopback(path, out)


if __name__ == "__main__":
    main()
