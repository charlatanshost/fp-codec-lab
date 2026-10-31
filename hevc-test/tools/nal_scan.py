#!/usr/bin/env python3
"""Decide whether an fp clip holds HEVC or AVC, from NAL headers.

Two independent signals, reported separately and then combined:

1. The sample entry's configuration box.  In a well-formed MOV the parameter
   sets are out of band, in avcC -- not in mdat -- so looking for SPS+PPS in
   the bitstream can never succeed on a real clip.  This is also the sharpest
   signal for the mode-8 test: the fp's muxer only knows how to write
   avc1/avcC (C071F3E8/C071FCD8), so a clip whose avcC carries NAL headers
   that are HEVC VPS/SPS/PPS is exactly what mode 8 should produce.
       AVC  SPS byte0 0x67, PPS 0x68     (type 7, 8 in a 1-byte header)
       HEVC VPS 0x40, SPS 0x42, PPS 0x44 (type 32, 33, 34 in a 2-byte header)

2. The mdat slice NALs, read strictly both ways.  HEVC's 2-byte header
   constrains nuh_layer_id to 0 and forbids temporal_id_plus1 == 0; in an AVC
   stream that second byte is slice-header payload and violates those
   constraints most of the time.  Counting strict violations each way
   discriminates without needing any parameter set.

Why both: a stock AVC clip read with HEVC's header reports plausible-looking
VPS, because AVC's 0x41 non-IDR byte decodes to type 32.  One reading alone
"finds" whatever it is asked for.

usage: nal_scan.py FILE [--limit BYTES]
"""
import argparse
import struct
import sys

HEVC_T = {32: 'VPS', 33: 'SPS', 34: 'PPS', 35: 'AUD', 39: 'PREFIX_SEI', 40: 'SUFFIX_SEI',
          19: 'IDR_W_RADL', 20: 'IDR_N_LP', 21: 'CRA', 0: 'TRAIL_N', 1: 'TRAIL_R',
          2: 'TSA_N', 3: 'TSA_R', 16: 'BLA_W_LP', 17: 'BLA_W_RADL', 18: 'BLA_N_LP'}
AVC_T = {1: 'non-IDR', 2: 'DPA', 5: 'IDR', 6: 'SEI', 7: 'SPS', 8: 'PPS', 9: 'AUD'}
HEVC_SLICE = set(range(0, 10)) | set(range(16, 22))
AVC_REAL_SLICE = {1, 5}          # what an encoder actually emits
AVC_PARTITION = {2, 3, 4}        # data partitioning: Extended profile only
CONTAINERS = {b'moov', b'trak', b'mdia', b'minf', b'stbl', b'edts', b'udta'}
VIDEO_ENTRY = (b'avc1', b'avc3', b'hvc1', b'hev1', b'encv')


def walk(buf, start, end, depth=0, out=None):
    """Box walk that clamps a box running past the buffer instead of bailing --
    a partial read of a camera clip always truncates mdat."""
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
        if size < hdr:
            break
        truncated = off + size > end
        eff = min(size, end - off)
        out.append((typ, off, size, hdr, depth, truncated))
        if not truncated:
            if typ in CONTAINERS:
                walk(buf, off + hdr, off + size, depth + 1, out)
            elif typ == b'stsd':
                walk(buf, off + hdr + 8, off + size, depth + 1, out)
        if truncated:
            break
        off += size
    return out


def parse_config(buf, entry_off, entry_hdr, entry_size, fourcc):
    """Return (kind, [(label, nal_bytes)]) from avcC or hvcC inside a sample entry."""
    end = entry_off + entry_size
    for box in (b'avcC', b'hvcC'):
        i = buf.find(box, entry_off + entry_hdr, end)
        if i < 0:
            continue
        csz = struct.unpack_from('>I', buf, i - 4)[0]
        cfg = buf[i + 4:i - 4 + csz]
        sets = []
        try:
            if box == b'avcC':
                nsps = cfg[5] & 0x1F
                off = 6
                for _ in range(nsps):
                    ln = struct.unpack_from('>H', cfg, off)[0]
                    off += 2
                    sets.append(('param set', cfg[off:off + ln]))
                    off += ln
                npps = cfg[off]
                off += 1
                for _ in range(npps):
                    ln = struct.unpack_from('>H', cfg, off)[0]
                    off += 2
                    sets.append(('param set', cfg[off:off + ln]))
                    off += ln
            else:
                narr = cfg[22]
                off = 23
                for _ in range(narr):
                    cnt = struct.unpack_from('>H', cfg, off + 1)[0]
                    off += 3
                    for _ in range(cnt):
                        ln = struct.unpack_from('>H', cfg, off)[0]
                        off += 2
                        sets.append(('param set', cfg[off:off + ln]))
                        off += ln
        except (IndexError, struct.error):
            pass
        return box.decode(), cfg, sets
    return None, b'', []


def video_samples(buf, bs, limit=24):
    """First `limit` (offset, size) of the video track, from its own sample table.

    mdat is interleaved -- on these clips it opens with another track's chunk --
    so NAL walking has to start at real video sample offsets, not at mdat+0.
    """
    traks = [(o, s) for t, o, s, hd, d, tr in bs if t == b'trak']
    for to, ts in traks:
        inside = [(t, o, s, hd) for t, o, s, hd, d, tr in bs if to <= o < to + ts]
        if not any(t in VIDEO_ENTRY for t, _, _, _ in inside):
            continue
        tbl = {}
        for t, o, s, hd in inside:
            if t in (b'stsz', b'stsc', b'stco', b'co64'):
                tbl[t] = (o + hd, s - hd)
        if not {b'stsz', b'stsc'} <= set(tbl) or not ({b'stco', b'co64'} & set(tbl)):
            return []
        o, _ = tbl[b'stsz']
        ssize, scount = struct.unpack_from('>II', buf, o + 4)
        sizes = ([ssize] * scount if ssize else
                 [struct.unpack_from('>I', buf, o + 12 + 4 * i)[0]
                  for i in range(min(scount, limit + 8))])
        o, _ = tbl[b'stsc']
        ns = struct.unpack_from('>I', buf, o + 4)[0]
        stsc = [struct.unpack_from('>III', buf, o + 8 + 12 * i) for i in range(ns)]
        if b'co64' in tbl:
            o, _ = tbl[b'co64']
            nc = struct.unpack_from('>I', buf, o + 4)[0]
            chunks = [struct.unpack_from('>Q', buf, o + 8 + 8 * i)[0] for i in range(nc)]
        else:
            o, _ = tbl[b'stco']
            nc = struct.unpack_from('>I', buf, o + 4)[0]
            chunks = [struct.unpack_from('>I', buf, o + 8 + 4 * i)[0] for i in range(nc)]

        out, si = [], 0
        for ci, coff in enumerate(chunks, start=1):
            spc = 1
            for first, per, _ in stsc:
                if ci >= first:
                    spc = per
            pos = coff
            for _ in range(spc):
                if si >= len(sizes) or len(out) >= limit:
                    return out
                out.append((pos, sizes[si]))
                pos += sizes[si]
                si += 1
        return out
    return []


def classify_header(b0, b1):
    """Strict validity both ways for one NAL header."""
    avc_ok = hevc_ok = False
    avc_t = b0 & 0x1F
    hevc_t = (b0 >> 1) & 0x3F
    if not (b0 & 0x80):
        avc_ok = 1 <= avc_t <= 23
        layer = ((b0 & 1) << 5) | (b1 >> 3)
        tid = b1 & 0x07
        hevc_ok = layer == 0 and tid != 0 and hevc_t <= 40
    return (avc_ok, avc_t), (hevc_ok, hevc_t)


def length_prefixed(buf, nlen=4):
    hits, off, n = [], 0, len(buf)
    while off + nlen <= n:
        ln = int.from_bytes(buf[off:off + nlen], 'big')
        if ln == 0 or off + nlen + ln > n:
            break
        hits.append(off + nlen)
        off += nlen + ln
    return hits


def annexb(buf):
    hits, i = [], 0
    while True:
        i = buf.find(b'\x00\x00\x01', i)
        if i < 0:
            break
        hits.append(i + 3)
        i += 3
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('path')
    ap.add_argument('--limit', type=lambda s: int(s, 0), default=16 << 20)
    a = ap.parse_args()

    buf = open(a.path, 'rb').read(a.limit)
    print('%s  read %d bytes' % (a.path.split('/')[-1], len(buf)))
    bs = walk(buf, 0, len(buf))

    verdicts = []

    # --- signal 1: the configuration box -------------------------------------
    for typ, off, size, hdr, depth, trunc in bs:
        if typ in VIDEO_ENTRY:
            w, h = struct.unpack_from('>HH', buf, off + hdr + 24)
            print('  sample entry %s  %dx%d' % (typ.decode(), w, h))
            kind, cfg, sets = parse_config(buf, off, hdr, size, typ)
            if not kind:
                print('    no avcC/hvcC found')
                continue
            print('    %s, %d bytes, %d parameter set(s)' % (kind, len(cfg), len(sets)))
            as_avc = as_hevc = 0
            for label, nal in sets:
                if not nal:
                    continue
                b0 = nal[0]
                b1 = nal[1] if len(nal) > 1 else 0
                (ao, at), (ho, ht) = classify_header(b0, b1)
                avc_name = AVC_T.get(at, '?')
                hevc_name = HEVC_T.get(ht, '?')
                print('      %2d B  byte0=0x%02X  AVC:%-8s(%2d)  HEVC:%-8s(%2d)'
                      % (len(nal), b0, avc_name, at, hevc_name, ht))
                if at in (7, 8):
                    as_avc += 1
                if ht in (32, 33, 34):
                    as_hevc += 1
            if as_hevc >= 2 and as_hevc > as_avc:
                print('    -> config box carries HEVC parameter sets  *** HEVC ***')
                verdicts.append('HEVC')
            elif as_avc >= 2:
                print('    -> config box carries AVC SPS+PPS')
                verdicts.append('AVC')
            else:
                print('    -> parameter sets inconclusive')

    # --- signal 2: the slice NALs --------------------------------------------
    samples = video_samples(buf, bs)
    if samples:
        got = [(o, n) for o, n in samples if o + n <= len(buf)]
        print('  video sample table: %d samples located, %d fully inside the bytes read'
              % (len(samples), len(got)))
        blob = b''.join(buf[o:o + n] for o, n in got)
        starts, base = [], 0
        for o, n in got:
            starts += [base + p for p in length_prefixed(buf[o:o + n])]
            base += n
        pay = blob
        for name, starts in (('sample NALs', starts),
                             ('Annex-B in samples', annexb(blob))):
            if not starts:
                continue
            a_ok = h_ok = 0
            at_c, ht_c = {}, {}
            for p in starts:
                if p + 1 >= len(pay):
                    continue
                (ao, at), (ho, ht) = classify_header(pay[p], pay[p + 1])
                if ao:
                    a_ok += 1
                    at_c[at] = at_c.get(at, 0) + 1
                if ho:
                    h_ok += 1
                    ht_c[ht] = ht_c.get(ht, 0) + 1
            n = len(starts)
            print('    %s: %d NALs   strict-valid as AVC %d/%d (%.0f%%), as HEVC %d/%d (%.0f%%)'
                  % (name, n, a_ok, n, 100.0 * a_ok / n, h_ok, n, 100.0 * h_ok / n))
            # Validity alone cannot separate these: a real HEVC TRAIL_R NAL
            # (02 01 ..) is also a structurally valid AVC type-2 header.  So
            # score on what an encoder actually emits.  AVC types 2/3/4 are
            # data-partitioned slices -- Extended profile only, which no camera
            # produces -- so an "AVC" reading made of type 2 is itself evidence
            # the stream is not AVC.
            a_sl = sum(c for t, c in at_c.items() if t in AVC_REAL_SLICE)
            h_sl = sum(c for t, c in ht_c.items() if t in HEVC_SLICE)
            a_part = sum(c for t, c in at_c.items() if t in AVC_PARTITION)
            print('      as AVC : plausible slices %-5d types %s%s'
                  % (a_sl, dict(sorted(at_c.items())),
                     '   (%d are data-partition types -- implausible)' % a_part if a_part else ''))
            print('      as HEVC: plausible slices %-5d types %s'
                  % (h_sl, dict(sorted(ht_c.items()))))
            if n >= 8:
                if h_sl > a_sl and h_sl >= n * 0.6:
                    print('      -> slices read as HEVC')
                    verdicts.append('HEVC')
                elif a_sl > h_sl and a_sl >= n * 0.6:
                    print('      -> slices read as AVC')
                    verdicts.append('AVC')
                else:
                    print('      -> slices inconclusive (AVC %d vs HEVC %d of %d)'
                          % (a_sl, h_sl, n))
            break
    else:
        print('  could not locate video samples in the bytes read')

    print()
    if verdicts and all(v == verdicts[0] for v in verdicts):
        print('  VERDICT: %s  (%d signal(s) agree)' % (verdicts[0], len(verdicts)))
        return 0
    if verdicts:
        print('  VERDICT: signals disagree %s -- do not conclude' % verdicts)
        return 1
    print('  VERDICT: no usable signal')
    return 1


if __name__ == '__main__':
    sys.exit(main())
