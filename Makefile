# kura

PYTEST := uv run --offline --with pytest --with pyyaml pytest

.PHONY: test build checksum clean

test:
	$(PYTEST) -q tests

build:
	python3 build.py

# Checksum names the exact asset bytes; kura --version names the semver for humans.
checksum: build
	shasum -a 256 dist/kura

clean:
	python3 -c "import shutil; shutil.rmtree('dist', ignore_errors=True)"
