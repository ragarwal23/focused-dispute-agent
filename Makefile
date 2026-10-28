.PHONY: setup run build test

setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	[ -f .env ] || cp .env.example .env

run:
	.venv/bin/python -m app.server

build:
	cd app && npm ci && npm run build

test:
	.venv/bin/python -m pytest -q
