"""The version has three homes, and a release is unrecoverable if they disagree.

`release.yml` checks the git tag against `pyproject.toml`, which leaves two gaps
it cannot see: `obolus.__version__`, which is what an installed copy reports when
someone files a bug, and `server.json`, which is what the MCP Registry serves.
PyPI will not let a version be replaced, so a mismatch is discovered by users and
fixed only by burning the next number.

Skipped rather than failed when the metadata is absent: the package is importable
from a wheel, where neither file ships.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from obolus import __version__

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
SERVER_JSON = ROOT / "server.json"

needs_source_tree = pytest.mark.skipif(
    not PYPROJECT.exists(), reason="not a source checkout"
)


@needs_source_tree
def test_dunder_version_matches_pyproject():
    packaged = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    assert __version__ == packaged


@needs_source_tree
def test_server_json_matches_pyproject():
    server = json.loads(SERVER_JSON.read_text(encoding="utf-8"))
    assert server["version"] == __version__
    # The Registry entry names a PyPI distribution and a version of it. Both are
    # published artifacts; a wrong one points installers at something that does
    # not exist, or worse, at the old name.
    pkg = next(p for p in server["packages"] if p["registryType"] == "pypi")
    assert pkg["version"] == __version__
    assert pkg["identifier"] == tomllib.loads(
        PYPROJECT.read_text(encoding="utf-8")
    )["project"]["name"]


@needs_source_tree
def test_no_stale_obol_spelling_in_user_facing_text():
    """The rename left `obol vault optin` in error messages people are told to run.

    That command does not exist on a fresh install - the console script is
    `obolus`. Two spellings are deliberate and excluded: the HMAC domain
    separator in keys.py, which would re-derive every session account if it
    changed, and the legacy data-dir fallback in config.py, which is the only
    way back to money already on chain.
    """
    allowed = {"keys.py", "config.py"}
    offenders = []
    for path in (ROOT / "obolus").rglob("*.py"):
        if path.name in allowed:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "OBOL_" in line:  # env var aliases are kept on purpose
                continue
            for spelling in ("obol ", "obol-", "obol/", "Obol "):
                if spelling in line and spelling.replace("obol", "obolus") not in line:
                    offenders.append(f"{path.name}:{n}: {line.strip()}")
                    break
    assert not offenders, "stale pre-rename spelling:\n" + "\n".join(offenders)
