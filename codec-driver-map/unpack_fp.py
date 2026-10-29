#!/usr/bin/env python3
"""Unpack SIGMA fp FP__V502.bin MAIN segments (DFI + Okumura LZSS), per
fpSup research/firmware/notes/FORMAT.md. usage: unpack_fp.py FP__V502.bin outdir"""
import os
import struct
import sys

def lzss(src, usize):
    N, F = 4096, 18
    ring = bytearray(N)
    r = N - F
    out = bytearray()
    i, n = 0, len(src)
    while i < n and len(out) < usize:
        flags = src[i]; i += 1
        for bit in range(8):
            if i >= n:
                break
            if flags >> bit & 1:
                c = src[i]; i += 1
                out.append(c); ring[r] = c; r = (r + 1) & (N - 1)
            else:
                if i + 1 >= n:
                    break
                b0, b1 = src[i], src[i + 1]; i += 2
                off = b0 | ((b1 & 0xF0) << 4)
                for k in range((b1 & 0x0F) + 3):
                    c = ring[(off + k) & (N - 1)]
                    out.append(c); ring[r] = c; r = (r + 1) & (N - 1)
    return bytes(out[:usize])

fw = open(sys.argv[1], 'rb').read()
os.makedirs(sys.argv[2], exist_ok=True)
assert fw[0x100:0x104] == b'@DFI'
for k in range(2):
    usize, coff, addr, csum, method, csize = struct.unpack_from('<6I', fw, 0x120 + 0x18 * k)
    comp = fw[0x300 + coff:0x300 + coff + csize]
    ok = (sum(comp) & 0xFFFFFFFF) == csum
    data = lzss(comp, usize)
    path = os.path.join(sys.argv[2], 'seg%d_%08x.bin' % (k, addr))
    open(path, 'wb').write(data)
    print('%s method=%#x csum %s size %d/%d' % (path, method, 'OK' if ok else 'BAD', len(data), usize))
