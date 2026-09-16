#!/usr/bin/env python3
"""ERLE and talker margin at each playback level of the sweep session."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import CENTRE, MICS, RATE, bandpass, db, load
from aec_probe import cancel, TAPS, PRE

d = sys.argv[1]
def prompts(name):
    start, out = None, []
    for line in open(os.path.join(d, "session.log")):
        f = line.split()
        if len(f) >= 3 and f[1] == "start" and f[2] == name: start = float(f[0])
        if len(f) >= 4 and f[1] == "prompt" and f[2] == name and start is not None: out.append(float(f[0]) - start)
    return out
def windows(n, ps, lo, hi):
    m = np.zeros(n, bool)
    for p in ps: m[int((p+lo)*RATE):int((p+hi)*RATE)] = True
    return m

print("| file level | echo at centre mic | echo at the 7-mic average | ERLE, average | residual | talker over residual |")
print("|---|---|---|---|---|---|")
for lvl in [33, 27, 21, 15]:
    name = f"sweep_l{lvl}"
    x = load(os.path.join(d, name + ".s24")); ref, mics = x[7], x[:MICS]; ps = prompts(name)
    talk = windows(x.shape[1], ps, 0.2, 2.2); gaps = ~windows(x.shape[1], ps, -0.5, 3.5); gaps[:2*RATE] = False
    avg = mics.mean(0)
    idx = np.where(gaps)[0]; idx = idx[(idx > TAPS) & (idx < len(avg) - PRE)]
    rng = np.random.default_rng(0); rows = rng.choice(idx, min(24000, len(idx)), replace=False)
    X = ref[rows[:, None] + PRE - np.arange(TAPS)[None, :]].astype(np.float32)
    h, *_ = np.linalg.lstsq(X, avg[rows].astype(np.float32), rcond=None)
    e = cancel(ref, avg, h.astype(np.float64))
    pin, pres = (avg[gaps]**2).mean(), (e[gaps]**2).mean()
    margin = db(max((e[talk]**2).mean() - pres, 1e-20) / pres)
    print(f"| -{lvl} dBFS | {db((mics[CENTRE][gaps]**2).mean()):.1f} dBFS | {db(pin):.1f} dBFS | {db(pin/pres):.1f} dB | {db(pres):.1f} dBFS | {margin:+.1f} dB |")
