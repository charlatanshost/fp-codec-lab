#!/usr/bin/env python3
"""Pull the facts that decide which clip is which: size, rate, codec fourcc.

The rate matters because C070D858 returns mode 5 for 1920x1080 only at the 2997
and 11988 rate codes -- an FHD clip at 29.97 is a mode-5 setting, so it is the
wrong control for a mode-8 test.

usage: movinfo.py FILE...
"""
import struct
import sys

CONTAINERS = {b'moov', b'trak', b'mdia', b'minf', b'stbl', b'edts', b'udta'}


def walk(buf, start, end, depth=0, out=None):
    out = [] if out is None else out
    off = start
    while off + 8 <= end:
        size, typ = struct.unpack_from('>I4s', buf, off)
        hdr = 8
        if size == 1:
            if off + 16 > end:
                break
            size = struct.unpack_from('>Q', buf, off + 8)[0]
            hdr = 16
        elif size == 0:
            size = end - off
        if size < hdr or off + size > end:
            break
        out.append((typ, off, size, hdr, depth))
        if typ in CONTAINERS:
            walk(buf, off + hdr, off + size, depth + 1, out)
        elif typ == b'stsd':
            walk(buf, off + hdr + 8, off + size, depth + 1, out)
        off += size
    return out


def main():
    for path in sys.argv[1:]:
        buf = open(path, 'rb').read()
        bs = walk(buf, 0, len(buf))
        print('%s  %d bytes' % (path.split('/')[-1], len(buf)))
        for typ, off, size, hdr, depth in bs:
            name = typ.decode('latin1', 'replace')
            if name in ('avc1', 'hvc1', 'hev1', 'encv'):
                w, h = struct.unpack_from('>HH', buf, off + hdr + 24)
                print('   video sample entry: %s   %dx%d' % (name, w, h))
            elif typ == b'mdhd':
                ver = buf[off + hdr]
                if ver == 1:
                    ts, dur = struct.unpack_from('>IQ', buf, off + hdr + 4 + 16)
                else:
                    ts, dur = struct.unpack_from('>II', buf, off + hdr + 4 + 8)
                if ts:
                    print('   mdhd: timescale %d  duration %d  -> %.3f s' % (ts, dur, dur / ts))
            elif typ == b'stts':
                n = struct.unpack_from('>I', buf, off + hdr + 4)[0]
                ent = [struct.unpack_from('>II', buf, off + hdr + 8 + 8 * i) for i in range(min(n, 4))]
                print('   stts: %d entries, first %s (count, delta)' % (n, ent))
            elif typ == b'stsz':
                cnt = struct.unpack_from('>I', buf, off + hdr + 8)[0]
                print('   stsz: %d samples' % cnt)
            elif typ == b'mdat':
                print('   mdat at 0x%X, %d bytes' % (off, size))
        print()


if __name__ == '__main__':
    sys.exit(main())
