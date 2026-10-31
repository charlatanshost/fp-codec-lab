#!/usr/bin/env python3
"""Put the HEVC mode-8 patch into a card's AutoRun.txt, with a readback file.

The experiment, with no USB shell involved:

  C0725690 is the SDK-layer caller that picks the video codec mode.  Two routes
  reach it -- mode = C0709158(w,h,fps) captured at C0725694, or, when SDK param
  +0x68 == 1, a forced 0 at C0725698 that never calls C0709158 at all.  Both are
  patched to `movs r5,#8`, so whichever route stock recording takes, the codec
  opens in mode 8: the HEVC mirror of AVC mode 0 (C0709C38 shares mode 0's
  branch for 8 and additionally sets 300C03F8.bit0, which selects the HEVC
  level/geometry path).

Verified against FP__V502.bin seg0 (SHA-256 aaa5208a...), both round-tripped
through capstone, both word-aligned, each preserving the following instruction:

  0xC0725694  stock 0xE0004605  mov r5,r0   -> 0xE0002508  movs r5,#8
  0xC0725698  stock 0xA8012500  movs r5,#0  -> 0xA8012508  movs r5,#8

Doing this from the AutoRun rather than over USB is deliberate: the words are
written at boot, before the codec code has ever run, so no stale instruction
cache line can shadow them -- the same reasoning fpSup's README gives for its
own seven patches.  There is no cache maintenance primitive in the shell.

`mem save` writes the patched words back out to the card so the result is not
read from a recording whose patch never landed.  Those two words are the one
thing that separates "HEVC does not work" from "the patch never took effect".

Shoot FHD 24p: C070D858 returns 5 only for 3840x2160 and 2560x1440 at the 2997
rate code, 1440x720, 854x480 and 640x480, so FHD 24p is a mode-0 setting and 8
is the right mirror for it.  Forcing 8 on a mode-5 setting would pair HEVC with
mode 0's level tables.

usage: add_hevc.py /path/to/card [--dry-run] [--revert]
"""
import argparse
import os
import shutil
import sys

ANCHORS = (b'mem set 0xC01E7704 0x4628E01E\n',   # beside the NOPTP lines, if present
           b'mem set 0xC03E4698 0xE3A08010\n',   # else the last screen patch
           b'mem set 0xC0CF379C 0x00000040\n')   # else a classic card's last descriptor word
MUST_PRECEDE = (b'mem set 0xC072DE64', b'display text fpSup')

NEW = (b'mem set 0xC0725694 0xE0002508\n'
       b'mem set 0xC0725698 0xA8012508\n'
       b'mem save \\HEVCCHK.BIN 0xC0725694,,0x10\n')
MARK = b'mem set 0xC0725694'
PAD = b'# pad -- see PAD_TO: mode 7 overwrites but does not truncate\n'

# --hvc1: rename the MOV sample entry and config box.  'avc1' and 'avcC' each
# occur exactly ONCE in the whole firmware image, in a box-type -> writer
# dispatch table in .data (12-byte entries: fourcc, 0, writer fn), so this
# table is the only place those names come from:
#   C2EF4F40  'avc1' 0x31637661 -> 'hvc1' 0x31637668   writer C071BFD0
#   C2EF4F4C  'avcC' 0x43637661 -> 'hvcC' 0x43637668   writer C071C078
# One byte each ('a' 0x61 -> 'h' 0x68), both word-aligned, both inside seg0.
# This does NOT produce a valid hvcC: that record's layout differs completely
# from AVCDecoderConfigurationRecord, which C071FCD8 is what builds.  The point
# is only to see whether the write still fails -- if a file appears, its mdat
# slices answer the real question, and nal_scan reads those without needing a
# valid config box (proven against the hvc1 fixture).
NEW_FOURCC = (b'mem set 0xC2EF4F40 0x31637668\n'
              b'mem set 0xC2EF4F4C 0x43637668\n'
              b'mem save \\FOURCC.BIN 0xC2EF4F40,,0x10\n')
MARK_FOURCC = b'mem set 0xC2EF4F40'

# --probe: capture the PREVIOUS session's codec state, retained across a warm
# restart (power switch, not battery -- DRAM self-refresh).  These run first,
# before anything this boot can overwrite them.
#
#   C2F30040   the global C0709C38 stores the opened mode into.  Its initial
#              value in .data is 0x00000001, so three answers are distinct:
#              8 = opened in mode 8, 0 or 5 = opened in an AVC mode,
#              1 = .data was re-initialised and the probe says nothing.
#   300C0000..03FF  the codec register block.  C070AC88 puts the output
#              bitstream address in 0110/0114 as bits 16..28 and 0..15 of
#              (addr - 0x40000000) >> 3, its size in 0384, and C0709A80 sets
#              03F8.bit0 for modes 8-11.  If these survive, they give the one
#              thing offline analysis could not: where the encoder wrote.
NEW_PROBE = (b'mem save \\MODEG.BIN 0xC2F30040,,0x10\n'
             b'mem save \\REGS.BIN 0x300C0000,,0x400\n')
MARK_PROBE = b'mem save \\MODEG.BIN'


def restore_length(out, target):
    """Give back pad lines until it fits, then make up the remainder exactly."""
    while len(out) > target and PAD in out:
        out = out.replace(PAD, b'', 1)
    short = target - len(out)
    if short > 0:
        filler = b'#' + b'-' * (short - 2) + b'\n' if short >= 2 else b'\n' * short
        if PAD in out:
            out = out.replace(PAD, filler + PAD, 1)
        else:
            out += filler
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('card')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--hvc1', action='store_true',
                    help="also rename the MOV sample entry/config box to hvc1/hvcC")
    ap.add_argument('--probe', action='store_true',
                    help="dump the previous session's mode global and codec registers first")
    a = ap.parse_args()
    block = (NEW_PROBE if a.probe else b'') + NEW + (NEW_FOURCC if a.hvc1 else b'')

    path = os.path.join(a.card, 'AutoRun.txt')
    if not os.path.exists(path):
        print('no AutoRun.txt at %s' % a.card)
        return 2
    d = open(path, 'rb').read()
    target = len(d)
    print('%s  %d bytes' % (path, target))

    if a.revert:
        if MARK not in d and MARK_FOURCC not in d and MARK_PROBE not in d:
            print('  no HEVC lines are there -- nothing to do')
            return 0
        out = d.replace(NEW_FOURCC, b'', 1).replace(NEW_PROBE, b'', 1).replace(NEW, b'', 1)
        out = restore_length(out, target)
        if MARK in out or MARK_FOURCC in out or MARK_PROBE in out:
            print('  could not remove the lines cleanly; not writing')
            return 1
        print('  HEVC lines removed, length %d' % len(out))
    else:
        if MARK in d or MARK_FOURCC in d or MARK_PROBE in d:
            print('  HEVC lines are already there -- revert first if changing them')
            return 0
        anchor = next((x for x in ANCHORS if d.count(x) == 1), None)
        if anchor is None:
            print('  no usable anchor line; not guessing. looked for:')
            for x in ANCHORS:
                print('    %-34s count=%d' % (x.decode().strip(), d.count(x)))
            return 1
        print('  anchor: %s' % anchor.decode().strip())
        out = restore_length(d.replace(anchor, anchor + block, 1), target)

        pos = out.find(MARK)
        for m in MUST_PRECEDE:
            p = out.find(m)
            if p != -1 and pos > p:
                print('  landed after %r (%d > %d); refusing' % (m.decode(), pos, p))
                return 1
        print('  inserted at byte %d; later markers: %s' % (pos, ', '.join(
            '%s@%d' % (m.decode(), out.find(m)) for m in MUST_PRECEDE if out.find(m) != -1)))

    if len(out) != target:
        print('  length not preserved (%d vs %d); refusing' % (len(out), target))
        return 1
    print('  length preserved: %d bytes' % len(out))
    if a.dry_run:
        print('  --dry-run: nothing written')
        return 0

    bak = path + ('.before-hevc' if not a.revert else '.before-revert')
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
        print('  backup: %s' % bak)
    with open(path, 'wb') as f:
        f.write(out)
        f.flush()
        os.fsync(f.fileno())
    if open(path, 'rb').read() != out:
        print('  VERIFY FAILED -- the card did not take the write')
        return 1
    print('  written and verified')
    return 0


if __name__ == '__main__':
    sys.exit(main())
