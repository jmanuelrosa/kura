"""Manage coding-agent artifacts for Claude Code and Pi from one catalog.

Claude Code installations are the source of truth. Kura maintains Pi-compatible
project views of their skills and agents.

Scope is decided by the `global` group tag, never by the working directory. A
tagged artifact belongs in ~/.claude; everything else belongs in the enclosing
git project. Landing in ~/.claude by direct request always needs --global, so the
call site says so out loud.

Dependencies resolve their own scope rather than inheriting the parent's: a
project skill may depend on a global one and the reverse.
"""

__all__ = ["cli", "errors", "paths"]
