#!/usr/bin/env python3
"""
camtriage — Wildlife camera trap triage tool
============================================

Walks a source folder (SD card or any image directory), runs Google's
SpeciesNet wildlife classifier on every image, copies interesting images
to a destination folder organised by species, and annotates each with
behavioral context using Apple Foundation Models (on-device, no API key).

Usage:
    python camtriage.py <source> <dest> [--config config.yaml]

    source  Path to SD card or image folder
    dest    Output folder (created if it does not exist)
    config  Path to config file (default: config.yaml in script directory)

Requirements:
    Python 3.9–3.13   (SpeciesNet constraint)
    speciesnet        pip install speciesnet
    pyyaml            pip install pyyaml
    Pillow            pip install Pillow        (optional, for EXIF)
    apple-fm-sdk      pip install apple-fm-sdk  (optional, for annotation)

See config.example.yaml for all configuration options.
"""

import sys
import os
import json
import shutil
import tempfile
import subprocess
import asyncio
import argparse
from datetime import datetime
from pathlib import Path
from collections import defaultdict

# ── Optional dependencies ─────────────────────────────────────────────────────

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False
    print("Warning: pyyaml not installed. Run: pip install pyyaml")

try:
    from PIL import Image as PILImage
    from PIL.ExifTags import TAGS
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import apple_fm_sdk as fm
    _fm_model = fm.SystemLanguageModel()
    _fm_available, _ = _fm_model.is_available()
    HAS_FM = _fm_available
except Exception:
    HAS_FM = False

# ── Constants ─────────────────────────────────────────────────────────────────

IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')

DEFAULT_CONFIG = {
    'location': {
        'country': 'USA',
        'state':   'ME',
        'location_description': 'Maine, USA',
    },
    'min_confidence': 0.50,
    'skip_labels': [
        'blank', 'empty', 'vehicle', 'human', 'person',
        'homo sapiens', 'animalia',
    ],
    'species_behavior': {
        'northern raccoon':      {'active': 'nocturnal',   'flag_if': 'daytime'},
        'raccoon':               {'active': 'nocturnal',   'flag_if': 'daytime'},
        'red fox':               {'active': 'crepuscular', 'flag_if': 'daytime'},
        'white-tailed deer':     {'active': 'crepuscular', 'flag_if': 'daytime'},
        'eastern gray squirrel': {'active': 'diurnal',     'flag_if': 'night'},
        'striped skunk':         {'active': 'nocturnal',   'flag_if': 'daytime'},
        'bobcat':                {'active': 'crepuscular', 'flag_if': 'daytime'},
        'wild turkey':           {'active': 'diurnal',     'flag_if': 'night'},
        'domestic cat':          {'active': 'any',         'flag_if': None},
        'pileated woodpecker':   {'active': 'diurnal',     'flag_if': 'night'},
        'american crow':         {'active': 'diurnal',     'flag_if': 'night'},
        'corvus species':        {'active': 'diurnal',     'flag_if': 'night'},
        'common raven':          {'active': 'diurnal',     'flag_if': 'night'},
        'coyote':                {'active': 'crepuscular', 'flag_if': 'daytime'},
        'black bear':            {'active': 'crepuscular', 'flag_if': 'night'},
        'moose':                 {'active': 'crepuscular', 'flag_if': 'daytime'},
        'fisher':                {'active': 'nocturnal',   'flag_if': 'daytime'},
        'porcupine':             {'active': 'nocturnal',   'flag_if': 'daytime'},
    },
    'annotation': {
        'enabled': True,
    },
}

# ── Config loading ────────────────────────────────────────────────────────────

def load_config(config_path):
    """Load YAML config, falling back to defaults for missing keys."""
    cfg = DEFAULT_CONFIG.copy()
    if not config_path or not os.path.exists(config_path):
        return cfg
    if not HAS_YAML:
        print(f"Warning: cannot load {config_path} — install pyyaml")
        return cfg
    with open(config_path) as f:
        user = yaml.safe_load(f) or {}
    # Deep merge top-level sections
    for key in ('location', 'annotation'):
        if key in user:
            cfg[key] = {**cfg.get(key, {}), **user[key]}
    for key in ('min_confidence', 'skip_labels'):
        if key in user:
            cfg[key] = user[key]
    if 'species_behavior' in user:
        cfg['species_behavior'] = {**cfg['species_behavior'], **user['species_behavior']}
    return cfg

# ── EXIF ──────────────────────────────────────────────────────────────────────

def read_exif(image_path):
    result = {'time_str': None, 'date_str': None}
    if not HAS_PIL:
        return result
    try:
        img  = PILImage.open(image_path)
        exif = img._getexif()
        if not exif:
            return result
        for tag_id, value in exif.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag == 'DateTimeOriginal' and isinstance(value, str):
                parts = value.split(' ')
                if len(parts) == 2:
                    result['date_str'] = parts[0].replace(':', '-')
                    result['time_str'] = parts[1]
    except Exception:
        pass
    return result

def time_of_day(time_str):
    if not time_str:
        return None
    try:
        hour = int(time_str.split(':')[0])
        if   5  <= hour < 8:  return 'dawn'
        elif 8  <= hour < 17: return 'daytime'
        elif 17 <= hour < 20: return 'dusk'
        else:                  return 'night'
    except Exception:
        return None

# ── Species behavior lookup ───────────────────────────────────────────────────

def get_behavior(label, species_behavior):
    label_lower = label.lower().strip()
    if label_lower in species_behavior:
        return species_behavior[label_lower]
    for key, val in species_behavior.items():
        if key in label_lower or label_lower in key:
            return val
    return {'active': 'unknown', 'flag_if': None}

# ── Apple FM annotation ───────────────────────────────────────────────────────

async def annotate(label, exif, cfg):
    if not HAS_FM or not cfg['annotation'].get('enabled', True):
        return None

    tod      = time_of_day(exif.get('time_str'))
    date     = exif.get('date_str')
    beh      = get_behavior(label, cfg['species_behavior'])
    active   = beh['active']
    flag_if  = beh['flag_if']
    location = cfg['location'].get('location_description', 'this region')

    is_unusual = (flag_if is not None and tod is not None and flag_if == tod)

    timing_note = (
        f"UNUSUAL TIMING: {label.capitalize()} are {active} — "
        f"detecting one at {tod} is not typical and may indicate stress, "
        f"illness, or disturbance."
        if is_unusual else
        f"{label.capitalize()} are {active} — {tod or 'unknown time'} "
        f"activity is normal."
    )

    date_note = f" Detected on {date}." if date else ""

    prompt = (
        f"A trail camera in {location} detected a {label}"
        f"{' at ' + tod if tod else ''}{date_note} "
        f"{timing_note} "
        f"In one sentence, describe the most likely specific behavior "
        f"(foraging, territorial patrol, den-seeking, etc.)."
    )

    try:
        session  = fm.LanguageModelSession(model=_fm_model)
        response = await session.respond(prompt)
        prefix   = "⚠️  " if is_unusual else ""
        return f"{prefix}{response.strip()}"
    except Exception as e:
        return f"annotation error: {e}"

# ── SpeciesNet ────────────────────────────────────────────────────────────────

def run_speciesnet(image_paths, cfg):
    country = cfg['location']['country']
    state   = cfg['location']['state']
    print(f"  Running SpeciesNet ({country}/{state}) on {len(image_paths)} images...")

    results = {}
    try:
        out_json = tempfile.mktemp(suffix='_predictions.json')
        if os.path.exists(out_json):
            os.unlink(out_json)

        cmd = [
            sys.executable, '-m', 'speciesnet.scripts.run_model',
            '--folders', os.path.commonpath(image_paths),
            '--predictions_json', out_json,
            '--country', country,
            '--admin1_region', state,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode != 0:
            print(f"  SpeciesNet error:\n{result.stderr[-400:]}")
            return {}

        if not os.path.exists(out_json) or os.path.getsize(out_json) == 0:
            print("  SpeciesNet produced empty output.")
            return {}

        with open(out_json) as f:
            data = json.load(f)
        os.unlink(out_json)

        preds = data.get('predictions', [])
        print(f"  SpeciesNet returned {len(preds)} predictions")

        for pred in preds:
            filepath        = pred.get('filepath', '')
            classifications = pred.get('classifications', {})
            classes = classifications.get('classes', []) if isinstance(classifications, dict) else []
            scores  = classifications.get('scores',  []) if isinstance(classifications, dict) else []

            raw_label  = classes[0] if classes else (pred.get('prediction', '') or '')
            label      = raw_label.split(';')[-1].strip() if ';' in raw_label else raw_label
            confidence = float(scores[0]) if scores else 0.0

            entry = {'label': label, 'confidence': confidence}
            results[filepath]                   = entry
            results[os.path.basename(filepath)] = entry

    except Exception as e:
        import traceback
        print(f"  SpeciesNet batch failed: {e}")
        traceback.print_exc()

    return results

# ── Helpers ───────────────────────────────────────────────────────────────────

def is_interesting(label, confidence, cfg):
    if not label:
        return False
    skip = {s.lower() for s in cfg.get('skip_labels', [])}
    if label.lower().strip() in skip:
        return False
    if confidence < cfg.get('min_confidence', 0.50):
        return False
    return True

def safe_copy(src, dest_dir, label):
    safe_label = label.replace(' ', '_').replace('/', '_').lower()
    label_dir  = os.path.join(dest_dir, safe_label)
    os.makedirs(label_dir, exist_ok=True)
    fname = os.path.basename(src)
    dest  = os.path.join(label_dir, fname)
    if os.path.exists(dest):
        base, ext = os.path.splitext(fname)
        counter = 1
        while os.path.exists(dest):
            dest = os.path.join(label_dir, f"{base}_{counter}{ext}")
            counter += 1
    shutil.copy2(src, dest)
    return dest

def print_species_summary(sn_results, cfg):
    """Print a summary of what SpeciesNet found before triaging."""
    counts = defaultdict(lambda: {'count': 0, 'interesting': 0})
    for path, pred in sn_results.items():
        if os.path.basename(path) == path:
            continue  # skip basename duplicates
        label      = pred['label']
        confidence = pred['confidence']
        counts[label]['count'] += 1
        if is_interesting(label, confidence, cfg):
            counts[label]['interesting'] += 1

    print(f"\n{'─'*60}")
    print(f"  {'SPECIES':<30} {'TOTAL':>6}  {'COPY':>6}")
    print(f"{'─'*60}")
    for label, c in sorted(counts.items(), key=lambda x: -x[1]['count']):
        bar = '█' * min(20, c['count'] // 2 + 1)
        print(f"  {label:<30} {c['count']:>6}  {c['interesting']:>6}  {bar}")
    print(f"{'─'*60}\n")

# ── Main triage ───────────────────────────────────────────────────────────────

async def triage_async(source, dest, cfg):
    os.makedirs(dest, exist_ok=True)
    started = datetime.now()

    print(f"\ncamtriage")
    print(f"  Source     : {source}")
    print(f"  Dest       : {dest}")
    print(f"  Location   : {cfg['location']['country']}/{cfg['location']['state']}")
    print(f"  Confidence : ≥{cfg['min_confidence']:.0%}")
    print(f"  Apple FM   : {'✓ available' if HAS_FM else '✗ not available'}")
    print(f"  EXIF       : {'✓ available' if HAS_PIL else '✗ install Pillow'}")

    # Collect images
    images = sorted(
        os.path.join(dp, f)
        for dp, _, files in os.walk(source)
        for f in files
        if f.lower().endswith(IMAGE_EXTENSIONS)
    )

    if not images:
        print("\nNo images found.")
        return

    print(f"  Images     : {len(images)}\n")

    # SpeciesNet batch pass
    sn = run_speciesnet(images, cfg)
    if not sn:
        print("No SpeciesNet results — exiting.")
        return

    # Species summary
    print_species_summary(sn, cfg)

    log_rows = []
    copied   = 0
    skipped  = 0
    reviewed = 0   # low confidence — neither copied nor skipped

    print("Triaging...")
    for img_path in images:
        pred = sn.get(img_path) or sn.get(os.path.basename(img_path))
        if not pred:
            label, confidence = 'unknown', 0.0
        else:
            label      = pred['label']
            confidence = pred['confidence']

        interesting = is_interesting(label, confidence, cfg)
        low_conf    = (not interesting and label.lower() not in
                       {s.lower() for s in cfg.get('skip_labels', [])}
                       and confidence > 0)
        dest_path   = None
        annotation  = None

        if interesting:
            dest_path = safe_copy(img_path, dest, label)
            copied += 1
            status = "COPIED"
            if HAS_FM:
                exif       = read_exif(img_path)
                annotation = await annotate(label, exif, cfg)
        elif low_conf:
            reviewed += 1
            status = "review"   # below threshold but not blank — flag for human
        else:
            skipped += 1
            status = "skip  "

        rel_src = os.path.relpath(img_path, source)
        marker  = "✓" if interesting else ("?" if low_conf else "·")
        print(f"  {marker}  {rel_src:<50}  {label:<28}  {confidence:.0%}  {status}")
        if annotation:
            print(f"       ↳ {annotation}")

        exif = read_exif(img_path) if HAS_PIL else {}
        log_rows.append({
            'source':      img_path,
            'label':       label,
            'confidence':  f"{confidence:.0%}",
            'interesting': interesting,
            'needs_review': low_conf,
            'dest':        dest_path or '',
            'annotation':  annotation or '',
            'time_of_day': time_of_day(exif.get('time_str')),
            'date':        exif.get('date_str') or '',
        })

    # ── Write log ─────────────────────────────────────────────────────────────
    elapsed  = (datetime.now() - started).total_seconds()
    log_path = os.path.join(dest, 'triage_log.json')

    log = {
        'run_at':          started.isoformat(),
        'source':          source,
        'dest':            dest,
        'config':          {
            'country':        cfg['location']['country'],
            'state':          cfg['location']['state'],
            'min_confidence': cfg['min_confidence'],
        },
        'total_images':    len(images),
        'copied':          copied,
        'needs_review':    reviewed,
        'skipped':         skipped,
        'elapsed_s':       round(elapsed, 1),
        'apple_fm':        HAS_FM,
        'images':          log_rows,
    }
    with open(log_path, 'w') as f:
        json.dump(log, f, indent=2)

    print(f"\n{'═'*60}")
    print(f"  Total        : {len(images)}")
    print(f"  ✓ Copied     : {copied}")
    print(f"  ? Review     : {reviewed}  (below confidence threshold)")
    print(f"  · Skipped    : {skipped}  (blank / boring)")
    print(f"  Elapsed      : {elapsed:.1f}s")
    print(f"  Log          : {log_path}")
    print(f"{'═'*60}\n")

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Wildlife camera trap triage using SpeciesNet + Apple FM'
    )
    parser.add_argument('source', help='Source folder (SD card or image directory)')
    parser.add_argument('dest',   help='Destination folder for sorted images')
    parser.add_argument(
        '--config', '-c',
        default=os.path.join(os.path.dirname(__file__), 'config.yaml'),
        help='Path to config.yaml (default: config.yaml next to this script)'
    )
    args = parser.parse_args()

    if not os.path.isdir(args.source):
        print(f"Error: source '{args.source}' is not a directory.")
        sys.exit(1)

    cfg = load_config(args.config)
    asyncio.run(triage_async(args.source, args.dest, cfg))

if __name__ == '__main__':
    main()
