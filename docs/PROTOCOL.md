# Optical Modem Protocol

A screen-to-camera data link. Everything lives in `optical_modem/`:

```
optical_modem/
├── config.py           # modulation palettes, ECC profiles, frame geometry
├── protocol/           # packet layer (no pixels involved)
│   ├── packet.py       # FrameHeader, CRC16, Packetizer (chunking / resume)
│   ├── ecc.py          # Reed-Solomon (with erasures) + LDPC (sum-product)
│   └── frames.py       # bytes <-> cells, interleaving, block sizing
├── phy/                # physical layer (pixels)
│   ├── render.py       # symbol grid -> image
│   ├── channel.py      # camera-channel simulator (perspective, gamma, blur…)
│   ├── locate.py       # contour detection + cell sampling
│   ├── align.py        # sub-cell alignment refinement (sharpness objective)
│   ├── demod.py        # illumination normalisation, calibration, soft demod
│   └── classical.py    # full classical receiver
├── detect/yolo.py      # YOLO26 frame detector (same output contract)
├── receiver.py         # HybridReceiver: yolo / classical / auto
├── bench.py            # benchmark harness
├── data/               # synthetic dataset + YOLO26 training
└── gui/                # FastAPI + single-page UI
```

## 1. Frame geometry

A frame is a square grid of coloured cells (`grid x grid`, default 32x32), drawn
with a white quiet zone and a dark border ring so that it is trivially
segmentable by classical CV as well as by a neural network.

```
┌──────────────────── white quiet zone (2 cells) ────────────────────┐
│  ▓▓▓▓ dark border ring (1 cell) ▓▓▓▓                               │
│      ┌──────────── coded grid (G x G cells) ────────────┐          │
│      │ calibration ring (1 cell)                         │ y        │
│      │ ┌──── header block: 11B + RS(16) ────┐            │          │
│      │ │ payload block: data + CRC16 + FEC │             │          │
│      │ └───────────────────────────────────┘             │          │
│      └───────────────────────────────────────────────────┘          │
└────────────────────────────────────────────────────────────────────┘
```

| region       | cells                        | purpose                                    |
|--------------|------------------------------|--------------------------------------------|
| quiet zone   | outside the grid             | detection / contrast                       |
| border ring  | 1 cell, dark                 | strong closed contour for detection        |
| calibration ring | outermost grid cells     | known symbol sequence → colour calibration |
| header block | `ceil((11+16)*8 / (inner_cols*bits))` rows | fixed-rate, self-describing metadata |
| payload block| the rest                     | user data, variable ECC                    |
| finder block | 0 or 4 (or 1) `k x k` blocks, `k=4..5` | **optional** anchor for detection; carved out of header+payload, never painted over them |

The header row count is computed (`effective_layout`) so that even a 1 bit/cell
modulation has room for the header.

### 1.1 Optional finder pattern

`layout.finder` selects a shape from the catalogue in
`optical_modem/protocol/finder.py` — `none` (default), `ring`, `dice`,
`bullseye`, `checker`, `diamond`, `cross`, `x`, `bracket`, `triangle`, `solid` —
and `layout.finder_span` picks which reserved slots get one: `corners` (default),
`three`, `center`, `edges`.

Placement is against the inner corners, inside the calibration ring, so a finder
never touches the ring. The symbols it occupies are **removed from the header and
payload symbol lists before either is filled**; `FrameCodec` asserts the four
regions are pairwise disjoint and cover the grid exactly once, so a finder can
never overwrite data (it costs capacity, and possibly one extra header row).

Finder tiles are always solid (pattern index 0) and use the palette's
brightest/darkest entries (`bright_dark_indices`) — a blur-safe, hue-agnostic
anchor that works for every modulation profile, including the `pair` modes where
the second cell of the symbol carries the hue+180° complement.

## 2. Frame header (11 bytes, little endian)

| field      | bytes | meaning                                                  |
|------------|-------|----------------------------------------------------------|
| magic      | 2     | `b"OM"`                                                  |
| version    | 1     | high nibble major, low nibble minor (`0x10`)             |
| mod_id     | 1     | modulation id (0 gray2, 1 gray4, 2 rgb8, 3 rgb8x2)       |
| ecc_id     | 1     | ECC profile id                                           |
| frame_type | 1     | 0 DATA, 1 START, 2 END, 3 ACK, 4 NACK, 5 META            |
| seq        | 2     | sequence number (16 bit)                                 |
| length     | 2     | payload length in bytes **including the 2-byte CRC16**   |
| mask       | 1     | whitening mask: hi 2 bits family, lo 6 bits seed (§2.1)  |

The header is always protected by **RS(16)** at a *known* rate, so it can be
read before the sender's ECC choice is known. Because `mod_id` is inside the
header, a receiver that guessed the wrong modulation can re-decode the payload
with the modulation the header names (implemented in
`ClassicalReceiver.decode`).

### 2.1 Payload whitening mask

A frame's payload block is a fixed size, so a short payload leaves the tail
zero-padded.  Unmasked, that tail is a *large contiguous slab of identical dark
tiles*: it wastes the display, makes the code look half empty, and wrecks any
receiver statistic that is estimated locally (the background/gain estimate in
`phy/demod.py` is exactly such a statistic — a flat slab biases it onto the
foreground).

Every payload symbol value is therefore XORed with a deterministic mask before
it is drawn, and the receiver XORs it back (XOR is self-inverse).  The mask
byte in the header selects the family and seeds it, so no side channel is
needed and any receiver can reproduce it.

| family | name        | value at `(i, j)`                 | notes |
|--------|-------------|-----------------------------------|-------|
| 0      | none        | `0`                               | diagnostics / A-B tests only |
| 1      | prng        | `SplitMix64(seed) mod n_values`   | default; flat statistics |
| 2      | alternating | `(i + j) & 1 ? n_values/2 : 0`    | no generator needed |
| 3      | ramp        | `(7i + 13j + seed) mod n_values`  | no generator needed |

Measured on an 80-cell `rgb32` frame carrying a 2-byte payload:

| mask | modal symbol share | longest identical run |
|------|--------------------|-----------------------|
| none | 0.90               | 79 cells              |
| prng | 0.03               | 4 cells               |
| alt  | 0.45               | 12 cells              |
| ramp | 0.03               | 3 cells               |

The **header block** uses a *public constant* mask (`HEADER_MASK_BYTE`): it has
to be readable before the mask byte inside it is known, and its own zero tail
would otherwise be a 3-4 row dark bar.  The calibration ring is never masked —
it is the reference the receiver calibrates against.

The generator is a hand-written SplitMix64 (`protocol/mask.py`) rather than
`numpy.random`, so the mask is reproducible bit-for-bit by any implementation
and by any future numpy version.  `ModemConfig.mask_family` picks the default
family and `ModemConfig.mask_byte(seq)` derives a fresh seed per frame, so
consecutive frames of a transfer do not share a tile pattern.

## 3. Modulation

`config.Modulation` is `(name, mod_id, bits, palette, labels)`. `labels` assigns
bit patterns to palette points such that *adjacent colours differ by one bit*
(Hamming-minimal / Gray-ish), so a channel confusion costs one bit instead of
three — the FEC layer is then sized for single-bit errors.

| name     | bits/cell | palette                                   |
|----------|-----------|-------------------------------------------|
| `gray2`  | 1         | 2 intensity levels                        |
| `gray4`  | 2         | 4 intensity levels (Gray-labelled)        |
| `rgb8`   | 3         | 8 colours, one bit per RGB channel        |
| `rgb8x2` | 4         | composite: 8 colours x 2 brightness       |

Cells are mapped MSB-first; the palette index of each symbol is what actually
gets rendered.

## 4. Error correction

`protocol/ecc.py`

* **Reed-Solomon** over GF(256) (`reedsolo`), profiles `rs8/rs16/rs32/rs64`
  (parity bytes per 255-byte codeword). Supports **erasures**.
* **LDPC** — quasi-cyclic `H = [A | I]` (A built from z×z circulant blocks),
  sum-product decoding, profiles `ldpc12` (rate 1/2) and `ldpc23` (rate 2/3).
* `none` for benchmarking.

Soft information path: the demodulator produces per-bit LLRs; the *least
reliable* bytes (below 60% of the median confidence) are handed to RS as
erasures, capped at `nsym-1` per codeword.

Interleaving: a fixed pseudo-random permutation (`seed=1234`) spreads
neighbouring cells across the codeword so a burst of damage (a finger, a
specular highlight) becomes sparse errors. Header cells use a different seed.

## 5. Packets and resume

`protocol/packet.py:Packetizer`

* splits a byte stream into chunks of `max_user_payload - 4` bytes;
* wraps every chunk with the frame codec's CRC16;
* START/END frames append `(total_frames, whole_message_crc16)`;
* the receiver buffers chunks **by sequence number**, so duplicates are
  harmless and a gap is simply retransmitted — file transfer resumes without
  restarting.

## 6. Receiver pipeline

```
image ─► localise ─► align ─► sample cells ─► calibrate on ring ─► demodulate
       (YOLO26 or contours)   (sharpness)     (affine, robust)     (LLR)
       ─► de-interleave ─► FEC (soft erasures) ─► CRC16 ─► payload
```

1. **Localise** – YOLO26-pose predicts the four coded-grid corners; the
   classical fallback scores contour hypotheses (a contour may be the dark
   border ring *or* the bright plate).
2. **Align** – a coarse-to-fine search over (zoom, dx, dy) in *cell space*
   minimising mean within-cell intensity spread. Alignment is photometric
   independent: misalignment shows up as extra energy inside every cell.
3. **Photometry** – the frame is calibrated against the known calibration ring
   with a robust affine fit (`PaletteCalibrator`), optionally on a
   locally-normalised copy of the frame (division by a blurred version), which
   cancels shadows and highlights. The ring decides which variant is used.
4. **Demodulate** – Gaussian likelihood over palette points → symbol + per-bit
   LLR + confidence.
5. **FEC** – de-interleave, hand low-confidence bytes to RS as erasures, decode,
   verify CRC16, then the quality metric (`mean|LLR|`) drives adaptive ECC
   selection for the next transmission (`config.choose_ecc`).

## 7. Configuration surface

`ModemConfig` (also exposed in the GUI and via `python -m optical_modem info`):

| parameter | values |
|---|---|
| modulation | gray2, gray4, rgb8, rgb8x2 |
| ecc | none, rs8, rs16, rs32, rs64, ldpc12, ldpc23 |
| grid | 24 … 48 cells (default 32) |
| cell_px | pixels per cell in the rendered frame |
| quiet_zone_cells | default 2 |
| interleave | on/off |
| auto_detect_modulation | try every modulation when the header does not validate |

Capacity scales as `grid² x bits`; see `python -m optical_modem info`.
