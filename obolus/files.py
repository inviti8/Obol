"""Path confinement for the file parameters on `x402_fetch`.

WHY THIS MODULE EXISTS AT ALL. `body_file` lets an agent say "POST the bytes at
this path to this URL". That is exactly the shape of an exfiltration primitive:
an agent under prompt injection could aim it at a private key and a merchant of
the attacker's choosing, and **no spend cap bounds it**, because the caps govern
money and this moves bytes. `output_file` is the same hazard pointing the other
way - it writes attacker-influenced content to a path the agent picked.

So both are **off unless a root is configured**, and confined to that root when
it is. A default install has no such primitive at all.

The check is `resolve()`-then-contain, not string prefixing. `resolve()` walks
symlinks and normalises `..` before the comparison, which is what makes a symlink
inside the root pointing outside it fail rather than pass.
"""

from __future__ import annotations

from pathlib import Path

from .errors import WalletError


def resolve_within(root: Path | None, candidate: str, *, purpose: str) -> Path:
    """Resolve `candidate` and prove it stays inside `root`.

    A relative path is taken relative to `root`, not to the process's working
    directory - the server's cwd is whatever the MCP client happened to spawn it
    with, which is not something a user can reason about.
    """
    if root is None:
        raise WalletError(
            f"{purpose} is disabled: no file root is configured. Set "
            "`[files] root` in config.toml to the directory Obolus may read from "
            "and write to. It is off by default because it lets an agent move "
            "bytes off this machine, which no spend cap can bound."
        )

    if "\x00" in candidate:
        raise WalletError(f"{purpose} path contains a null byte.")

    root_resolved = root.expanduser().resolve()
    raw = Path(candidate).expanduser()
    target = raw if raw.is_absolute() else root_resolved / raw

    # resolve() normalises `..` and follows symlinks, so a link inside the root
    # that points outside it resolves to the outside path and fails below. Doing
    # this with string prefixes instead is the classic way to get it wrong.
    # strict=False so a not-yet-existing output file still resolves.
    resolved = target.resolve()

    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise WalletError(
            f"{purpose} path escapes the configured root.\n"
            f"  asked for: {candidate}\n"
            f"  resolves to: {resolved}\n"
            f"  root: {root_resolved}\n"
            "Refusing. Move the file under the root, or change `[files] root`."
        )
    return resolved


def read_body(root: Path | None, candidate: str) -> bytes:
    path = resolve_within(root, candidate, purpose="body_file")
    if not path.is_file():
        raise WalletError(f"body_file is not a readable file: {path}")
    return path.read_bytes()


def write_output(root: Path | None, candidate: str, content: bytes) -> Path:
    path = resolve_within(root, candidate, purpose="output_file")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path
