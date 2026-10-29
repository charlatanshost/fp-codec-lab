# fp video codec driver map: modes, HEVC switch point, buffer layout

2026-10-29. **Offline static analysis only: no camera I/O, no emulation of the codec, no card
writes.** Builds on [the 2026-10-15 analysis](../../fpSup-main/research/imaging-hw/notes/FP_H264_10BIT_FEASIBILITY_2026-10-15.md)
and [the 2026-10-24 audit](../../fpSup-main/research/imaging-hw/h264-10bit-audit-20261024/README.md).
Marks: **[C]** read directly from ROM instructions · **[I]** inference · **[P]** needs the camera.

Image: `seg0_c0000000.bin`, SHA-256
`aaa5208a028d9c4aebb9cc8614add723d456e96b2a95914f433079954320e622` (same image as the earlier
audits), unpacked from `FP__V502.bin` with `unpack_fp.py`. The firmware is not in this repo.

## Why this map

Three wanted features go through the same driver: an HEVC recording mode, higher-bitrate /
all-intra H.264, and feeding the encoder a Bayer frame instead of ISP output (lossy raw in a
video codec). This note maps the driver layer those need: how the mode is chosen, what the
modes are, and where the encoder's buffers are programmed.

## 1. Call chain [C]

```
SDK layer   C0725690 / C0725940
              mode = C0709158(w, h, fps)        # -> C070D858, returns 0 or 5
                     (forced 0 when SDK param +0x68 == 1, C072567A)
              C0708640 -> C0709B80              # per-mode prep
              C0708668 -> C0709C38(mode, ...)   # codec OPEN: stores mode in C2F30040,
                                                #   C0709A80 init (8–11 set 300C03F8.bit0)
              C0708978 -> C0709E50(mode, desc)  # session SETUP from the 0x104-byte descriptor
```

`C0709158` has five callers (`C070E9A6`, `C071027C`, `C0725690`, `C0725724`, `C0725C60`), all on
the encode/SDK side. Decode-side opens enter `C0708668` from `C0708B12` / `C0708B24`.

## 2. The twelve modes [C table, I meaning]

`C0709C38` branches on the mode (TBB table at `C0709D28`):

| Mode | Command to `C070A9A8` | `300C02C0` bit 15 | Same branch as |
|---|---|---|---|
| 0 | `0x2001` | set | — |
| 1 | `0x4001` | set | — |
| 2 | `0xA001` | clear | — |
| 3 | `0xC001` | set | — |
| 4 | `0x6001` | set | — |
| 5 | `0x2001` | clear | — |
| 6 | `0x4001` | clear (+ `C01F8E8C`) | — |
| 7 | `0x2001` | set | 0 |
| **8** | `0x2001` | set | **0** |
| **9** | `0x4001` | set | **1** |
| **10** | `0x2001` | clear | **5** |
| **11** | `0x4001` | clear (+ `C01F8E8C`) | **6** |

Modes 8–11 reuse the branches of 0, 1, 5 and 6 exactly. The only difference at open time is the
`300C03F8.bit0` flag that `C0709A80` sets for 8–11, which later selects the HEVC level/geometry
path (`C070B7A0`, `C070C930`, `C070CAB0`) and `C070AE18` instead of `C070ADE0` in setup.

Reading [I]: **8, 9, 10, 11 are the HEVC counterparts of AVC modes 0, 1, 5, 6.** Stock MOV
recording uses 0 or 5, so the encode candidates are **8** (mirror of 0) and **10** (mirror of 5).
Command `0x2001` is shared by every encode-side mode and `0x4001` plausibly means decode (the
camera must decode MOV for playback); that naming is not proven. Modes 2 and 3 additionally
program a second stream from descriptor `+0x104..+0x158` (see §4), consistent with a
dual-stream (main + proxy) or transcode mode.

`C070D858` returns 5 only for specific size/rate pairs (examples: 3840×2160 and 2560×1440 at the
2997 rate code, 1440×720, 854×480 and 640×480 at other codes); everything else is 0.

## 3. Register map by function

`regmap-C0700000-C0730000.txt`: every function in the driver range and the `0x300Cxxx` offsets it
reads and writes (heuristic linear sweep; use it to find code, not as proof).

## 4. Encoder memory layout [C]

`C0709E50` programs these from the low-level descriptor. All addresses are DRAM in the
`0x40000000` window, stored as `(addr − 0x40000000) >> 3`, split into a 13-bit high and a 16-bit
low register.

| Region | Descriptor | Registers | Setter |
|---|---|---|---|
| Input picture ring: base, slot size, slot count, width | `+0x28`, `+0x2C` (>>12, 4 KiB pages), `+0x30` (−1), `+0x10` (u16) | `108`/`10C`, `244`/`24C`, `250`, `26C` = ceil(w/16)×4; `270` = `0x220`, `274` = 0 | `C070B330` |
| Second input plane / stream (mode 2) | `+0x12C`, `+0x130`, `+0x114` | `240`/`248`, `24C`, `2E4` | `C070B330` |
| Reference / work area | `+0x34`, `+0x38` (or `+0x34..+0x40`) | `120`/`124` base, `22C`/`230` offsets in 4 KiB pages, `260` = 1, `264` = column count by width | `C070B3E8` / `C070B4B0` |
| Output bitstream | `+0x44` address, `+0x48` size | `110`/`114`, `380`, `384` = size ÷ `C070D5D0()` | `C070AC88` |
| Second output stream (mode 2) | `+0x148`, `+0x14C` | `388`/`38C` region | `C070AC88` |

`C070B3E8` / `C070B4B0` choose the column count (`264`) from the mode global `C2F30040` and the
picture width (>1920 → 120, >1280 → 60, else 40, or ceil(w/32) in some modes).

Reading [I]: the encoder fetches its input from a **CPU-programmed ring of plain DRAM buffers**
(base + slot size + count), the same ring the ISP's MOV output fills ([`RAW_TO_CODEC.md`](../../fpSup-main/research/imaging-hw/h264-10bit-audit-20261015/RAW_TO_CODEC.md) §4). Nothing
here ties the input to the ISP in hardware; whether the encoder accepts a buffer the ISP did not
write is [P].

## 5. What this means for the planned features

| Feature | Driver-level change | Unknowns that decide it |
|---|---|---|
| HEVC recording | make `C0709158` return 8 (or 10) | Does the 192 KiB control image actually encode HEVC? [P] The muxer writes only `avc1`/`avcC` (`C071F3E8`/`C071FCD8`): needs `hvc1`/`hvcC`, and the SPS/PPS extraction must handle HEVC NAL headers. |
| Higher bitrate / all-intra H.264 | descriptor bitrate and GOP fields (`C070B7A0`; GOP=1 accepted in the 10-24 audit) | Real limits of the hardware and of SD write speed [P] |
| Bayer in H.264/HEVC | write a tiled 8-bit Bayer image (+ flat chroma) into the input ring slots, or point the ring base at an own buffer | Timing/ownership against the ISP writer, and running the codec during CinemaDNG recording [P] |

## 6. Next steps

Offline:
1. Emulate `C0709C38` + `C0709E50` with mode 8 vs 0 and diff every register write (extend the
   10-24 harness, which already runs `C0709A80`); confirm the HEVC path completes without faults.
2. Trace the bitstream header extraction and the `avc1`/`avcC` writer to scope the MOV changes.
3. Trace who writes the input ring slots per frame (ISP writer vs encoder fetch), for Bayer
   injection.

On the camera (first experiment, read-only output): RAM-patch the mode to 8 for one short MOV,
dump the output bitstream buffer (`desc+0x44`) over the USB shell, and check it with a standard
HEVC parser on the computer. Pass = a parseable VPS/SPS/PPS and a decodable frame.

## Tools

```sh
python3 unpack_fp.py FP__V502.bin out/                   # MAIN segments; checks DFI checksums
python3 regmap.py out/seg0_c0000000.bin C0700000 C0730000
python3 dis.py out/seg0_c0000000.bin C0709C38 200
python3 xref.py out/seg0_c0000000.bin C0709158,C070D858
```

Need `pip install capstone`.
