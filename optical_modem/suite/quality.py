"""Read-only dataset preflight. Findings are candidates for human review."""
import hashlib
import math
from pathlib import Path

import yaml

IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}


def inspect(root: Path, offset: int = 0, limit: int = 200) -> dict:
    issues = []
    counts = {}
    hashes = {}
    duplicates = 0
    # Dataset Studio defaults to four 3-dimensional keypoints. Respect a
    # declared schema and class list rather than silently judging all datasets
    # against that default.
    shape, classes = (4, 3), None
    try:
        config = yaml.safe_load((root / 'data.yaml').read_text()) or {}
        if isinstance(config, dict):
            declared = config.get('kpt_shape')
            if isinstance(declared, list) and len(declared) == 2 and type(declared[0]) is int and declared[0] > 0 and declared[1] in (2, 3):
                shape = tuple(declared)
            elif declared is not None:
                issues.append({'kind': 'invalid_schema', 'file': 'data.yaml', 'detail': 'Invalid keypoint shape; using four keypoints'})
            names = config.get('names')
            if isinstance(names, list):
                classes = set(range(len(names)))
            elif isinstance(names, dict):
                classes = {int(k) for k in names}
    except (OSError, ValueError, yaml.YAMLError):
        issues.append({'kind': 'invalid_schema', 'file': 'data.yaml', 'detail': 'Could not read dataset schema; using four keypoints'})
    for split in ('train', 'val'):
        folder = root / 'images' / split
        if not folder.resolve().is_relative_to(root.resolve()):
            issues.append({'kind': 'invalid_path', 'file': split, 'detail': 'Image directory is outside the dataset'})
            counts[split] = 0
            continue
        images = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT), key=lambda p: p.name) if folder.is_dir() else []
        counts[split] = len(images)
        stems = {}
        if not images:
            issues.append({'kind': 'empty_split', 'file': split, 'detail': 'No images in split'})
        for index, image in enumerate(images):
            def add(kind, detail):
                issues.append({'kind': kind, 'file': f'{split}/{image.name}', 'split': split, 'image': image.name, 'index': index, 'detail': detail})
            if not image.resolve().is_relative_to(root.resolve()):
                add('invalid_path', 'Image is outside the dataset')
                continue
            if image.stem in stems:
                add('shared_label', f'Shares a label filename with {stems[image.stem]}')
            else:
                stems[image.stem] = image.name
            label = root / 'labels' / split / (image.stem + '.txt')
            if not label.resolve().is_relative_to(root.resolve()):
                add('invalid_path', 'Label is outside the dataset')
            elif not label.is_file():
                add('missing_label', 'No label file; may be an intentional background image')
            else:
                try:
                    lines = [line for line in label.read_text(encoding='utf-8').splitlines() if line.strip()]
                    if not lines:
                        add('empty_label', 'Label is empty; may be an intentional background image')
                    for line_no, line in enumerate(lines, 1):
                        fields = line.split()
                        try:
                            cls = int(fields[0])
                            if len(fields) != 5 + shape[0] * shape[1] or cls < 0 or (classes is not None and cls not in classes):
                                raise ValueError('wrong field count or unknown class')
                            coords = [float(x) for x in fields[1:]]
                            if not all(math.isfinite(x) for x in coords):
                                raise ValueError('non-finite coordinate')
                            x, y, w, h = coords[:4]
                            if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1 and x-w/2 >= -1e-6 and x+w/2 <= 1+1e-6 and y-h/2 >= -1e-6 and y+h/2 <= 1+1e-6):
                                raise ValueError('invalid bounding box')
                            outside = False
                            for i in range(4, len(coords), shape[1]):
                                if not (0 <= coords[i] <= 1 and 0 <= coords[i+1] <= 1 and (shape[1] == 2 or coords[i+2] in (0, 1, 2))):
                                    raise ValueError('invalid keypoint or visibility')
                                if (shape[1] == 2 or coords[i+2] > 0) and not (x-w/2-1e-6 <= coords[i] <= x+w/2+1e-6 and y-h/2-1e-6 <= coords[i+1] <= y+h/2+1e-6):
                                    outside = True
                            if outside:
                                add('suspicious_label', f'Line {line_no}: visible keypoint outside its bounding box')
                        except (ValueError, OverflowError, IndexError) as exc:
                            add('invalid_label', f'Line {line_no}: {exc or "invalid pose label"} (expected {shape[0]} keypoints with {shape[1]} values each)')
                            break
                except (OSError, UnicodeError):
                    add('unreadable_label', 'Label could not be read as UTF-8')
            try:
                digest = hashlib.sha256()
                with image.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
                key = digest.digest()
                if key in hashes:
                    duplicates += 1
                    add('duplicate', f'Identical to {hashes[key]}')
                else:
                    hashes[key] = f'{split}/{image.name}'
            except OSError:
                add('unreadable_image', 'Image could not be read')
    return {'counts': counts, 'issue_count': len(issues), 'duplicates': duplicates, 'issues': issues[offset:offset + limit], 'truncated': offset + limit < len(issues), 'offset': offset}
