PY ?= python3
export PYTHONPATH := $(CURDIR)

.PHONY: help install install-dev test test-cov frontend build ci info dataset train bench gui render sim clean

help:
	@grep -E '^[a-z]+:.*##' Makefile | sed 's/:.*##/\t/'

install:            ## install runtime python dependencies
	$(PY) -m pip install -r requirements.txt

install-dev:        ## install everything needed to run the tests
	$(PY) -m pip install -r requirements-dev.txt

test:               ## run the test suite (no env vars needed: see pyproject.toml)
	$(PY) -m pytest

test-cov:           ## run the suite and report coverage
	$(PY) -m pytest --cov=optical_modem --cov-report=term-missing

frontend:           ## install frontend dependencies
	cd frontend && npm ci

build:              ## typecheck + build the SPA into suite/static
	cd frontend && npm run build

ci:                 ## everything CI runs, in the order CI runs it
	$(MAKE) test
	$(MAKE) build

info:               ## capacity table for every modulation x ECC
	$(PY) -m optical_modem info

dataset:            ## generate the synthetic training set
	$(PY) -m optical_modem dataset --train 1200 --val 200

train:              ## fine-tune YOLO26-pose (CPU defaults; see docs/MODEL.md)
	$(PY) -m optical_modem.data.train_yolo --epochs 12 --imgsz 384 --batch 4 --workers 0

bench:              ## run benchmarks -> docs/BENCHMARKS.md
	$(PY) -m optical_modem bench --n 12

gui:                ## launch the web GUI on :8000
	$(PY) -m optical_modem gui --port 8889

render:             ## render one demo frame
	$(PY) -m optical_modem render --text "hello optical modem" --out frame.png

sim:                ## simulated transmitter -> camera -> receiver run
	$(PY) -m optical_modem sim --level 0.6 --n 10

clean:
	rm -rf .pytest_cache **/__pycache__ runs coverage.xml .coverage
