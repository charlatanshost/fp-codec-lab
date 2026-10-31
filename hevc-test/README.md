# HEVC mode 8 on the fp: first camera test

2026-10-29/30. **First camera work in this repo.** Marks as elsewhere: **[C]** confirmed
(ROM instructions, measurement, or a readback from the camera) · **[I]** inference · **[P]** still
needs the camera.

Image for all offline work: `seg0_c0000000.bin`, SHA-256
`aaa5208a028d9c4aebb9cc8614add723d456e96b2a95914f433079954320e622`. No firmware or firmware
memory dumps are committed here; the words quoted below are quoted, not shipped.

## Result in one line

Codec mode 8 can be forced and the patch demonstrably lands, but **recording fails**, and the
reason is not the codec: **the firmware contains no HEVC muxing support at all** [C]. Whether the
encoder emits an HEVC bitstream in mode 8 is **still unproven** [P] — every route to that evidence
was blocked, and the one that remains needs the USB shell.

## The patch

`C0725690` (SDK layer) reaches the codec mode by two routes, and this matters more than it looks:

```
C0725678  ldr r1,[sp,#4]
C072567A  ldr r1,[r1,#0x68]
C072567C  cmp r1,#1
C072567E  beq C0725698        -> C0725698  movs r5,#0   mode forced, C0709158 NEVER called
C0725690  bl  C0709158                                  (the wrapper -> C070D858, returns 0 or 5)
C0725694  mov r5,r0                                     mode = the return value
```

So when SDK param `+0x68 == 1`, `C0709158` is never called. Patching only that wrapper — which is
what [`codec-driver-map`](../codec-driver-map/README.md) §5 proposed — can therefore produce a
stock AVC clip and read as "HEVC does not work" when the patch simply never ran. Both routes are
patched instead [C]:

| address | stock | patched | route |
|---|---|---|---|
| `0xC0725694` | `0xE0004605` `mov r5,r0` | `0xE0002508` `movs r5,#8` | via `C0709158` |
| `0xC0725698` | `0xA8012500` `movs r5,#0` | `0xA8012508` `movs r5,#8` | forced-0 bypass |

Both round-tripped through capstone, both word-aligned, each preserving the following instruction.

Applied from the card's `AutoRun.txt` rather than over USB, deliberately: AutoRun writes them at
boot, before the codec code has ever executed, so no stale instruction-cache line can shadow them.
There is no cache-maintenance primitive in the shell, and this is the same reasoning fpSup's README
gives for its own seven patches.

## Offline findings

### 1. The full mode-5 table [C]

`C070D858(w, h, rate)` returns 5 for these and **0 for everything else**. Rate codes are fps x 100.
This extends the examples in `codec-driver-map` §2 to the whole function:

| size | rates returning mode 5 |
|---|---|
| 3840x2160, 3840x1920 | 2397, 2500, 2997 |
| 2880x1920, 2048x1536, 2560x1440 | 2997 |
| 1920x1080 | 2997, 11988 |
| 1280x720 | 11988, 23976 |
| 1440x720 | 11988 |
| 854x480, 640x480 | 23976 |

Mode 5 tracks the high-frame-rate and large-readout settings. Two consequences that decide how any
HEVC test must be set up:

- **UHD is a mode-5 setting at every rate the fp offers**, so an HEVC test at UHD is a **mode 10**
  experiment, not mode 8.
- **FHD is not automatically mode 0** — 1920x1080 at 29.97 and at 119.88 are mode 5. Only the other
  FHD rates (23.98/24/25/50/60) are mode 0, where 8 is the right mirror.

Cross-check: the 2026-10-24 audit's 264 captured descriptors split FHD 76 cases at mode 0 and 56 at
mode 5, independently consistent with this table.

### 2. The firmware cannot name an HEVC track [C]

Whole-image string search across `seg0` and `seg1`:

```
avc1   1 occurrence (C2EF4F40)      hvc1   0      hev1   0      hvcC   0
avcC   1 occurrence (C2EF4F4C)      "HEVC" 0      "H265" 0      "h265" 0
```

The MOV writer is table-driven: a box-type -> writer dispatch table in `.data`, 12-byte entries of
`{fourcc, 0, writer fn}`:

| fourcc | writer | | fourcc | writer |
|---|---|---|---|---|
| `stbl` | `C071BE70` | | `sowt` | `C071C078` |
| `stsd` | `C071BEB8` | | `tmcd` | `C071C2B8` |
| `avc1` | `C071BF28` | | `stts` | `C071C368` |
| `avcC` | `C071BFD0` | | `ctts` | `C071C3E0` |
| | | | `stss` | `C071C428` |

`sowt` (PCM) and `tmcd` (timecode) account for the other two tracks in a stock fp MOV. Because each
of `avc1`/`avcC` occurs exactly once, that table is the only source of those names [C].

### 3. The config-record builder is AVC-shaped [C]

`C071FCD8` builds an `AVCDecoderConfigurationRecord` from a 0x100-byte parameter-set blob at
`r5+0x28`, whose length is `r5+0x128`; it fixes constraint flags by `profile_idc` (byte 5 compared
against `0x42` Baseline and `0x4D` Main, writing byte 6), and `r5+0x130`/`+0x132` are the width and
height that go into the sample entry. `hvcC`'s record layout is nothing like this, so HEVC muxing
needs a new builder, not a patch [I].

**Not found:** what fills that blob. There is **no store to a register offset `#0x128` anywhere in
the image**, so the struct is written through a different base decomposition; pinning the
parameter-set extractor needs real xref tooling, not the linear capstone scans used here [P].

## Camera results

Reported separately from the offline work, per this repo's rules. SIGMA fp Ver.5.02, fpSup loaded
from the card, FHD 1920x1080 **23.98p**, MOV — a mode-0 setting, so 8 is the correct mirror.

Every run wrote `\HEVCCHK.BIN` from the AutoRun (`mem save 0xC0725694,,0x10`), so the patch state is
a camera-side readback, not an assumption.

| run | patch | readback | behaviour |
|---|---|---|---|
| 3 boots | mode 8 | both words patched [C] | recorded, then write light stayed on, **two beeps, no file** |
| 1 boot | mode 8 + `hvc1`/`hvcC` rename | all four words patched [C] | **froze** during the attempt |
| 2 boots | mode 8 + retention probe | both words patched [C] | froze during the attempt |

Supporting observations:

- **Not storage.** It failed to an external SSD as well as to the SD card, and the identical setting
  records normally with the patch reverted (9.009 s, 7.75 MB/s, verdict AVC).
- **The failure is at stop/finalise, not at record start** [C]. The camera showed recording and the
  write light appeared afterwards and stuck. (An earlier inference here was wrong — see Corrections.)
- **The `hvc1`/`hvcC` rename takes effect and makes things worse** [C]: a clean abort becomes a
  hang, consistent with the muxer accepting the name and then running the AVC-shaped builder of §3.
- **Warm-restart retention does not work** [C]. After a power-switch restart (no battery pull),
  `C2F30040` read back `0x00000001` — its `.data` initial value — and all 256 words of
  `0x300C0000..03FF` read zero. So nothing can be recovered post-mortem from the card, which closes
  the card-only probe route entirely.
- Reading the codec register block at boot is **safe**, returning zeros without hanging [C].

## What is still open

**Does mode 8 make the encoder emit HEVC?** [P] Unproven. The muxer will not write a file, so there
is no bitstream to inspect; warm-restart retention does not preserve the registers that locate the
output buffer; and the codec hardware and its control ISA cannot be emulated offline.

The one remaining route is the USB shell, reading live during recording — while REC rolls, the codec
is open and the encoder is producing data, which is before the finalise failure:

```
mem get 0xC2F30040,,0x10      the mode C0709C38 actually opened with
mem get 0x300C0100,,0x20      0110/0114: output bitstream address
mem get 0x300C03F8,,0x8       bit 0 set for modes 8-11
mem get 0x300C0380,,0x10      0384: output size / unit
```

`C070AC88` stores bits 16..28 of `(addr - 0x40000000) >> 3` at `0x300C0110` and bits 0..15 at
`0x300C0114`, so those two words give the buffer, which `mem save` can dump for offline analysis.
`C2F30040` is interpretable because its `.data` initial value is `0x00000001`: **8** means the codec
opened in mode 8, **0 or 5** means the patch never reached codec open, **1** means it never opened.

## Tools

| file | what it does |
|---|---|
| `tools/nal_scan.py` | decides HEVC vs AVC for a clip or a raw bitstream dump |
| `tools/movinfo.py` | size, rate and codec fourcc of a clip — which is what says whether a clip is a mode-0 or mode-5 setting |
| `tools/make_fixtures.py` | builds the MOV fixtures that validate `nal_scan.py`, including the HEVC-in-`avc1` case mode 8 should produce |
| `tools/patch_autorun.py` | adds/removes the patch lines in a card's `AutoRun.txt`, preserving its exact length, with a `mem save` readback |

### nal_scan is validated both ways

It uses two independent signals and reports them separately: the sample entry's `avcC`/`hvcC`
parameter-set NAL headers, and the video track's slice NALs located through its own sample table.

```sh
python3 tools/make_fixtures.py
python3 tools/nal_scan.py selftest/avc_stock.mov       # AVC,  2 signals agree
python3 tools/nal_scan.py selftest/hevc_in_avc1.mov    # HEVC, 2 signals agree
python3 tools/nal_scan.py selftest/hevc_hvc1.mov       # HEVC, 2 signals agree
```

Three real fp clips (UHD 29.97, FHD 29.97, FHD 23.976) also come out AVC with both signals agreeing.

Three traps it exists to avoid, all of which caught an earlier version of it:

1. **A stock AVC clip read with HEVC's 2-byte header reports plausible "VPS"**, because AVC's `0x41`
   non-IDR byte decodes to type 32. Requiring VPS+SPS+PPS together, and comparing both readings,
   is what rules that out.
2. **A real HEVC `TRAIL_R` NAL (`02 01 ..`) is also a structurally valid AVC type-2 header**, so
   validity alone cannot separate them. Scoring on what an encoder actually emits does: AVC types
   2/3/4 are data-partitioned slices, Extended profile only, which no camera produces.
3. **In a well-formed MOV the parameter sets are out of band in `avcC`, not in `mdat`**, and `mdat`
   is interleaved — a stock fp clip opens with another track's chunk. Walking NALs from `mdat+0`
   is meaningless; the sample table is required.

## Reproducing

```sh
# offline
python3 ../codec-driver-map/dis.py seg0_c0000000.bin C0725650 40     # the two routes
python3 ../codec-driver-map/dis.py seg0_c0000000.bin C070D858 400    # the mode table
python3 ../codec-driver-map/dis.py seg0_c0000000.bin C071FCD8 55     # the avcC builder

# card (camera powered off, card in a reader)
python3 tools/patch_autorun.py /path/to/card              # mode 8 + readback
python3 tools/patch_autorun.py /path/to/card --hvc1       # also rename the box types
python3 tools/patch_autorun.py /path/to/card --revert     # back to stock
```

Boot with USB unplugged, let the bar finish, shoot FHD 23.98p MOV. `\HEVCCHK.BIN` on the card must
read `08 25 00 e0 08 25 01 a8` or the patch did not land and nothing else means anything.

## Corrections

Kept because each was wrong in a way the instrumentation caught, and the instrumentation is the
point:

- **"The failure is upstream of the muxer."** Inferred from the `CINEMA` directory's mtime never
  changing — no file was ever created. The camera's actual behaviour (recording ran, write light
  came on afterwards and stuck) puts the failure at finalise instead. A directory mtime does not
  localise a failure; the operator's observation did.
- **"The boot freeze was my MMIO read."** The retention probe read `0x300C0000..03FF` at boot, and
  reading an unclocked peripheral can hang the bus. But `\REGS.BIN` exists from both boots, so the
  read completed and the freeze was in the recording attempt.
- **"The USB shell is broken on the camera."** Hours went into the card and the sup configuration on
  the assumption the worker was not arming EP 0x01. The shell answers from a **Windows** host on the
  same camera and card, so the camera side was fine and the failure was host-side on Linux [C]. No
  conclusion here depends on the shell, but the time spent does.
