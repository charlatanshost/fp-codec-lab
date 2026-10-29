# fp-codec-lab

Research into new recording codecs and raw compression for the **SIGMA fp (firmware Ver.5.02)**,
built on [fpSup](../fpSup-main/README.md). Kept separate from the fpSup tree until the work is
proven; nothing here has been run on a camera yet.

Started 2026-10-29. Marks used throughout: **[C]** confirmed (ROM instructions, measurement, or
fpSup's verified notes) · **[I]** inference · **[P]** needs the camera.

## Goals

Add as many useful recording options as the fp's hardware allows:

1. **Lossless raw compression** — preferred.
2. **Lossy raw compression** — for people who want smaller files.
3. **HEVC recording**, and better H.264 (higher bitrate, all-intra).

## Status

| Feature | Status | Deciding unknown |
|---|---|---|
| Lossless LJ92 (fpSup's fpLossless) | fpSup prototype works at FHD on camera [C] | Stability, frame identity, pipelining for OG3K / wide UHD |
| 10-bit + LJ92 | ~2.8–3:1 estimated vs ~2:1 for 12-bit [I] | Engine 10-bit mode with fp's 10-bit output, on camera [P] |
| 10-bit with curve + LJ92 | Idea | Can the 8-bit curve hardware run in 10-bit mode? [P] |
| HEVC recording | Driver switch found: modes 8–11 = HEVC versions of 0/1/5/6 [C/I] | Does the codec control program encode HEVC? MOV writer needs `hvc1` [P] |
| Higher-bitrate / all-intra H.264 | Fields known; GOP=1 accepted by driver [C] | Hardware and SD limits [P] |
| Bayer in H.264/HEVC (lossy raw) | Encoder input ring mapped [C] | Will the encoder take our own buffer, during cDNG recording? [P] |
| Lossy JPEG DNG via `0x301B` | Notes only | Does the engine accept Bayer? [P] |
| CineForm RAW in camera | **Ruled out**: 30–37 ARM instructions/pixel [C] | — (works as a computer-side converter) |

## Layout

| Folder | What it holds |
|---|---|
| [compression-options/](compression-options/README.md) | The full lossless/lossy landscape on the fp, LJ92 engine time budget, 10-bit ratio estimates; `engine_budget.py`, `ljpeg_ratio_estimate.py` |
| [codec-driver-map/](codec-driver-map/README.md) | Reverse-engineering of the `0x300C` video codec driver: the 12 modes, the HEVC switch point, encoder buffer layout; firmware unpacker, register map, disassembler, cross-reference tools |
| [cineform/](cineform/README.md) | CineForm RAW evaluation: fp cDNG → CineForm converter, x86 results, Cortex-A7 NEON build and emulated instruction counts |

## Key findings so far

1. **Only hardware paths work in-camera.** The fp's single A7-class core is already full while
   recording; CineForm measured at 30–37 instructions/pixel (single-digit fps at FHD even with
   the whole CPU). [C]
2. **The lossless engine (LJ92) is the lossless route**, and its speed limits it: FHD fits easily,
   OG3K and 3840×1600 fit at 24/25p with parallel encode+write, full UHD 16:9 does not fit. [C model]
3. **Lower bit depth stacks with LJ92** (10-bit: about 1.8 bits/pixel saved after compression);
   8-bit does not, because the engine has no 8-bit mode. [C/I]
4. **The video codec has HEVC modes.** Modes 8, 9, 10, 11 mirror AVC modes 0, 1, 5, 6 exactly,
   apart from an HEVC flag that selects HEVC level tables. Stock recording picks mode 0 or 5 in
   `C0709158`; an HEVC test means making it return 8. [C structure, I meaning]
5. **The video encoder reads its input from a CPU-programmed ring of plain DRAM buffers**, which
   makes Bayer-in-video plausible, and the encoder is idle during CinemaDNG recording. [C/I]

## Setup

```sh
python3 -m venv venv && venv/bin/pip install capstone unicorn ziglang
# Unpack firmware for the RE tools (keep the output out of any repo):
venv/bin/python codec-driver-map/unpack_fp.py ../FP__V502.bin /path/outside/repo/fw
#   seg0 SHA-256 must be aaa5208a028d9c4aebb9cc8614add723d456e96b2a95914f433079954320e622
```

CineForm builds need the SDK checkout at `../cineform-sdk-master`: see `cineform/tools/build.sh`.

## Working rules

- **No firmware or decompiled output in this folder.** fpSup's pre-commit check forbids it, and
  the same applies here; keep unpacked images outside (`.gitignore` covers the usual names).
- **Offline vs camera are reported separately.** Emulation counts instructions and checks logic;
  it says nothing about timing or what the codec hardware actually does.
- **Read fpSup, don't edit it.** Findings that hold up get offered to fpSup later.
- Camera experiments are designed read-only first (dump output, change nothing persistent),
  following fpSup's `research/SETTINGS_AREA_FIRST_RULE.md`.

## Next steps

Offline (now):
1. Emulate codec open + setup (`C0709C38`, `C0709E50`) in mode 8 vs 0 and diff every register
   write; check the HEVC path completes. Extends fpSup's 2026-10-24 harness.
2. Trace the MOV writer's H.264-specific parts (`avc1`/`avcC`, SPS/PPS extraction) to scope HEVC muxing.
3. Trace who fills the encoder's input ring each frame, for Bayer injection.
4. Count instructions for software LJ92 on the A7 (for the hardware + CPU tile split idea).
5. Computer-side Bayer-in-H.264/HEVC prototype: ratio and quality before any camera work.

On the camera (when available):
1. HEVC: RAM-patch the mode to 8 for one short MOV, dump the output bitstream buffer, parse it on
   the computer.
2. 10-bit + LJ92 on top of fpLossless.
3. Read the CPU clock (`[0xC3033260]`, read-only) to pin the CineForm/LJ92 software estimates.

## Log

- **2026-10-29** — CineForm evaluation (converter, A7 cost), compression landscape and LJ92
  budget, 10-bit ratio estimates, firmware unpack, codec driver map (modes, HEVC switch, buffers).
