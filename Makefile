# kura

PYTEST := uv run --offline --with pytest --with pyyaml pytest

.PHONY: test build checksum clean requirements

test:
	$(PYTEST) -q tests

build:
	python3 build.py

# Checksum names the exact asset bytes; kura --version names the semver for humans.
checksum: build
	shasum -a 256 dist/kura

# CI installs with --require-hashes, so a bump goes through here and not a hand edit.
requirements:
	uv pip compile requirements-test.in --universal --python-version 3.13 --generate-hashes --no-header -o requirements-test.txt

clean:
	python3 -c "import shutil; shutil.rmtree('dist', ignore_errors=True)"
