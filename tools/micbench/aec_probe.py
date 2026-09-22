#!/usr/bin/env python3
"""Second-pass checks on a capture session: tones in the quiet floor, and what a linear echo
canceller (a least-squares FIR fitted on the music-only lead-in) leaves behind, per mic and
for mixes of canceled mics.

    python aec_probe.py <capture dir>
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from analyze import CENTER, FRAME, MICS, RATE, bandpass, db, frame_power, load  # noqa: E402

TAPS = 768
PRE = 64  # taps before the loopback sample, for the acausal side of the fit


def tones(q):
    w = np.hanning(4096)
    n = q.shape[1] // 4096
    spec = np.zeros(2049)
    for m in range(MICS):
        blocks = q[m, : n * 4096].reshape(n, 4096) * w
        spec += (np.abs(np.fft.rfft(blocks, axis=1)) ** 2).mean(axis=0)
    spec /= MICS
    f = np.fft.rfftfreq(4096, 1 / RATE)
    med = np.median(spec)
    peaks = [(f[i], db(spec[i] / med)) for i in range(2, len(spec) - 2)
             if spec[i] == spec[i - 2:i + 3].max() and spec[i] > 30 * med]
    peaks.sort(key=lambda p: -p[1])
    return peaks[:12]


def fit(ref, mic, rows=24000, seed=1):
    """Least-squares FIR h with mic[n] ~ sum_k h[k] ref[n + PRE - k], fitted on random rows."""
    rng = np.random.default_rng(seed)
    n = rng.choice(np.arange(TAPS, len(mic) - PRE), size=min(rows, len(mic) - TAPS - PRE), replace=False)
    idx = n[:, None] + PRE - np.arange(TAPS)[None, :]
    X = ref[idx].astype(np.float32)
    y = mic[n].astype(np.float32)
    h, *_ = np.linalg.lstsq(X, y, rcond=None)
    return h.astype(np.float64)


def cancel(ref, mic, h):
    est = np.convolve(ref, h)[PRE: PRE + len(mic)]
    return mic - est


def notch(x, tones=(3152.3, 6304.7), width=60):
    spec = np.fft.rfft(x, axis=-1)
    f = np.fft.rfftfreq(x.shape[-1], 1 / RATE)
    for t in tones:
        spec[..., np.abs(f - t) < width] = 0
    return np.fft.irfft(spec, n=x.shape[-1], axis=-1)


def main():
    d = sys.argv[1]
    q = load(os.path.join(d, "quiet.s24"))[:MICS]
    print("# Tones in the quiet floor (average over mics, dB above the median bin)\n")
    for f, lvl in tones(q):
        print(f"- {f:7.1f} Hz  +{lvl:.1f} dB")
    lowcut = bandpass(q, 100, 8000)
    for lo, hi in [(100, 1000), (1000, 3000), (3000, 8000)]:
        c = np.corrcoef(bandpass(q, lo, hi))
        off = c[~np.eye(MICS, dtype=bool)]
        print(f"\nQuiet floor mean inter-mic correlation {lo}-{hi} Hz: {off.mean():+.2f} (min {off.min():+.2f}, max {off.max():+.2f})")

    x = load(os.path.join(d, "music_front1m_seg0.s24"))
    ref = x[7]
    mics = x[:MICS]
    lead = 10 * RATE  # music only
    talk0 = 12 * RATE  # talking starts about 11 s after playback, capture started 1 s after it
    print("\n# Echo cancellation, least-squares FIR fitted on the first 10 s (music only)\n")
    print("| mic | echo in (dBFS) | residual, fit window | residual, held-out 10-12 s ... | ERLE fit | ERLE held-out |")
    print("|---|---|---|---|---|---|")
    out = np.zeros_like(mics)
    for m in range(MICS):
        h = fit(ref[:lead], mics[m, :lead])
        e = cancel(ref, mics[m], h)
        out[m] = e
        pin = (mics[m, 2 * RATE:lead] ** 2).mean()
        pfit = (e[2 * RATE:lead] ** 2).mean()
        pin_ho = (mics[m, lead:talk0] ** 2).mean()
        pho = (e[lead:talk0] ** 2).mean()
        print(f"| {m} | {db(pin):.1f} | {db(pfit):.1f} | {db(pho):.1f} | {db(pin / pfit):.1f} | {db(pin_ho / pho):.1f} |")

    # Speech over the residual: speech frames from the canceled center mic in the talk section.
    seg = slice(talk0, x.shape[1])
    cen = bandpass(out[CENTER, seg][None], 200, 4000)
    p = frame_power(cen)[0]
    quiet_p = frame_power(bandpass(out[CENTER, 2 * RATE:lead][None], 200, 4000))[0].mean()
    speech = p > quiet_p * 4
    print(f"\nTalk section: {speech.sum()} speech frames of {len(p)} (6 dB over the canceled music-only residual)")
    if speech.sum() < 10:
        return

    def score(sig_talk, sig_res, lo, hi):
        pt = frame_power(bandpass(sig_talk[None], lo, hi))[0][speech].mean()
        pr = frame_power(bandpass(sig_res[None], lo, hi))[0].mean()
        return db(max(pt - pr, 1e-20) / pr)

    res = slice(2 * RATE, lead)
    cands = {
        "center, no AEC": (mics[CENTER, seg], mics[CENTER, res]),
        "center + AEC": (out[CENTER, seg], out[CENTER, res]),
        "average of 7, no AEC": (mics[:, seg].mean(0), mics[:, res].mean(0)),
        "average of 7 AEC'd mics": (out[:, seg].mean(0), out[:, res].mean(0)),
    }
    print("\nSpeech over music: speech frames against the music-only residual (dB)\n")
    print("| front end | 100-8k | <1.5k | 1.5-4.7k |\n|---|---|---|---|")
    for k, (t, r) in cands.items():
        print(f"| {k} | " + " | ".join(f"{score(t, r, lo, hi):.1f}" for lo, hi in [(100, 8000), (100, 1500), (1500, 4700)]) + " |")


if __name__ == "__main__":
    main()
