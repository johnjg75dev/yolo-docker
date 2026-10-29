import pytest
from optical_modem.suite.jobs import parse_progress


def test_current_ultralytics_progress_and_pose_metrics():
    log = ('\x1b[K        2/10 0G 1.2 2.3 0.8 1.2 0.1 5.0 2 160: 40% ━━━ 2/5 1.2it/s\r'
           '                   all          2          2       0.8 0.7 0.6 0.5 0.4 0.3 0.2 0.1\n')
    progress, message, metrics = parse_progress('train', log)
    assert progress == pytest.approx(0.14)
    assert message == 'epoch 2/10 · 2/5'
    assert metrics['map50'] == 0.6
    assert metrics['pose_map50_95'] == 0.1


def test_legacy_progress_bar():
    progress, message, _ = parse_progress('train', '      2/10 0G 1.2 2.3:  20% 1/5 [00:01<00:04]\n')
    assert progress == pytest.approx(0.12)
    assert message == 'epoch 2/10 · 1/5'
