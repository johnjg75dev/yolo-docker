PY ?= python3
export PYTHONPATH := $(CURDIR)

.PHONY: help install test info dataset train bench gui render sim clean

help:
	@grep -E '^[a-z]+:.*##' Makefile | sed 's/:.*##/\t/'

install:            ## install python dependencies
	$(PY) -m pip install -r requirements.txt

test:               ## run the test suite
	$(PY) -m pytest -q

info:               ## capacity table for every modulation x ECC
	$(PY) -m optical_modem info

dataset:            ## generate the synthetic training set
	$(PY) -m optical_modem dataset --train 1200 --val 200

train:              ## fine-tune YOLO26-pose (CPU defaults; see docs/MODEL.md)
	$(PY) -m optical_modem.data.train_yolo --epochs 12 --imgsz 384 --batch 4 --workers 0

bench:              ## run benchmarks -> docs/BENCHMARKS.md
	$(PY) -m optical_modem bench --n 12

gui:                ## launch the web GUI on :8000
	$(PY) -m optical_modem gui --port 8000

render:             ## render one demo frame
	$(PY) -m optical_modem render --text "hello optical modem" --out frame.png

sim:                ## simulated transmitter -> camera -> receiver run
	$(PY) -m optical_modem sim --level 0.6 --n 10

clean:
	rm -rf .pytest_cache **/__pycache__ runs
