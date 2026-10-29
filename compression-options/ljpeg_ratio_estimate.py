#!/usr/bin/env python3
"""Estimate what the fp's LJ92 engine would achieve on an uncompressed 12-bit fp cDNG
frame at 12-bit, 10-bit linear (>>2) and 10-bit square-root curve: entropy of the
same-colour left-neighbour residual + ~0.05 bit Huffman overhead. Pure Python, slow
(~1 min per 8 Mpix). usage: ljpeg_ratio_estimate.py frame.DNG"""
import sys, struct, math
from collections import Counter
def load(p):
    b=open(p,'rb').read(); e='<'
    o=struct.unpack('<I',b[4:8])[0]; n=struct.unpack('<H',b[o:o+2])[0]; d={}
    for i in range(n):
        t,ty,c,v=struct.unpack('<HHI4s',b[o+2+i*12:o+14+i*12])
        d[t]=struct.unpack('<H' if ty==3 else '<I',v[:2 if ty==3 else 4])[0]
    w,h,off=d[256],d[257],d[273]; s=b[off:off+w*h*3//2]; px=[0]*(w*h)
    for i in range(0,w*h,2):
        j=i*3//2; px[i]=(s[j]<<4)|(s[j+1]>>4); px[i+1]=((s[j+1]&15)<<8)|s[j+2]
    return w,h,px
def H(w,h,px):  # left same-colour neighbour predictor, first two columns predicted from above
    c=Counter()
    for y in range(h):
        r=y*w
        for x in range(w):
            p = px[r+x-2] if x>=2 else (px[r-2*w+x] if y>=2 else 0)
            c[px[r+x]-p]+=1
    N=w*h; return -sum(v/N*math.log2(v/N) for v in c.values())
w,h,px=load(sys.argv[1]); N=w*h
lin10=[v>>2 for v in px]
# sqrt-style curve 12->10 bit over black..white: noise-matched-ish
blk=256; lut=[0]*4096
for v in range(4096):
    x=max(v-blk,0)/(4095-blk); lut[v]=round(1023*math.sqrt(x))
sq10=[lut[v] for v in px]
for name,data,bits in (("12-bit linear (stock)",px,12),("10-bit linear (>>2)",lin10,10),("10-bit sqrt curve",sq10,10)):
    e=H(w,h,data)+0.05   # ~Huffman overhead
    print(f"{name:24s} {e:5.2f} bits/px  -> {12/e:4.2f}:1 vs 12-bit packed, {bits/e:4.2f}:1 vs its own packing")
