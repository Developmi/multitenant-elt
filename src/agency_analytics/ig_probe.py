"""Backward-compatibility shim for the Instagram diagnostic probe.

The standalone diagnostic CLI has been relocated to tools/diagnostics/ig_probe.py
to keep the core production application package lightweight.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import tools.diagnostics.ig_probe as _ig_probe_mod  # noqa: E402

sys.modules[__name__] = _ig_probe_mod
