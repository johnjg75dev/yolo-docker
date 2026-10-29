"""Build a non-destructive, selected-image copy of a YOLO dataset."""
import shutil
from pathlib import Path

import yaml

from . import paths as pmod


def curated_copy(source: Path, destination: Path, selected: dict, yaml_path: Path):
    if destination.exists():
        raise ValueError('Destination dataset already exists')
    if not isinstance(selected, dict) or set(selected) - {'train', 'val'}:
        raise ValueError('Invalid split selection')
    files = []
    for split in ('train', 'val'):
        names = selected.get(split, [])
        if not isinstance(names, list):
            raise ValueError('Invalid image selection')
        for name in names:
            # same containment rule as everywhere else, via the one
            # implementation: this copy used to allow any suffix the others
            # rejected and to check only the image for symlinks
            try:
                image = pmod.resolve_within(source, 'images', split,
                                            pmod.safe_image_name(name))
                label = pmod.resolve_within(source, 'labels', split,
                                            Path(name).stem + '.txt',
                                            must_exist=False)
            except pmod.UnsafePath as exc:
                raise ValueError(f'Invalid image selection: {exc}') from exc
            if not image.is_file():
                raise ValueError(f'Image not found: {name}')
            files.append((split, image, label))
    if not files:
        raise ValueError('Select at least one image')
    if len(set((s, p.name) for s, p, _ in files)) != len(files):
        raise ValueError('Duplicate image selection')
    try:
        config = yaml.safe_load(yaml_path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError('Invalid dataset YAML') from exc
    if not isinstance(config, dict):
        raise ValueError('Invalid dataset YAML')
    # The copied files use the standard split layout, independent of the source paths.
    config.update(path=str(destination.resolve()), train='images/train', val='images/val')
    config.pop('test', None)
    try:
        destination.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ValueError('Destination dataset already exists') from exc
    try:
        for split in ('train', 'val'):
            (destination / 'images' / split).mkdir(parents=True)
            (destination / 'labels' / split).mkdir(parents=True)
        for split, image, label in files:
            shutil.copy2(image, destination / 'images' / split / image.name)
            if label.is_file():
                shutil.copy2(label, destination / 'labels' / split / label.name)
        (destination / 'data.yaml').write_text(yaml.safe_dump(config, sort_keys=False))
    except Exception:
        shutil.rmtree(destination)
        raise
    return len(files)
