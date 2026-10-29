from optical_modem.suite.quality import inspect


def test_quality_finds_empty_invalid_missing_and_duplicates(tmp_path):
    (tmp_path / 'data.yaml').write_text('kpt_shape: [4, 3]\nnames: [frame]\n')
    train = tmp_path / 'images/train'
    labels = tmp_path / 'labels/train'
    train.mkdir(parents=True); labels.mkdir(parents=True)
    (train / 'one.jpg').write_bytes(b'same')
    (train / 'two.jpg').write_bytes(b'same')
    (labels / 'one.txt').write_text('0 1 2 3\n')
    report = inspect(tmp_path)
    assert report['counts'] == {'train': 2, 'val': 0}
    assert {i['kind'] for i in report['issues']} == {'empty_split', 'invalid_label', 'missing_label', 'duplicate'}



def test_quality_labels_schema_and_paginated_findings(tmp_path):
    (tmp_path / 'data.yaml').write_text('kpt_shape: [2, 3]\nnames: [frame]\n')
    images = tmp_path / 'images/train'; labels = tmp_path / 'labels/train'
    images.mkdir(parents=True); labels.mkdir(parents=True)
    for name in ('a', 'b', 'c', 'd'):
        (images / f'{name}.jpg').write_bytes(name.encode())
    (labels / 'a.txt').write_text('0 .5 .5 .4 .4 .4 .4 2 .6 .6 2\n')
    (labels / 'b.txt').write_text('   \n')
    (labels / 'c.txt').write_text('1 .5 .5 .4 .4 .4 .4 2 .6 .6 2\n')
    report = inspect(tmp_path, 0, 2)
    assert report['issue_count'] == 4  # empty, invalid class, missing, empty val
    assert report['truncated']
    assert [x['kind'] for x in report['issues']] == ['empty_label', 'invalid_label']
    assert report['issues'][0]['index'] == 1
    tail = inspect(tmp_path, 2, 2)
    assert [x['kind'] for x in tail['issues']] == ['missing_label', 'empty_split']
    assert not tail['truncated']


def test_quality_does_not_follow_external_label(tmp_path):
    (tmp_path / 'data.yaml').write_text('names: [frame]\n')
    (tmp_path / 'images/train').mkdir(parents=True)
    (tmp_path / 'images/train/a.jpg').write_bytes(b'a')
    (tmp_path / 'labels').symlink_to(tmp_path.parent, target_is_directory=True)
    assert any(i['kind'] == 'invalid_path' for i in inspect(tmp_path)['issues'])


def test_quality_flags_keypoint_outside_box(tmp_path):
    (tmp_path / 'data.yaml').write_text('kpt_shape: [1, 3]\nnames: [frame]\n')
    (tmp_path / 'images/train').mkdir(parents=True)
    (tmp_path / 'labels/train').mkdir(parents=True)
    (tmp_path / 'images/train/a.jpg').write_bytes(b'a')
    (tmp_path / 'labels/train/a.txt').write_text('0 .5 .5 .2 .2 .9 .9 2\n')
    assert any(i['kind'] == 'suspicious_label' for i in inspect(tmp_path)['issues'])
