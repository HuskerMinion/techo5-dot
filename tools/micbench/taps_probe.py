#!/usr/bin/env python3
"""Does a longer echo filter help on the Dot? ERLE vs filter length at one playback level."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import MICS, RATE, db, load

d, name = sys.argv[1], sys.argv[2]
x = load(os.path.join(d, name + ".s24")); ref, avg = x[7], x[:MICS].mean(0)
start, ps = None, []
for line in open(os.path.join(d, "session.log")):
    f = line.split()
    if len(f) >= 3 and f[1] == "start" and f[2] == name: start = float(f[0])
    if len(f) >= 4 and f[1] == "prompt" and f[2] == name and start is not None: ps.append(float(f[0]) - start)
gaps = np.ones(x.shape[1], bool)
for p in ps: gaps[int((p-0.5)*RATE):int((p+3.5)*RATE)] = False
gaps[:2*RATE] = False

print("| filter length | reach | ERLE on the 7-mic average |\n|---|---|---|")
for taps in [256, 768, 1536, 3072, 6144]:
    pre = 64
    idx = np.where(gaps)[0]; idx = idx[(idx > taps) & (idx < len(avg) - pre)]
    rng = np.random.default_rng(0)
    rows = rng.choice(idx, min(3 * taps, len(idx)), replace=False)
    X = ref[rows[:, None] + pre - np.arange(taps)[None, :]].astype(np.float32)
    h, *_ = np.linalg.lstsq(X, avg[rows].astype(np.float32), rcond=None)
    est = np.convolve(ref, h.astype(np.float64))[pre:pre + len(avg)]
    e = avg - est
    # score on gap samples the fit did not use, to keep it honest
    held = np.zeros(len(avg), bool); held[idx] = True; held[rows] = False
    print(f"| {taps} taps | {taps / RATE * 1000:.0f} ms | {db((avg[held]**2).mean() / (e[held]**2).mean()):.1f} dB |")
