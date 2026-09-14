# Isolate private harness trust adapters

Kura applies trust to every selected harness that is installed locally, while preserving each harness's native project identity.
Claude Code and Pi expose no common persistent trust API, and Pi documents only its interactive `/trust` flow, so automatic Pi support must follow a version-sensitive private file and lock contract.
That behavior is isolated behind harness-specific adapters, preflighted across all targets, and rolled back on partial failure rather than being represented as declarative profile data.
