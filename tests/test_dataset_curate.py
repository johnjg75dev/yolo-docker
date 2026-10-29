from pathlib import Path

import pytest
import yaml

from optical_modem.suite.curate import curated_copy


def source_dataset(tmp_path):
    root = tmp_path / 'source'
    for split in ('train', 'val'):
        (root / 'images' / split).mkdir(parents=True)
        (root / 'labels' / split).mkdir(parents=True)
        (root / 'images' / split / 'a.jpg').write_bytes(b'image')
        (root / 'labels' / split / 'a.txt').write_text('label')
    (root / 'data.yaml').write_text('path: old\ntrain: old/train\nval: old/val\nkpt_shape: [4, 3]\nnames: [frame]\n')
    return root


def test_curated_copy_preserves_source_and_schema(tmp_path):
    source = source_dataset(tmp_path)
    target = tmp_path / 'target'
    count = curated_copy(source, target, {'train': ['a.jpg'], 'val': ['a.jpg']}, source / 'data.yaml')
    assert count == 2
    assert (target / 'labels/val/a.txt').read_text() == 'label'
    config = yaml.safe_load((target / 'data.yaml').read_text())
    assert config['path'] == str(target.resolve())
    assert config['kpt_shape'] == [4, 3]
    assert config['train'] == 'images/train'
    assert (source / 'data.yaml').read_text().startswith('path: old')


def test_reject_invalid_selection_and_existing_target(tmp_path):
    source = source_dataset(tmp_path)
    target = tmp_path / 'target'
    for selected in ({}, {'train': ['../a.jpg']}, {'train': ['missing.jpg']}, {'test': ['a.jpg']}, {'train': ['a.jpg', 'a.jpg']}):
        with pytest.raises(ValueError):
            curated_copy(source, target, selected, source / 'data.yaml')
        assert not target.exists()
    target.mkdir()
    with pytest.raises(ValueError):
        curated_copy(source, target, {'train': ['a.jpg']}, source / 'data.yaml')


def test_unlabelled_image_and_symlink(tmp_path):
    source = source_dataset(tmp_path)
    (source / 'labels/train/a.txt').unlink()
    curated_copy(source, tmp_path / 'target', {'train': ['a.jpg']}, source / 'data.yaml')
    assert not (tmp_path / 'target/labels/train/a.txt').exists()
    (source / 'images/train/link.jpg').symlink_to(Path('/etc/passwd'))
    with pytest.raises(ValueError):
        curated_copy(source, tmp_path / 'other', {'train': ['link.jpg']}, source / 'data.yaml')
