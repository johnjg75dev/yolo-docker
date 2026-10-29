from PIL import Image
import pytest

from optical_modem.suite import crop


def dataset(tmp_path):
    image = tmp_path / 'images/train/frame.png'
    label = tmp_path / 'labels/train/frame.txt'
    image.parent.mkdir(parents=True)
    label.parent.mkdir(parents=True)
    Image.new('RGB', (100, 80), 'red').save(image)
    label.write_text('0 0.5 0.5 0.4 0.5 0.3 0.25 2 0.7 0.75 2\n')
    return image, label


def test_crop_transforms_and_reverts_original(tmp_path):
    image, label = dataset(tmp_path)
    original_image, original_label = image.read_bytes(), label.read_bytes()
    crop.crop(tmp_path, 'train', 'frame.png', 20, 10, 60, 60)
    with Image.open(image) as im:
        assert im.size == (60, 60)
    parts = label.read_text().split()
    assert [float(x) for x in parts[1:5]] == pytest.approx([0.5, 0.5, 2/3, 2/3])
    assert [float(parts[5]), float(parts[6])] == pytest.approx([1/6, 1/6], abs=1e-6)
    crop.crop(tmp_path, 'train', 'frame.png', 0, 0, 60, 60)
    crop.revert(tmp_path, 'train', 'frame.png')
    assert image.read_bytes() == original_image
    assert label.read_bytes() == original_label


def test_crop_rejects_clipping_without_modification(tmp_path):
    image, label = dataset(tmp_path)
    original = image.read_bytes(), label.read_bytes()
    with pytest.raises(ValueError, match='clip'):
        crop.crop(tmp_path, 'train', 'frame.png', 40, 0, 60, 80)
    assert (image.read_bytes(), label.read_bytes()) == original
    assert not (tmp_path / '.originals').exists()


def test_unlabelled_revert_and_thumbnail_invalidation(tmp_path):
    image, label = dataset(tmp_path)
    label.unlink()
    thumbnail = tmp_path / '.thumbs/train/128/frame.jpg'
    thumbnail.parent.mkdir(parents=True)
    thumbnail.write_bytes(b'old')
    crop.crop(tmp_path, 'train', 'frame.png', 0, 0, 50, 50)
    assert not thumbnail.exists()
    label.write_text('later label')
    crop.revert(tmp_path, 'train', 'frame.png')
    assert not label.exists()
    with Image.open(image) as im:
        assert im.size == (100, 80)


def test_invalid_crop_and_split(tmp_path):
    dataset(tmp_path)
    with pytest.raises(ValueError):
        crop.crop(tmp_path, 'train', 'frame.png', 0, 0, 101, 80)
    with pytest.raises(ValueError):
        crop.paths(tmp_path, '../train', 'frame.png')


def test_interrupted_edit_is_recovered(tmp_path):
    image, label = dataset(tmp_path)
    image_before, label_before = image.read_bytes(), label.read_bytes()
    journal = tmp_path / '.edit-journal/train/frame.png'
    journal.mkdir(parents=True)
    (journal / 'image').write_bytes(image_before)
    (journal / 'label').write_bytes(label_before)
    (journal / 'ready').write_text('1')
    image.write_bytes(b'incomplete')
    label.write_text('incomplete')
    crop.recover(tmp_path, 'train', 'frame.png')
    assert image.read_bytes() == image_before
    assert label.read_bytes() == label_before
    assert not journal.exists()


def test_write_failure_rolls_back(tmp_path, monkeypatch):
    image, label = dataset(tmp_path)
    before = image.read_bytes(), label.read_bytes()
    real = crop._atomic_write
    failed = False
    def fail_once(path, data):
        nonlocal failed
        if path == label and not failed:
            failed = True
            raise OSError('disk error')
        return real(path, data)
    monkeypatch.setattr(crop, '_atomic_write', fail_once)
    with pytest.raises(OSError):
        crop.crop(tmp_path, 'train', 'frame.png', 20, 10, 60, 60)
    assert (image.read_bytes(), label.read_bytes()) == before


def test_reject_parent_symlink_escape(tmp_path):
    root = tmp_path / 'dataset'
    root.mkdir()
    outside = tmp_path / 'outside'
    (outside / 'train').mkdir(parents=True)
    (root / 'images').symlink_to(outside, target_is_directory=True)
    # Rejected by name, at the symlinked *component*: the previous check only
    # noticed after resolving the whole path, so it reported a containment
    # failure that pointed at the leaf instead of the actual cause.
    with pytest.raises(ValueError, match='symbolic link'):
        crop.paths(root, 'train', 'image.jpg')
    with pytest.raises(ValueError):
        crop.paths(root, 'train', '..')
