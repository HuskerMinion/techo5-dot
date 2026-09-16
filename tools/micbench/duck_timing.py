#!/usr/bin/env python3
"""How much warning does the wake score give? Lead time from a low arming level to the detection,
which is all the time a ducking rule would have to dip the music and still help the same utterance.

    python duck_timing.py <trace dir> [<capture dir for session.log>]
"""
import glob, os, sys
import numpy as np

traces, capdirs = sys.argv[1], sys.argv[2:]
DET, ARM = 0.85, [0.2, 0.3, 0.5]

def prompts(name):
    for d in capdirs:
        p = os.path.join(d, "session.log")
        if not os.path.exists(p):
            continue
        start, out = None, []
        for line in open(p):
            f = line.split()
            if len(f) >= 3 and f[1] == "start" and f[2] == name: start = float(f[0])
            if len(f) >= 4 and f[1] == "prompt" and f[2] == name and start is not None: out.append(float(f[0]) - start)
        if out: return out
    return []

print(f"| take | front end | detections | lead over {ARM[0]} | over {ARM[1]} | over {ARM[2]} |")
print("|---|---|---|---|---|---|")
for path in sorted(glob.glob(os.path.join(traces, "*.csv"))):
    base = os.path.basename(path)[:-4]
    name, fe = base.rsplit("-", 1)
    d = np.loadtxt(path, delimiter=",")
    t, s = d[:, 0], d[:, 1]
    hits = [i for i in range(1, len(s)) if s[i] >= DET and s[i-1] < DET]
    if not hits:
        continue
    leads = {a: [] for a in ARM}
    for h in hits:
        for a in ARM:
            i = h
            while i > 0 and s[i-1] >= a:
                i -= 1
            leads[a].append(t[h] - t[i])
    row = f"| {name} | {fe} | {len(hits)} |"
    for a in ARM:
        v = np.array(leads[a])
        row += f" {v.mean()*1000:.0f} ms (max {v.max()*1000:.0f}) |"
    print(row)
