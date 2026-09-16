#!/usr/bin/env python3
"""One canceller on the 7-mic average vs seven cancellers averaged, on the music capture."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import CENTRE, MICS, RATE, bandpass, db, frame_power, load
from aec_probe import fit, cancel

d = sys.argv[1]
x = load(os.path.join(d, "music_front1m_seg0.s24"))
ref, mics = x[7], x[:MICS]
lead, talk0 = 10 * RATE, 12 * RATE
avg = mics.mean(0)
one = cancel(ref, avg, fit(ref[:lead], avg[:lead]))
seven = np.stack([cancel(ref, mics[m], fit(ref[:lead], mics[m, :lead])) for m in range(MICS)]).mean(0)
cen = cancel(ref, mics[CENTRE], fit(ref[:lead], mics[CENTRE, :lead]))
quiet = load(os.path.join(d, "quiet.s24"))[:MICS]
seg, res = slice(talk0, None), slice(2 * RATE, lead)
p = frame_power(bandpass(cen[seg][None], 200, 4000))[0]
speech = p > frame_power(bandpass(cen[res][None], 200, 4000))[0].mean() * 4
def score(sig, lo, hi):
    pt = frame_power(bandpass(sig[seg][None], lo, hi))[0][speech].mean()
    pr = frame_power(bandpass(sig[res][None], lo, hi))[0].mean()
    return db(max(pt - pr, 1e-20) / pr)
print("| front end | residual dBFS | quiet floor dBFS | speech/residual 100-8k | <1.5k | 1.5-4.7k |\n|---|---|---|---|---|---|")
for name, sig, q in [("centre + AEC", cen, quiet[CENTRE]), ("7 AECs, averaged", seven, quiet.mean(0)), ("1 AEC on the 7-mic average", one, quiet.mean(0))]:
    print(f"| {name} | {db((sig[res]**2).mean()):.1f} | {db((bandpass(q[None],100,8000)**2).mean()):.1f} | " + " | ".join(f"{score(sig, lo, hi):.1f}" for lo, hi in [(100,8000),(100,1500),(1500,4700)]) + " |")
