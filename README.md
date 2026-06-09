# camtriage

Wildlife camera trap triage tool. Automatically sorts and annotates trail camera images using Google's [SpeciesNet](https://github.com/google/cameratrapai) classifier — no cloud API, no account required.

Optionally annotates detections with behavioral context using Apple Foundation Models (Mac only, on-device, private).

Built as a research prototype for [HomesteadAI](https://robroy.online).

---

## What it does

1. **Walks** a source folder (SD card, external drive, or any image directory)
2. **Classifies** every image with SpeciesNet — a wildlife-specific model trained on millions of camera trap images, accurate to species level
3. **Sorts** interesting images into subfolders by species (`white-tailed_deer/`, `red_fox/`, `bobcat/`, etc.)
4. **Alerts** on configurable species — always (mountain lion, bear) or context-sensitive (human at night)
5. **Matches** companion video clips to classified stills by timestamp — no video processing needed
6. **Annotates** each detection with behavioral context using Apple Foundation Models (Mac with Apple Intelligence)
7. **Flags** unusual timing using a grounded species behavior table — no LLM guessing
8. **Writes** a `triage_log.json` with full details for every image and video

### Example output

```
  ✓  DCIM/IMG0042.JPG     white-tailed deer   94%  COPIED
       ↳ The deer is likely feeding along a field edge during its typical crepuscular activity period.

  🚨  DCIM/IMG0187.JPG    human               91%  🚨 ALERT
       ↳ ⚠️  Unusual — human detected at night.

  ✓  DCIM/IMG0107.JPG     red fox             89%  COPIED
  ⚠️ ↳ UNUSUAL: Red foxes are crepuscular — daytime activity may indicate
       disturbance or mange; the fox is likely moving between cover.

  🎥  DCIM/DSCF0042.AVI   white-tailed deer        → white-tailed_deer/DSCF0042.AVI

  ?  DCIM/IMG0203.JPG     pileated woodpecker  32%  review
  ·  DCIM/IMG0311.JPG     blank                 —   skip

════════════════════════════════════════════════════════════
  🚨 ALERTS    : 1  ← review immediately
  Total        : 847
  ✓ Copied     : 634
  🎥 Videos    : 12 copied  (2 unmatched)
  ? Review     : 48   (below confidence threshold)
  · Skipped    : 165  (blank / boring)
  Elapsed      : 142.3s
```

### Species summary

Before copying, camtriage shows what SpeciesNet found:

```
  SPECIES                        TOTAL    COPY   ALERT
  ─────────────────────────────────────────────────────
  white-tailed deer                312     312          ████████████████
  blank                            201       0
  eastern gray squirrel             67      67          ████
  red fox                           89      89          █████
  domestic cat                      43      43   🚨      ███
  human                              5       0
```

---

## Platform support

| Feature | Mac | Linux | Windows |
|---------|-----|-------|---------|
| SpeciesNet classification | ✓ | ✓ | ✓ |
| Image sorting & alerts | ✓ | ✓ | ✓ |
| Video timestamp matching | ✓ | ✓ | ✓ |
| EXIF time extraction | ✓ | ✓ | ✓ |
| Behavioral annotation (text) | ✓ Mac + Apple Intelligence | ✗ | ✗ |
| Vision annotation (image input) | ✓ macOS 27+ (fall 2026) | ✗ | ✗ |

The core triage pipeline — classification, sorting, alerts, video — runs on any platform. Apple Foundation Models annotation is a Mac enhancement, not a requirement.

---

## Requirements

- **Python 3.9–3.13** (SpeciesNet constraint)
- **speciesnet**, **pyyaml** — required
- **Pillow** — recommended (enables EXIF time-of-day extraction)
- **apple-fm-sdk** — optional, Mac only (behavioral annotation)

---

## Installation

```bash
# 1. Clone the repo
git clone https://github.com/robroy/camtriage.git
cd camtriage

# 2. Create a virtualenv with Python 3.11
#    macOS:
python3.11 -m venv camenv && source camenv/bin/activate
#    Linux/Windows:
python3.11 -m venv camenv && camenv/bin/activate  # or camenv\Scripts\activate on Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and edit the config
cp config.example.yaml config.yaml
# Edit config.yaml — set your country/state, adjust species for your region
```

---

## Usage

```bash
source camenv/bin/activate   # macOS/Linux

python camtriage.py <source> <dest>
python camtriage.py <source> <dest> --config myconfig.yaml

# Examples
python camtriage.py /Volumes/SD_CARD ~/Desktop/Wildlife
python camtriage.py /media/sdcard ~/triage --config oregon.yaml
```

### Arguments

| Argument | Description |
|----------|-------------|
| `source` | SD card or image folder |
| `dest`   | Output folder — created if it doesn't exist |
| `--config` | Path to config YAML (default: `config.yaml` next to the script, then built-in defaults) |

---

## Configuration

Copy `config.example.yaml` to `config.yaml` and edit for your location and preferences.

```yaml
location:
  country: USA          # ISO 3166-1 alpha-3
  state: ME             # ISO 3166-2 subdivision

min_confidence: 0.50    # raise for stricter triage

# Always alert on these species
alert_labels:
  - mountain lion
  - black bear
  - dog

# Alert only when detected at specific time of day
alert_if:
  human:        [night, dawn]   # trespasser
  domestic cat: [night]         # predator behavior

skip_labels:
  - blank
  - empty
  - vehicle
  - human                       # skipped by default; alert_if overrides for night

species_behavior:
  northern raccoon: { active: nocturnal, flag_if: daytime }
  red fox:          { active: crepuscular, flag_if: daytime }
  # add/edit for your region
```

### Decision priority

Every image is assigned exactly one outcome, in this order:

| Priority | Outcome | Condition |
|----------|---------|-----------|
| 1 | 🚨 **ALERT** | Matches `alert_labels` or `alert_if` + confidence ≥ threshold. **Overrides `skip_labels`** — a night human alerts even though `human` is in `skip_labels`. |
| 2 | ✓ **COPIED** | Not an alert, not in `skip_labels`, confidence ≥ threshold |
| 3 | ? **REVIEW** | Not skippable, but confidence < threshold |
| 4 | · **SKIP** | In `skip_labels` or confidence = 0 |

---

## How it works

### SpeciesNet

[SpeciesNet](https://github.com/google/cameratrapai) is Google's open-source wildlife classifier, trained on millions of camera trap images worldwide. It classifies to species level and returns confidence scores. Geographic priors (country + state/province) improve accuracy by down-weighting species not present in your region.

Accuracy on the author's Maine game cam test set: **91.1%** vs 38.2% for a general-purpose vision LLM.

### Apple Foundation Models (Mac only)

Behavioral annotations use Apple's on-device Foundation Models via the [`apple-fm-sdk`](https://pypi.org/project/apple-fm-sdk/) Python SDK. Inference runs locally — no data leaves your machine, no API key required.

The tool provides a grounded species behavior table to the model rather than asking it to recall behavioral facts (which LLMs do unreliably). The model receives species name, time of day, date, location, and whether the timing is unusual — it generates a one-sentence behavioral description. Unusual detections are prefixed with ⚠️.

Vision annotation (passing the actual image to Apple FM) requires macOS 27 Golden Gate, shipping fall 2026.

### Video handling

Most game cams shoot both stills and a short video clip for the same trigger event. camtriage uses **timestamp matching** — after classifying all stills, it matches video files to copied stills by filesystem timestamp. If a video falls within the configured window (default 60s) of an interesting or alert still, the video is copied to the same species folder.

```
  🎥  DCIM/DSCF0042.AVI    white-tailed deer    → white-tailed_deer/DSCF0042.AVI
  ·   2 video(s) had no matching still — skipped
```

**Other video strategies considered:**

| Option | Approach | Pros | Cons |
|--------|----------|------|------|
| 1. Keyframe extract | ffmpeg extracts frames → SpeciesNet | Same classifier | Requires ffmpeg, adds time |
| 2. MegaDetector | `run_md_and_speciesnet` handles video natively | Purpose-built | Heavier install, prefers NVIDIA |
| 3. First frame only | Extract thumbnail, classify | Very fast | High false negative rate |
| 4. Copy all video | No classification | Zero false negatives | No triage value |
| **5. Timestamp match** ✓ | Match to classified stills | No video processing | Misses video-only events (rare) |

### Output structure

```
dest/
├── _alerts/
│   └── human/
│       └── IMG0187.JPG
├── white-tailed_deer/
│   ├── IMG0042.JPG
│   ├── IMG0043.JPG
│   └── DSCF0042.AVI        ← companion video
├── red_fox/
│   └── IMG0107.JPG
└── triage_log.json
```

---

## Roadmap

- [ ] Interactive species summary — confirm/adjust per-species decisions before copying
- [ ] Video Option 1: keyframe extraction via ffmpeg for video-only cameras
- [ ] macOS 27 Golden Gate: vision annotation via `ImageAttachment`
- [ ] Native Mac app (Swift) — SmartFiler integration

---

## Credits

- [SpeciesNet](https://github.com/google/cameratrapai) — Google, Apache 2.0
- [Apple Foundation Models SDK](https://pypi.org/project/apple-fm-sdk/) — Apple Inc.

---

## License

MIT — see [LICENSE](LICENSE)

Built by [Rob Roy](https://robroy.online) · [HomesteadAI](https://robroy.online)
