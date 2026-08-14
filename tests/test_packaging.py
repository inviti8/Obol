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

import argparse
import json
import tomllib
from pathlib import Path

import pytest

from obolus import __version__

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
SERVER_JSON = ROOT / "server.json"
PLUGIN_JSON = ROOT / "plugins" / "obolus" / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = ROOT / ".claude-plugin" / "marketplace.json"

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
def test_claude_plugin_matches_pyproject():
    """The fourth home for the version, and the one with a silent failure mode.

    Claude Code takes `version` from `plugin.json` and ignores the marketplace
    entry's copy without warning, so a second copy is a trap rather than a
    redundancy - this asserts there is exactly one. The plugin tracks the package
    version because it launches that package; a plugin claiming 0.2.2 while
    fetching something else is a support conversation nobody can win.
    """
    plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    assert plugin["version"] == __version__

    market = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))
    entry = next(p for p in market["plugins"] if p["name"] == plugin["name"])
    assert "version" not in entry, "set version in plugin.json only; the entry's copy is ignored"


@needs_source_tree
def test_plugin_launches_the_published_package():
    """The plugin's MCP command must be one the CLI actually answers.

    Same failure as the Registry entry: `uvx obolus` alone reaches the CLI, which
    exits with a usage error rather than speaking MCP. The plugin has to pass the
    subcommand too, and it has to be a real one.
    """
    from obolus.cli import build_parser

    mcp_json = json.loads((PLUGIN_JSON.parent.parent / ".mcp.json").read_text(encoding="utf-8"))
    server = mcp_json["mcpServers"]["obolus"]
    assert server["command"] == "uvx"

    pkg = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["name"]
    assert server["args"][0] == pkg

    subparsers = next(
        a.choices for a in build_parser()._actions  # noqa: SLF001
        if isinstance(a, argparse._SubParsersAction)  # noqa: SLF001
    )
    assert server["args"][1] in subparsers

    # Testnet is the default in config.py, but the plugin states it anyway: a
    # one-command install of a wallet should make its safe setting visible to
    # anyone reading the file, not leave it implied.
    assert server["env"]["OBOLUS_NETWORK"] == "testnet"

    # The seed must not live in the plugin directory - plugin dirs are replaced
    # on update and the seed is the only way back to money already on chain.
    assert "OBOLUS_DATA_DIR" not in server.get("env", {})


@needs_source_tree
def test_registry_package_arguments_are_real_cli_commands():
    """The Registry launch command must reach a server, not the CLI's usage error.

    A client builds its command from the PyPI distribution name: `uvx obolus`.
    That runs the `obolus` console script, which is the CLI - bare, it exits 2
    with "the following arguments are required: command". The listing would
    install cleanly and present as a server that dies on startup.

    server.json therefore carries `packageArguments: ["mcp"]`. This checks that
    every positional it names is a subcommand the CLI actually has, because the
    failure is silent: nothing in a normal test run or a release would notice
    the entry pointing at a verb that no longer exists.
    """
    from obolus.cli import build_parser

    server = json.loads(SERVER_JSON.read_text(encoding="utf-8"))
    pkg = next(p for p in server["packages"] if p["registryType"] == "pypi")
    positionals = [
        a["value"] for a in pkg.get("packageArguments", [])
        if a.get("type") == "positional" and "value" in a
    ]
    assert positionals, "server.json must pass a subcommand, or uvx runs the CLI"

    subparsers = next(
        a.choices for a in build_parser()._actions  # noqa: SLF001
        if isinstance(a, argparse._SubParsersAction)  # noqa: SLF001
    )
    for value in positionals:
        assert value in subparsers, f"server.json launches `obolus {value}`, which does not exist"


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
