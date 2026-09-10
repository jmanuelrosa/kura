# kura

PYTEST := uv run --offline --with pytest --with pyyaml pytest

.PHONY: test build checksum clean

test:
	$(PYTEST) -q tests

build:
	python3 build.py

# The identity a release is pinned by: there is no --version flag, so the tag names
# the release for a human and this names it for a machine.
checksum: build
	shasum -a 256 dist/kura

clean:
	python3 -c "import shutil; shutil.rmtree('dist', ignore_errors=True)"
