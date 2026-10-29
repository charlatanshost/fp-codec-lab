# cineform — CineForm RAW for fp footage: post-converter and in-camera cost

2026-10-29. Offline only: no camera I/O, no card writes. Confidence marks follow the fpSup
convention: **[C]** measured or confirmed here · **[I]** inferred · **[P]** needs the camera.
File paths in backticks such as `projects/...` and `research/...` are relative to `../../fpSup-main/`.

## Verdict

- **As a computer-side converter, CineForm RAW works on fp CinemaDNG.** The mapping below
  round-trips correctly and visually lossless settings give about 4–6:1 on detailed or noisy
  frames. It is a lossy codec, not a replacement for lossless `Compression=7`. [C]
- **As an in-camera recording codec it is not viable.** The fp has no wavelet hardware, so the
  encoder would run on the single Cortex-A7-class core, which is already fully loaded while
  recording. A NEON build needs **30–37 instructions per pixel** at normal quality, and up to
  58 on noisy frames at the highest quality. Even with the whole CPU that is single-digit fps at
  FHD and 1–4 fps at UHD. [C] instruction counts / [I] fps
- Possible niche: timelapse or very low frame rates, where the CPU has whole seconds per frame. [I]

## What is here

| File | What it does |
|---|---|
| `tools/fp2cfhd.cpp` | fp cDNG frame → CineForm RAW sample. Decodes it again and reports ratio, error (in 12-bit DN, split into shadows / clipped / unclipped) and encode time. `-d sample.cfhd` scores a sample made elsewhere. Built with `-DENCODE_ONLY` it becomes the ARM test program. |
| `tools/emu_count.py` | Runs a static ARM Linux binary under unicorn (just enough syscalls for musl) and counts the exact number of ARM/Thumb instructions between two marker syscalls placed around the encode. |
| `tools/build.sh` | Builds both binaries from an **unmodified** CineForm SDK checkout. Works around the SDK's libuuid dependency, routes SSE2 intrinsics to NEON with [sse2neon](https://github.com/DLTcollab/sse2neon) v1.7.0, and compiles a fixed copy of one file clang rejects. |

```sh
pip install ziglang unicorn                  # ARM cross-compiler + emulator, no root needed
tools/build.sh /path/to/cineform-sdk all     # -> build/fp2cfhd, build/fp2cfhd_arm
build/fp2cfhd -q 4 -o out.cfhd frame.DNG     # host converter + quality report
python3 tools/emu_count.py build/fp2cfhd_arm -q 4 frame.DNG   # emulated instruction count
```

The SDK's own build needs cmake and libuuid; `build.sh` needs neither.

Why sse2neon and not the SDK's plain-C path: the SDK has a non-SIMD switch (`_XMMOPT=0`), but
it has bitrotted. Building with it fails with 46 compile errors in `Codec/convert.c`,
`Codec/spatial.c` and `Codec/temporal.c`, so there is no portable C fallback to start an ARM port
from. Translating the SSE2 intrinsics was the shortest working route. [C]

## Encoder mapping [C]

How fp CinemaDNG data has to be fed to the CineForm encoder:

1. **Unpack.** fp movie frames are uncompressed strips, 12-bit packed MSB-first (TIFF rule),
   RGGB, `BlackLevel` 256, `WhiteLevel` 4095.
2. **Normalise.** `(raw − black) / (white − black)` → full-scale 16-bit linear, pixel format
   `CFHD_PIXEL_FORMAT_BYR4`. Pass the full sensor width and height; the encoder halves them itself.
3. **Bayer phase.** Must be set via metadata: `TAG_BAYER_FORMAT` = `CFHD_BAYER_FORMAT_RED_GRN` (0).
4. **Inside the encoder** (`Codec/frame.c` `ConvertBYR4ToFrame16s`): a log90 curve to 12 bits,
   then each 2×2 RGGB block becomes four half-size planes — G average, R−G, B−G, G1−G2 — and
   each plane is wavelet-coded.

Checked: the decoded mosaic lines up with the source pixel for pixel; errors sit on edges, not
as a shift.

**Known mapping issue:** clipped pixels come back about 10 DN low (≈4085, not 4095) at every
quality. A real converter should lower the DNG `WhiteLevel` it writes, or scale so clip lands
below the curve's top code. Not yet fixed.

## Host results (x86, one core, SSE2) [C]

Real scene, `rawview/captures/2026-09-24-dngcheck/A001_031_f000001.DNG` (2016×1344, defocused,
large clipped highlight). Error in 12-bit DN:

| Quality | Ratio vs 12-bit packed | RMSE | Shadows RMSE | Unclipped RMSE | Encode |
|---|---|---|---|---|---|
| 1 LOW | 34:1 | 20.4 | 1.5 | 17.8 | 5.4 ms |
| 4 FILMSCAN1 (default) | 27:1 | 10.0 | 1.0 | 8.3 | 6.0 ms |
| 6 FILMSCAN3 | 14.5:1 | 6.6 | 0.6 | 5.1 | 8.5 ms |

Busier, noisier frame, `open-gate/captures/og3k120-defect-A001_182` (3856×2170):
q1 28:1 · q4 10.4:1 · q6 **4.3:1** at RMSE 0.7 DN.

- These ratios are optimistic. The real-scene frame is mostly flat, and the other frames in the
  repo are near-black test cards (almost every pixel within 64 DN of black). Representative
  ratios need detailed, high-ISO footage.
- The remaining ~5 DN highlight error at q6 comes from the log curve's quantisation near white
  (about 4 DN per output code at the top). It is probably below shot noise there but makes the
  codec visibly not lossless in numbers.

## ARM / Cortex-A7 cost (emulated) [C counts, I time]

The codec built for `-mcpu=cortex_a7` with SSE2 → NEON. **At q1 and q4 the ARM bitstream is
byte-identical to x86** (only the GUID, a timestamp and the frame-number tag differ). At q6 it is
not: the sample is valid and decodes fine, but is ~20% smaller with RMSE 6.88 vs 6.59 DN, most
likely one SSE2 rounding operation that sse2neon translates differently on that path.

Instructions executed by `CFHD_EncodeSample` + `CFHD_GetSampleData`:

| Frame | Quality | Instructions | Per pixel |
|---|---|---|---|
| 2016×1344 real scene | 1 | 82.2 M | 30.3 |
| 2016×1344 real scene | 4 | 83.9 M | 31.0 |
| 2016×1344 real scene | 6 | 88.9 M | 32.8 (not bit-exact) |
| 1936×1090 FHD test card | 4 | 66.0 M | 31.3 |
| 3024×2010 OG3K test card | 4 | 196.5 M | 32.3 |
| 3856×2170 dark / noisy | 1 | 254.7 M | 30.4 |
| 3856×2170 dark / noisy | 4 | 312.8 M | 37.4 |
| 3856×2170 dark / noisy | 6 | 485.2 M | 58.0 (not bit-exact) |

Turning that into frame rates needs two numbers this project does not have: the fp's CPU clock
(not yet read; see `research/firmware/notes/CLOCK_IDLE_IRQOFF.md`) and the A7's real
instructions-per-clock on this code. The table assumes the **entire** CPU, 0.5–1.0
instructions per clock (in-order dual issue; 128-bit NEON ops take two cycles on A7):

| Format | 31 insn/px @ 1 GHz | 37 insn/px @ 1 GHz | 58 insn/px @ 1 GHz |
|---|---|---|---|
| FHD 1936×1090 | 7.6–15 fps | 6.4–13 fps | 4.1–8.2 fps |
| OG3K 3024×2010 | 2.7–5.3 fps | 2.2–4.4 fps | 1.4–2.8 fps |
| UHD 3840×2160 | 1.9–3.9 fps | 1.6–3.3 fps | 1.0–2.1 fps |

Scale linearly for other clocks. What the table leaves out, and which way it pushes:

- **Optimistic:** recording already uses the whole CPU (`projects/sys-perf/README.md`), so the
  encoder would get a fraction of it. Cache misses and DRAM bandwidth are not modelled; the
  encoder streams the frame several times through the wavelet levels.
- **Pessimistic:** sse2neon is a translation layer, not hand-written NEON. A native port could
  cut the count, perhaps substantially, but not by the 3–10× needed for FHD 24p alongside
  recording. [I]
- Emulation counts instructions only. Real timing needs the camera; fpSup has a 1 MHz counter
  (`C002B6E0`) and `callfn`, but running the SDK on the camera means a bare-metal port (no libc,
  threads or files), which is a large job for a result this table already bounds.

## Lossy options on the fp — notes against existing research

Raised in discussion (2026-10-29): instead of CineForm, use the fp's existing hardware for lossy
output. What the repo already knows:

- **Lossy JPEG DNG via the 0x301B engine.** `XC_SigproJpegComp` (MMIO `0x301B0000`) is a
  baseline DCT JPEG engine, Q85/90/95, rate-controlled. `RAW_COMPRESSION_RESEARCH.md` lists it as
  YCbCr-only and wired into the ISP; `HW_LOSSLESS_JPEG_CODEC.md` keeps CFA input as an open
  question (its video-path caller loops over **four planes** with a per-plane bit-depth field,
  which would fit split Bayer planes) and lists the probe needed: CFA acceptance, ≥166 Mpix/s
  standalone, spare DSP cycles while recording. Submitting work needs a live frame graph and DSP
  session, so it is much harder to drive than the lossless `0x300D` engine. [P]
  - `FUN_c04f00c8` is the PTP LiveView handler that serves preview JPEGs
    (`projects/usb-shell-sup/notes/RAW_PUSH.md`). It shows the JPEG hardware is in use, not that
    it accepts raw data.
  - Check before building on it: whether DNG `Compression=34892` is accepted on CFA data by the
    readers that matter (Resolve in particular), and the 8-bit limit, which needs a strong
    curve to protect shadows. [P]
- **Higher-bitrate / all-intra H.264.** The MOV path drives a hardware codec at `0x300Cxxxx`;
  `C070B7A0` sets bitrate, GOP and level, and `300C0208` holds GOP and processing-budget fields
  (`FP_H264_10BIT_FEASIBILITY_2026-10-15.md`). Existing MOVs are H.264 High 4:2:0 8-bit; no
  10-bit path has been verified. Raising the bitrate or shortening the GOP is plausible but
  untested. [I]

## Status: parked

The goal of this project is new recording options **in the camera**, and the instruction counts
above rule CineForm out for that. Work has moved to the hardware paths (LJ92, the `0x300C` video
codec); see [../compression-options](../compression-options/README.md) and
[../codec-driver-map](../codec-driver-map/README.md).

The tools stay here because they work and are reproducible. If anyone wants to take the
computer-side converter further, the open items are:

1. **Decoder support.** Mux samples into a MOV and check DaVinci Resolve, Premiere and FFmpeg
   decode CineForm *RAW* (Bayer) correctly, with the right colour.
2. **Real footage.** Run `fp2cfhd` on detailed, high-ISO clips to get honest ratios.
3. **Fix the white-point mapping** (clip returns ~10 DN low).
4. **The q6 ARM/x86 mismatch** (only matters for an ARM build).

Reading the fp's CPU clock (`[0xC3033260]`, read-only) is still worth doing, but for the
software-LJ92 estimate in [../compression-options](../compression-options/README.md), not for CineForm.
