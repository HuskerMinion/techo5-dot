#!/usr/bin/env python3
"""Why the wake word fails over loud music and beside a talking phone (cap-wake session)."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import CENTRE, FRAME, MICS, RATE, bandpass, db, frame_power, load, gcc_phat, delay
from aec_probe import fit, cancel, tones

d = sys.argv[1]
def prompts(name):
    start, out = None, []
    for line in open(os.path.join(d, "session.log")):
        f = line.split()
        if len(f) >= 3 and f[1] == "start" and f[2] == name: start = float(f[0])
        if len(f) >= 4 and f[1] == "prompt" and f[2] == name and start is not None: out.append(float(f[0]) - start)
    return out

def windows(n, ps, lo=0.0, hi=2.5):
    m = np.zeros(n, bool)
    for p in ps: m[int((p + lo) * RATE):int((p + hi) * RATE)] = True
    return m

print("# Ring whine\n")
for name in ["quiet_ringoff", "quiet_ringon"]:
    q = load(os.path.join(d, name + ".s24"))[:MICS]
    top = tones(q)[:3]
    print(f"- {name}: floor {db((bandpass(q,100,8000)**2).mean()):.1f} dBFS; strongest tones " + ", ".join(f"{f:.0f} Hz +{l:.0f} dB" for f, l in top))

print("\n# Loud music take\n")
x = load(os.path.join(d, "wake_music_seg0.s24")); ref = x[7]; mics = x[:MICS]; ps = prompts("wake_music_seg0")
talk = windows(x.shape[1], ps)
gaps = ~windows(x.shape[1], ps, -0.5, 3.5); gaps[:2*RATE] = False
avg = mics.mean(0)
for label, sig in [("centre", mics[CENTRE]), ("average of 7", avg)]:
    h = fit(ref[gaps], sig[gaps]) if False else None
    # fit on the gaps only (no talker), apply everywhere
    idx = np.where(gaps)[0]; idx = idx[(idx > 800) & (idx < len(sig) - 100)]
    from aec_probe import TAPS, PRE
    rng = np.random.default_rng(0); rows = rng.choice(idx, 24000, replace=False)
    X = ref[rows[:, None] + PRE - np.arange(TAPS)[None, :]].astype(np.float32)
    h, *_ = np.linalg.lstsq(X, sig[rows].astype(np.float32), rcond=None)
    e = cancel(ref, sig, h.astype(np.float64))
    pin, pres = (sig[gaps]**2).mean(), (e[gaps]**2).mean()
    pspeech = max((e[talk]**2).mean() - pres, 1e-20)
    print(f"| {label} | echo {db(pin):.1f} dBFS | residual {db(pres):.1f} dBFS | ERLE {db(pin/pres):.1f} dB | talker over residual {db(pspeech/pres):.1f} dB |")
    for lo, hi in [(100, 1000), (1000, 4000), (4000, 8000)]:
        b_in, b_e = bandpass(sig[None], lo, hi)[0], bandpass(e[None], lo, hi)[0]
        print(f"    {lo}-{hi} Hz: ERLE {db((b_in[gaps]**2).mean()/(b_e[gaps]**2).mean()):.1f} dB, residual {db((b_e[gaps]**2).mean()):.1f} dBFS")
clip = int((np.abs(mics) >= 0.999).sum())
print(f"\nclipped mic samples: {clip}; loopback peak {20*np.log10(np.abs(ref).max()):.1f} dBFS, mic peak {20*np.log10(np.abs(mics).max()):.1f} dBFS")

print("\n# Phone take: talker on segment 0, phone to one side\n")
x = load(os.path.join(d, "wake_phone_seg0.s24")); mics = bandpass(x[:MICS], 100, 8000); ps = prompts("wake_phone_seg0")
talk = windows(x.shape[1], ps, 0.3, 1.6)
between = ~windows(x.shape[1], ps, -0.5, 3.5); between[:RATE] = False
# direction of the phone (between prompts) and of the talker (the first-session seg0 geometry: use the talk windows)
ph = np.array([gcc_phat(mics[CENTRE, between], mics[m, between])[0] for m in range(MICS)])
tk = np.array([gcc_phat(mics[CENTRE, talk], mics[m, talk])[0] for m in range(MICS)])
print("lags vs centre, phone:  " + " ".join(f"{v:+.2f}" for v in ph))
print("lags vs centre, talker: " + " ".join(f"{v:+.2f}" for v in tk))
def sir(sig):
    return db((sig[talk]**2).mean() / (sig[between]**2).mean())
cands = {"centre": mics[CENTRE], "average of 7": mics.mean(0),
         "delay-and-sum to talker": np.stack([delay(mics[m], -tk[m]) for m in range(MICS)]).mean(0)}
# MVDR per frequency: noise covariance from between-prompt audio, steering from talker lags
N = 512; hop = 256; win = np.hanning(N)
def stft(s):
    n = (s.shape[-1] - N) // hop
    return np.stack([np.fft.rfft(s[..., i*hop:i*hop+N] * win, axis=-1) for i in range(n)], axis=-1)  # mics, bins, frames
S = stft(mics)
fr = np.arange(S.shape[2]) * hop + N // 2
nb = between[fr]
f = np.fft.rfftfreq(N, 1 / RATE)
out = np.zeros(S.shape[1:], complex)
for b in range(S.shape[1]):
    a = np.exp(-2j * np.pi * f[b] / RATE * tk)[:, None]
    Rn = S[:, b, nb] @ S[:, b, nb].conj().T / nb.sum()
    Rn += np.eye(MICS) * np.trace(Rn).real / MICS * 0.05
    w = np.linalg.solve(Rn, a); w /= (a.conj().T @ w)
    out[b] = (w.conj().T @ S[:, b, :])[0]
y = np.zeros(mics.shape[1])
for i in range(out.shape[1]):
    y[i*hop:i*hop+N] += np.fft.irfft(out[:, i], n=N) * win
y /= (win**2).sum() / hop
cands["MVDR to talker (noise from between prompts)"] = y
print("\n| front end | talker windows over between-prompt audio (dB) |\n|---|---|")
for k, v in cands.items():
    print(f"| {k} | {sir(v):.1f} |")
