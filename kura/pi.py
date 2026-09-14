"""Pi's profile-backed native skill paths.

Pi receives independent per-skill links through `views`.
This module retains the Pi-specific import surface without recreating the retired Claude-backed directory bridge or plugin-agent projection.
"""

from . import harnesses

PROFILE = harnesses.get("pi")


def project_skill_root(project):
    return harnesses.project_skill_root(project, PROFILE.id)


def global_skill_root(home):
    return harnesses.global_skill_root(home, PROFILE.id)
