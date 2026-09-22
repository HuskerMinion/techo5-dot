#!/usr/bin/env python3
"""Rescore the speech captures' mixes with the ring's 3152/6305 Hz whine notched out of every mic."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from analyze import CENTER, MICS, RATE, bandpass, load, masks, snr, gcc_phat, speech_only, delay
from aec_probe import notch

d = sys.argv[1]
for name in ["front1m_seg0", "back1m_seg6", "far_seg0"]:
    x = notch(load(os.path.join(d, name + ".s24"))[:MICS])
    speech, noise = masks(x[CENTER])
    full = bandpass(x, 100, 8000)
    seg = speech_only(full, speech)
    lags = np.array([gcc_phat(seg[CENTER], seg[m])[0] for m in range(MICS)])
    per = np.array([snr(full[m], speech, noise, 100, 8000)[0] for m in range(MICS)])
    best = int(np.argmax(per))
    cands = [("center mic", full[CENTER]), (f"best single mic ({best})", full[best]),
             ("average of 7", full.mean(0)),
             ("delay-and-sum, measured lags", np.stack([delay(full[m], -lags[m]) for m in range(MICS)]).mean(0)),
             ("average of ring (0-5)", full[:6].mean(0))]
    print(f"\n## {name}, whine notched ({speech.sum()} speech frames)\n")
    print("| mix | 100-8k | <1.5k | 1.5-4.7k | >4.7k |\n|---|---|---|---|---|")
    for label, sig in cands:
        row = [snr(sig, speech, noise, lo, hi)[0] for lo, hi in [(100, 8000), (100, 1500), (1500, 4700), (4700, 8000)]]
        print(f"| {label} | " + " | ".join(f"{v:.1f}" for v in row) + " |")
