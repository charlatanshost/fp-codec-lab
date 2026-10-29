#!/usr/bin/env python3
"""Time per frame for the fp's 0x300D lossless JPEG (LJ92) engine, using fpSup's measured
cost model (LOSSLESS_DEVELOPMENT_SUMMARY.md §2):
    t(us) = 2179 + 4954.40 * aligned_Mpix + 40.926 * tiles
with the engine's tile limits (tileW 32..512 step 32, tileH multiple of 16 up to 512,
<= 326 tiles). Reproduces fpSup's published optima (FHD 13.87 ms, OG3K 34.33 ms, UHD 44.91 ms).
usage: engine_budget.py [WxH ...]"""
import math
import sys

def best(w, h):
    b = None
    for tw in range(32, 513, 32):
        for th in range(16, 513, 16):
            nt = math.ceil(w / tw) * math.ceil(h / th)
            if nt > 326:
                continue
            mp = math.ceil(w / tw) * tw * math.ceil(h / th) * th / 1e6
            t = 2179 + 4954.40 * mp + 40.926 * nt
            if b is None or t < b[0]:
                b = (t, tw, th, nt)
    return b

sizes = [s.split('x') for s in sys.argv[1:]] or \
    [(3840, 2160), (3840, 2048), (3840, 1920), (3840, 1600), (3024, 2010), (1936, 1090)]
for w, h in sizes:
    w, h = int(w), int(h)
    t, tw, th, nt = best(w, h)
    duty = '  '.join('@%s %3.0f%%' % (n, t / 1000 / p * 100) for n, p in (('23.976', 41.71), ('25', 40.0), ('29.97', 33.37)))
    print('%4dx%-4d %5.1f ms/frame (tile %dx%d, %d tiles)  %s' % (w, h, t / 1000, tw, th, nt, duty))
