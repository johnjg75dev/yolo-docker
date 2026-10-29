"""Safe, reversible crops for YOLO pose dataset images."""
import io
import hashlib
import json
import math
import os
import tempfile
import threading
import shutil
from pathlib import Path

from PIL import Image


def paths(dataset, split, filename):
    if split not in ('train', 'val') or not filename or filename in ('.', '..') or Path(filename).name != filename or '\\' in filename:
        raise ValueError('Invalid split or filename')
    image = dataset / 'images' / split / filename
    label = dataset / 'labels' / split / (Path(filename).stem + '.txt')
    backup = dataset / '.originals' / split / filename
    root = dataset.resolve()
    journal = dataset / '.edit-journal' / split / filename
    if any(not path.resolve().is_relative_to(root) for path in (image, label, backup, journal)):
        raise ValueError('Dataset paths must stay within the dataset')
    if image.is_symlink() or label.is_symlink():
        raise ValueError('Symbolic link images or labels are not supported')
    return image, label, backup


def revision(dataset, split, filename):
    """Digest image and label so a preview cannot overwrite newer edits."""
    image, label, _ = paths(dataset, split, filename)
    if not image.is_file():
        raise ValueError('Image not found')
    digest = hashlib.sha256()
    for path in (image, label):
        digest.update(b'present' if path.is_file() else b'missing')
        if path.is_file():
            with path.open('rb') as stream:
                for part in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(part)
    return digest.hexdigest()


_LOCK = threading.RLock()


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.edit-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def recover(dataset, split, filename):
    """Roll back an interrupted two-file edit before the next access."""
    with _LOCK:
        image, label, _ = paths(dataset, split, filename)
        journal = dataset / '.edit-journal' / split / filename
        if not (journal / 'ready').exists():
            return
        _atomic_write(image, (journal / 'image').read_bytes())
        if (journal / 'label').exists():
            _atomic_write(label, (journal / 'label').read_bytes())
        else:
            label.unlink(missing_ok=True)
        invalidate(dataset, split, filename)
        shutil.rmtree(journal)


def _replace_pair(dataset, split, filename, image_data, label_data):
    image, label, _ = paths(dataset, split, filename)
    journal = dataset / '.edit-journal' / split / filename
    if journal.exists():
        shutil.rmtree(journal)
    journal.mkdir(parents=True)
    try:
        _atomic_write(journal / 'image', image.read_bytes())
        if label.is_file():
            _atomic_write(journal / 'label', label.read_bytes())
        _atomic_write(journal / 'ready', b'1')
        _atomic_write(image, image_data)
        if label_data is None:
            label.unlink(missing_ok=True)
        else:
            _atomic_write(label, label_data)
    except Exception:
        if (journal / 'ready').exists():
            recover(dataset, split, filename)
        else:
            shutil.rmtree(journal, ignore_errors=True)
        raise
    shutil.rmtree(journal)
    invalidate(dataset, split, filename)


def crop(dataset, split, filename, x, y, width, height):
    with _LOCK:
        recover(dataset, split, filename)
        return _crop(dataset, split, filename, x, y, width, height)


def _crop(dataset, split, filename, x, y, width, height):
    image, label, backup = paths(dataset, split, filename)
    if not image.is_file():
        raise ValueError('Image not found')
    if any(type(v) is not int for v in (x, y, width, height)):
        raise ValueError('Crop coordinates must be integers')
    with Image.open(image) as im:
        iw, ih = im.size
        if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > iw or y + height > ih:
            raise ValueError('Crop must fit inside the image')
        # Transform every box and keypoint; refuse destructive clipping.
        lines = []
        text = label.read_text() if label.is_file() else ''
        for line in text.splitlines():
            if not line.strip():
                continue
            parts = line.split()
            try:
                if len(parts) < 5 or (len(parts) - 5) % 3:
                    raise ValueError()
                cx, cy, bw, bh = map(float, parts[1:5])
                if not all(math.isfinite(v) for v in (cx, cy, bw, bh)) or bw <= 0 or bh <= 0:
                    raise ValueError("Invalid bounding box")
                left, top = (cx - bw / 2) * iw, (cy - bh / 2) * ih
                right, bottom = (cx + bw / 2) * iw, (cy + bh / 2) * ih
                if left < x - 1e-5 or top < y - 1e-5 or right > x + width + 1e-5 or bottom > y + height + 1e-5:
                    raise ValueError('Crop would clip a bounding box')
                output = [parts[0], f'{(cx*iw-x)/width:.6f}', f'{(cy*ih-y)/height:.6f}', f'{bw*iw/width:.6f}', f'{bh*ih/height:.6f}']
                for i in range(5, len(parts), 3):
                    px, py, v = float(parts[i]), float(parts[i+1]), int(float(parts[i+2]))
                    if not math.isfinite(px) or not math.isfinite(py) or v not in (0, 1, 2):
                        raise ValueError("Invalid keypoint")
                    if v and not (x <= px*iw <= x+width and y <= py*ih <= y+height):
                        raise ValueError('Crop would clip a visible keypoint')
                    if not v:
                        output.extend(['0', '0', str(v)])
                    else:
                        output.extend([f'{(px*iw-x)/width:.6f}', f'{(py*ih-y)/height:.6f}', str(v)])
                lines.append(' '.join(output))
            except (ValueError, OverflowError) as exc:
                raise ValueError(str(exc) or 'Invalid YOLO label') from exc
        buf = io.BytesIO()
        im.crop((x, y, x + width, y + height)).save(buf, format=im.format)
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.backup-', dir=backup.parent))
        try:
            _atomic_write(staging / 'image', image.read_bytes())
            _atomic_write(staging / 'meta.json', json.dumps({'label_exists': label.is_file()}).encode())
            if label.is_file():
                _atomic_write(staging / 'label', label.read_bytes())
            os.rename(staging, backup)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    label_data = ('\n'.join(lines) + ('\n' if lines else '')).encode() if label.is_file() else None
    _replace_pair(dataset, split, filename, buf.getvalue(), label_data)


def invalidate(dataset, split, filename):
    thumbs = dataset / '.thumbs' / split
    if thumbs.exists():
        for size in thumbs.iterdir():
            if size.is_dir():
                for cached in size.iterdir():
                    if cached.name.startswith(filename + '.') and cached.name.endswith('.jpg'):
                        cached.unlink(missing_ok=True)
                (size / (filename + '.jpg')).unlink(missing_ok=True)
                (size / (Path(filename).stem + '.jpg')).unlink(missing_ok=True)


def revert(dataset, split, filename):
    with _LOCK:
        recover(dataset, split, filename)
        image, label, backup = paths(dataset, split, filename)
        if not (backup / 'image').is_file() or not image.is_file():
            raise ValueError('No original backup exists')
        meta = json.loads((backup / 'meta.json').read_text())
        label_data = (backup / 'label').read_bytes() if meta['label_exists'] else None
        _replace_pair(dataset, split, filename, (backup / 'image').read_bytes(), label_data)
