"""Disposable API fixture for browser tests; never touches the normal DATA_DIR."""
import os
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix='studio-e2e-'))
os.environ.update(DATA_DIR=str(root), APP_PASSWORD='', COOKIE_SECURE='0', SUITE_DB='sqlite')
from PIL import Image
for split in ('train', 'val'):
    images = root / 'datasets/sample/images' / split
    images.mkdir(parents=True)
    (root / 'datasets/sample/labels' / split).mkdir(parents=True)
    Image.new('RGB', (100, 80), 'navy').save(images / 'frame.png')
# A split spanning multiple pages exercises navigation at the 200-image boundary.
long_images = root / 'datasets/long/images/train'
long_images.mkdir(parents=True)
(root / 'datasets/long/labels/train').mkdir(parents=True)
for i in range(201):
    Image.new('RGB', (4, 4), 'blue').save(long_images / f'{i:03d}.png')
(root / 'datasets/long/data.yaml').write_text(f'path: {root / "datasets/long"}\ntrain: images/train\nval: images/train\nnames: [frame]\n')
(root / 'datasets/sample/data.yaml').write_text(f'path: {root / "datasets/sample"}\ntrain: images/train\nval: images/val\nkpt_shape: [4, 3]\nnames: [frame]\n')
import uvicorn
uvicorn.run('optical_modem.suite.app:app', host='127.0.0.1', port=8889, log_level='warning')
