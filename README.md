# camtriage

Wildlife camera trap triage tool for Mac. Automatically sorts and annotates trail camera images using Google's [SpeciesNet](https://github.com/google/cameratrapai) classifier and Apple Foundation Models — entirely on-device, no cloud API, no account required.

Built as a research prototype for [HomesteadAI](https://robroy.online) — intelligent power tools for Mac.

---

## What it does

1. **Walks** a source folder (SD card, external drive, or any image directory)
2. **Classifies** every image with SpeciesNet — a wildlife-specific model trained on millions of camera trap images, accurate to species level
3. **Sorts** interesting images into subfolders by species (`white-tailed_deer/`, `red_fox/`, `bobcat/`, etc.)
4. **Annotates** each detection with behavioral context using Apple Foundation Models — on-device, private, no API key
5. **Flags** unusual timing (e.g. a nocturnal raccoon detected at midday) using a grounded species behavior table — no LLM guessing
6. **Writes** a `triage_log.json` with full details for every image

### Example output

```
  ✓  DCIM/IMG0042.JPG     white-tailed deer   94%  COPIED
       ↳ The deer is likely feeding along a field edge during its typical crepuscular activity period.

  ✓  DCIM/IMG0107.JPG     red fox             89%  COPIED
  ⚠️ ↳ UNUSUAL: Red foxes are crepuscular — daytime activity may indicate
       disturbance or mange; the fox is likely moving between cover.

  ?  DCIM/IMG0203.JPG     pileated woodpecker  32%  review
  ·  DCIM/IMG0311.JPG     blank                 —   skip

════════════════════════════════════════════════════════════
  Total        : 847
  ✓ Copied     : 634
  ? Review     : 48   (below confidence threshold)
  · Skipped    : 165  (blank / boring)
  Elapsed      : 142.3s
```

### Species summary

Before copying, camtriage prints what SpeciesNet found:

```
  SPECIES                        TOTAL    COPY
  ────────────────────────────────────────────
  white-tailed deer                312     312  ████████████████
  blank                            201       0
  eastern gray squirrel             67      67  ████
  red fox                           89      89  █████
  domestic cat                      43      43  ███
  northern raccoon                  38      38  ██
```

---

## Requirements

- **macOS** with Apple Silicon (M1 or later)
- **Python 3.9–3.13** (SpeciesNet constraint — use pyenv or Homebrew to install)
- **Apple Intelligence** enabled for behavioral annotation

> Vision annotation (image input to Apple FM) requires **macOS 27 Golden Gate** (fall 2026). Text annotation works today on macOS 26 Tahoe.

---

## Installation

```bash
# 1. Clone the repo
git clone https://github.com/robroy/camtriage.git
cd camtriage

# 2. Create a virtualenv with Python 3.11
python3.11 -m venv camenv
source camenv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and edit the config
cp config.example.yaml config.yaml
# Edit config.yaml — set your country/state, adjust species list for your region
```

---

## Usage

```bash
# Activate your virtualenv first
source camenv/bin/activate

# Basic usage
python camtriage.py /Volumes/SD_CARD ~/Desktop/Wildlife

# With explicit config
python camtriage.py /Volumes/SD_CARD ~/Desktop/Wildlife --config myconfig.yaml
```

### Arguments

| Argument | Description |
|----------|-------------|
| `source` | SD card or image folder to triage |
| `dest`   | Output folder — created if it doesn't exist |
| `--config` | Path to config YAML (default: `config.yaml` next to the script) |

---

## Configuration

Copy `config.example.yaml` to `config.yaml` and edit:

```yaml
location:
  country: USA          # ISO 3166-1 alpha-3
  state: ME             # ISO 3166-2 subdivision

min_confidence: 0.50    # raise for stricter triage

skip_labels:
  - blank
  - empty
  - human
  # add any species you want to suppress

species_behavior:
  northern raccoon: { active: nocturnal, flag_if: daytime }
  red fox:          { active: crepuscular, flag_if: daytime }
  # add species for your region
```

The full species behavior table — with entries for 25 North American species — is in `config.example.yaml`. Edit or extend it for your region. The tool ships with defaults tuned for the northeastern US (Maine).

---

## How it works

### SpeciesNet

[SpeciesNet](https://github.com/google/cameratrapai) is Google's open-source wildlife classifier, trained on millions of camera trap images from around the world. It classifies to species level and returns a confidence score. Geographic priors (country + state/province) improve accuracy by down-weighting species not present in your region.

Accuracy on the author's Maine game cam test set: **91.1%** vs 38.2% for a general-purpose vision LLM.

### Apple Foundation Models

Behavioral annotations use Apple's on-device Foundation Models framework via the [`apple-fm-sdk`](https://pypi.org/project/apple-fm-sdk/) Python SDK. Inference runs locally — no data leaves your machine.

The tool uses a grounded species behavior table rather than asking the LLM to recall behavioral facts (which it does unreliably). The model receives:

- Species name
- Time of day (from EXIF)
- Date (from EXIF)
- Whether the timing is unusual, based on the behavior table
- Location description

It then generates a one-sentence behavioral description. Unusual detections are prefixed with ⚠️.

### Output structure

```
dest/
├── white-tailed_deer/
│   ├── IMG0042.JPG
│   └── IMG0043.JPG
├── red_fox/
│   └── IMG0107.JPG
├── northern_raccoon/
│   └── IMG0201.JPG
└── triage_log.json
```

`triage_log.json` contains every image processed — label, confidence, destination path, behavioral annotation, EXIF date/time, and whether it needs human review.

---

## Video handling

Most game cams produce both still images and short video clips for the same
trigger event. camtriage handles video via **timestamp matching** — the simplest
approach that works well in practice.

### How it works

After classifying all stills, camtriage compares every video file's filesystem
timestamp against the timestamps of copied stills. If a video falls within the
configured window (default 60 seconds) of an interesting or alert still, the
video is copied to the same species folder.

```
  🎥  DCIM/DSCF0042.AVI    white-tailed deer    → white-tailed_deer/DSCF0042.AVI
  ·   3 video(s) had no matching still event — skipped
```

This works because game cams almost always shoot stills and video for the same
trigger event within seconds of each other. The rare case — video only, no
companion still — is noted in the summary and log.

### Video strategy options

Five approaches exist, from simplest to most complex:

| Option | Approach | Pros | Cons |
|--------|----------|------|------|
| **1. Keyframe extract** | ffmpeg extracts 1 frame/Ns → SpeciesNet → copy video if hit | Uses same classifier | Requires ffmpeg, adds time |
| **2. MegaDetector** | `run_md_and_speciesnet` handles video natively | Purpose-built | Heavier install, prefers NVIDIA GPU |
| **3. First frame only** | Extract thumbnail/first frame, classify it | Very fast | High false negative — animal may not be in first frame |
| **4. Copy all video** | Skip classification, copy everything to `_video/` | Zero false negatives | No triage value |
| **5. Timestamp match** ✓ | Match video to classified stills by timestamp | No video processing, leverages stills work | Misses video-only events (rare) |

**Option 5 is the default** — it requires no additional dependencies and handles
the overwhelming majority of real game cam footage correctly. If you find you
have significant video-only events, Option 1 (keyframe + ffmpeg) is the
recommended upgrade path.

## Roadmap

- [ ] Video support (frame extraction → SpeciesNet → copy original clip)
- [ ] Interactive species summary — confirm/adjust per-species decisions before copying
- [ ] macOS 27 Golden Gate: vision annotation via `ImageAttachment` (image input to Apple FM)
- [ ] Native Mac app (Swift) — SmartFiler integration

---

## Credits

- [SpeciesNet](https://github.com/google/cameratrapai) — Google, Apache 2.0
- [Apple Foundation Models SDK](https://pypi.org/project/apple-fm-sdk/) — Apple Inc.

---

## License

MIT — see [LICENSE](LICENSE)

Built by [Rob Roy](https://robroy.online) · [HomesteadAI](https://robroy.online)
