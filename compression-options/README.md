# Raw compression options on the fp

2026-10-29. Offline: built from fpSup's research notes plus our own measurements on fp cDNG
frames. Marks: **[C]** confirmed (in fpSup notes or measured here) · **[I]** inference ·
**[P]** needs the camera. File paths in backticks are relative to `../../fpSup-main/`.

## Ground rule: only hardware paths are viable in-camera

The fp has a single Cortex-A7-class core (`research/firmware/notes/CPU_GLOBAL_CONFIG.md`), and it
is fully loaded while recording (`projects/sys-perf/README.md`). Any codec that runs in software
is out for recording: CineForm needs 30–37 ARM instructions per pixel ([../cineform](../cineform/README.md)),
and software LJ92, JPEG XL, Deflate/Zstd, VC-2 and HTJ2K all hit the same wall. [C for CineForm, I for the rest]

## Everything that can compress on the fp

From fpSup's hardware survey (`research/imaging-hw/notes/RAW_COMPRESSION_RESEARCH.md`, corrected
2026-10-15) and our driver map ([../codec-driver-map](../codec-driver-map/README.md)):

| Option | Runs on | Takes Bayer raw? | Status |
|---|---|---|---|
| LJ92 / lossless JPEG, `0x300D` (`XC_HalLjpeg`) | Hardware | Yes; 10, 12, 14, 16-bit (no 8-bit code) | fpSup prototype (fpLossless) works at FHD on the camera [C] |
| Lossy baseline JPEG, `0x301B` (`XC_SigproJpegComp`) | Hardware DSP | Unknown; fpSup notes disagree | Needs a camera probe; hard to drive (live frame graph + DSP session) [P] |
| H.264 (`0x300C`); HEVC branch in the same driver | Hardware | Not directly: processed 8-bit 4:2:0 input today | Driver mapped; HEVC modes 8–11 found [C]; whether HEVC works [P] |
| LZ4 / RLE library | CPU | Generic data | Poor on noisy raw, and costs CPU [I] |
| Bit-depth reduction (10-bit linear; 8-bit with curve) | Hardware (`RAWOUT`) | Yes | Already in the stock menu [C] |

"LJ92" is the common name for lossless JPEG (ITU T.81 lossless mode), i.e. DNG `Compression=7`.
It is the same thing as the `0x300D` engine; Magic Lantern's lossless raw uses Canon's equivalent.

## Lossless: the LJ92 engine

fpSup's fpLossless (`projects/lossless-sup/notes/PROGRESS_20260930.md`): the camera has compressed
its own FHD footage with the engine and written decodable DNGs at 44% of stock size [C]. Open
problems listed there: frame identity between submit and write, intermittent engine refusals,
memory at larger sizes.

### Engine time budget

`engine_budget.py`, using fpSup's measured cost model (reproduces their published optima):

| Frame size | Engine time | 23.976p | 25p | 29.97p |
|---|---|---|---|---|
| FHD 1936×1090 | 13.9 ms | 33% | 35% | 42% |
| OG3K 3024×2010 | 34.3 ms | 82% | 86% | 103% |
| 3840×1600 (2.4:1) | 33.9 ms | 81% | 85% | 102% |
| 3840×1920 (2:1) | 40.0 ms | 96% | 100% | 120% |
| 3840×2048 | 42.5 ms | 102% | 106% | 127% |
| UHD 3840×2160 | 44.9 ms | 108% | 112% | 135% |

- FHD fits easily. OG3K and a 3840×1600 crop fit at 24/25p **only if encoding and card writing
  run in parallel**; fpLossless currently runs them in series. Full UHD does not fit at any rate. [C model, I conclusions]
- Engine speed is per sample, independent of bit depth: lower bit depth shrinks files, not engine time. [C, fpSup]

### Stacking lower bit depth with LJ92

`ljpeg_ratio_estimate.py` on two fp frames (same-colour left-neighbour residual entropy, the
method fpSup uses for the engine). Ratios are against 12-bit packed stock files:

| Frame | 12-bit (stock) | 10-bit linear (`>>2`) | 10-bit square-root curve |
|---|---|---|---|
| `rawview/.../A001_031_f000001.DNG` (real scene, flat) | 4.29 b/px → 2.80:1 | 2.49 b/px → 4.82:1 | 2.85 b/px → 4.22:1 |
| `open-gate/.../A001_182_...000001.DNG` (dark, noisy) | 3.54 b/px → 3.39:1 | 1.76 b/px → 6.80:1 | 4.05 b/px → 2.96:1 |

- Going 12 → 10-bit linear saves about **1.8 bits per pixel** after compression. Applied to
  fpSup's 2.0:1 design value for 12-bit, that suggests roughly **2.8–3:1** overall. [I]
- Both frames are flatter or darker than typical footage, so absolute ratios are optimistic; the
  relative gain is the useful number. [C measured, I generalised]
- The stock 10-bit cDNG is **linear** (the writer adds a `LinearizationTable` only in 8-bit mode,
  `research/firmware/notes/cinemadng-header/HDR_PRODUCERS.md` §2.11), so the 12 → 10 step drops
  the two low bits and is lossy, mostly in deep shadows. A curve keeps shadow detail but, on dark
  frames, costs bits because it expands the shadows (second row). [C]
- Native **8-bit** cDNG already uses a hardware curve plus `LinearizationTable` (1.5:1, no CPU),
  but cannot be combined with LJ92: the engine has no 8-bit mode. [C]

### Hardware + CPU tile split (idea)

A DNG frame's tiles must share one compression type, not one encoder. The engine could encode
most tiles and the CPU the remaining 10–15% in software LJ92, getting full UHD past the engine's
limit. Only viable if the CPU has spare time during recording, which fpSup's notes say it does
not. Needs a software-LJ92 instruction count (not done yet) and a camera CPU measurement. [I/P]

## Lossy options

- **10-bit with a curve, then LJ92.** Best-quality combination: shadow detail kept by a curve,
  about 3:1. Needs the hardware that applies the 8-bit curve (`RAWOUT`, possibly the `RWAPKNEE`
  raw knee, disabled by default per `research/liveview/upstream-clip-audit.md`) to work in 10-bit
  mode. Unknown. [P]
- **Lossy JPEG DNG via `0x301B`.** About 4–6:1 if the engine accepts Bayer. fpSup's
  `HW_LOSSLESS_JPEG_CODEC.md` lists the probe: CFA input, ≥166 Mpix/s standalone, spare DSP time.
  Its video-path caller loops over four planes with a per-plane bit depth, which would fit split
  Bayer planes. `FUN_c04f00c8` is the PTP LiveView handler serving preview JPEGs, so it does not
  show raw support. Whether readers (Resolve) accept DNG `Compression=34892` on CFA data is also
  unverified; Adobe's own lossy DNG is demosaiced (LinearRaw). [P]
- **Bayer in H.264 / HEVC.** Tile the four Bayer planes of a curved 8-bit frame into one luma
  image, flat chroma, intra-only at high bitrate; a computer tool rebuilds DNGs. The video
  encoder already handles UHD-sized luma in real time and is idle during CinemaDNG recording, so
  it is the only fp hardware fast enough for full-UHD raw. Lossy, 8-bit, not directly editable;
  the deciding unknown is whether the encoder accepts our own input buffer. [I/P]
- **Higher-bitrate / all-intra H.264** for better MOV: bitrate and GOP are descriptor fields;
  GOP=1 is accepted by the driver (fpSup 10-24 audit). Not raw. [C fields, P limits]
- H.264 also has a true lossless mode, but it needs a profile the fp's encoder doesn't appear to
  support (stock streams are High profile). [I]

## Ruled out for in-camera recording

CineForm (measured), VC-2/Dirac, HTJ2K, JPEG XL, AV1/VVC and other CPU codecs. ProRes RAW and
BRAW are proprietary with no licensable or public encoder.
