#!/usr/bin/env python3
"""Build minimal but structurally real MOV fixtures to test nal_scan.py.

The one that matters is hevc_in_avc1.mov: the fp's muxer only writes avc1/avcC
(C071F3E8/C071FCD8), so if mode 8 makes the encoder emit HEVC, the expected
artifact is an avc1 sample entry whose avcC holds HEVC parameter sets and whose
samples are HEVC NALs.  Without a real mode-8 clip to hand, this is the only
positive control available -- and a classifier that cannot call this HEVC
cannot be trusted to call the real thing either.

Each fixture carries a genuine stsz/stsc/stco sample table, because mdat is
interleaved on real clips and nal_scan walks samples, not mdat+0.
"""
import struct
import os


def box(typ, *parts):
    body = b''.join(parts)
    return struct.pack('>I4s', 8 + len(body), typ) + body


def full(typ, ver_flags, *parts):
    return box(typ, struct.pack('>I', ver_flags), *parts)


def hevc_nal(t, payload=b'\x11\x22\x33\x44'):
    return bytes([(t << 1) & 0x7E, 0x01]) + payload        # layer 0, tid_plus1 = 1


def avc_nal(t, ref=3, payload=b'\x64\x00\x32\xac'):
    return bytes([(ref << 5) | t]) + payload


def visual_entry(fourcc, cfg_box, w=1920, h=1080):
    body = (b'\x00' * 6 + struct.pack('>H', 1)            # reserved, data_ref_index
            + b'\x00' * 16                                 # pre_defined / reserved
            + struct.pack('>HH', w, h)
            + struct.pack('>II', 0x00480000, 0x00480000)   # resolutions
            + b'\x00' * 4
            + struct.pack('>H', 1)                         # frame_count
            + b'\x00' * 32                                 # compressorname
            + struct.pack('>H', 24)                        # depth
            + b'\xff\xff')                                 # pre_defined
    return box(fourcc, body, cfg_box)


def avcC(param_sets, length_size=4):
    """AVCDecoderConfigurationRecord.  param_sets[0] is treated as the SPS list
    head; everything after the first goes in the PPS list, which is what the
    layout allows -- the point is the NAL header bytes, not the semantics."""
    sps = param_sets[:1]
    pps = param_sets[1:]
    out = bytes([1, 0x64, 0x00, 0x32, 0xFC | (length_size - 1), 0xE0 | len(sps)])
    for s in sps:
        out += struct.pack('>H', len(s)) + s
    out += bytes([len(pps)])
    for p in pps:
        out += struct.pack('>H', len(p)) + p
    return box(b'avcC', out)


def hvcC(param_sets, length_size=4):
    out = bytes([1]) + b'\x01\x60\x00\x00\x00' + b'\x90' + b'\x00' * 10
    out += struct.pack('>HBB', 0xF000, 0xFC, 0xFD)[:4]
    out = out[:21] + bytes([0xF0 | (length_size - 1)]) + bytes([len(param_sets)])
    for s in param_sets:
        out += bytes([(s[0] >> 1) & 0x3F]) + struct.pack('>H', 1) + struct.pack('>H', len(s)) + s
    return box(b'hvcC', out)


def build(path, fourcc, cfg, samples):
    lp = b''.join(struct.pack('>I', len(n)) + n for n in samples)
    sizes = [len(struct.pack('>I', len(n)) + n) for n in samples]

    stsd = full(b'stsd', 0, struct.pack('>I', 1), visual_entry(fourcc, cfg))
    stsz = full(b'stsz', 0, struct.pack('>II', 0, len(sizes)),
                b''.join(struct.pack('>I', s) for s in sizes))
    stsc = full(b'stsc', 0, struct.pack('>I', 1), struct.pack('>III', 1, len(sizes), 1))
    # chunk offset is filled in once the header length is known
    stco_ph = full(b'stco', 0, struct.pack('>I', 1), struct.pack('>I', 0))
    stbl = box(b'stbl', stsd, stsz, stsc, stco_ph)
    moov = box(b'moov', box(b'trak', box(b'mdia', box(b'minf', stbl))))
    ftyp = box(b'ftyp', b'qt  ' + struct.pack('>I', 0x200))
    head = ftyp + moov
    mdat_payload_off = len(head) + 8

    stco = full(b'stco', 0, struct.pack('>I', 1), struct.pack('>I', mdat_payload_off))
    stbl = box(b'stbl', stsd, stsz, stsc, stco)
    moov = box(b'moov', box(b'trak', box(b'mdia', box(b'minf', stbl))))
    head = ftyp + moov
    assert len(head) + 8 == mdat_payload_off, 'header length shifted'

    open(path, 'wb').write(head + box(b'mdat', lp))
    print('  %-24s %6d B  %s, %d samples' % (os.path.basename(path),
                                             os.path.getsize(path), fourcc.decode(), len(samples)))


d = 'selftest'
os.makedirs(d, exist_ok=True)

hevc_slices = [hevc_nal(19)] + [hevc_nal(1) for _ in range(11)]
avc_slices = [avc_nal(5)] + [avc_nal(1, 2) for _ in range(11)]
hevc_params = [hevc_nal(32), hevc_nal(33), hevc_nal(34)]
avc_params = [avc_nal(7), avc_nal(8)]

print('fixtures:')
build(f'{d}/avc_stock.mov', b'avc1', avcC(avc_params), avc_slices)
build(f'{d}/hevc_in_avc1.mov', b'avc1', avcC(hevc_params), hevc_slices)
build(f'{d}/hevc_hvc1.mov', b'hvc1', hvcC(hevc_params), hevc_slices)
