"""Path confinement for `body_file` and `output_file`.

These parameters let an agent move bytes off the machine, and **no spend cap
bounds that** - the caps govern money. So the containment is the whole control,
and it is tested the way a security control should be: mostly with the attacks.

The escape that matters most is the symlink. A check that compares strings
("does the path start with the root?") passes a link inside the root that points
at /etc/shadow. `resolve()` walks the link first, which is why it is used here.
"""

from __future__ import annotations

import os

import pytest

from obolus.errors import WalletError
from obolus.files import read_body, resolve_within, write_output


# ---- disabled by default -------------------------------------------------


def test_no_root_configured_refuses(tmp_path):
    """A default install must not offer the capability at all."""
    with pytest.raises(WalletError, match="disabled"):
        resolve_within(None, "anything.txt", purpose="body_file")


def test_the_refusal_says_how_to_enable_it(tmp_path):
    with pytest.raises(WalletError) as exc:
        resolve_within(None, "x", purpose="body_file")
    assert "[files] root" in str(exc.value)


# ---- inside the root is fine ---------------------------------------------


def test_relative_path_resolves_under_the_root(tmp_path):
    (tmp_path / "a.txt").write_text("hi")
    assert resolve_within(tmp_path, "a.txt", purpose="body_file") == (
        tmp_path / "a.txt"
    ).resolve()


def test_nested_path_is_allowed(tmp_path):
    nested = tmp_path / "sub" / "deep"
    nested.mkdir(parents=True)
    (nested / "b.txt").write_text("hi")
    assert resolve_within(tmp_path, "sub/deep/b.txt", purpose="body_file").exists()


def test_absolute_path_inside_the_root_is_allowed(tmp_path):
    target = tmp_path / "c.txt"
    target.write_text("hi")
    assert resolve_within(tmp_path, str(target), purpose="body_file") == target.resolve()


def test_relative_paths_are_relative_to_the_root_not_the_cwd(tmp_path, monkeypatch):
    """The server's cwd is whatever the MCP client spawned it with.

    Resolving against it would make the same path mean different things in
    different clients, which is not something a user can reason about.
    """
    (tmp_path / "d.txt").write_text("hi")
    elsewhere = tmp_path.parent / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    monkeypatch.chdir(elsewhere)
    assert resolve_within(tmp_path, "d.txt", purpose="body_file").exists()


# ---- the attacks ---------------------------------------------------------


def test_dot_dot_traversal_refuses(tmp_path):
    with pytest.raises(WalletError, match="escapes the configured root"):
        resolve_within(tmp_path, "../../secrets.txt", purpose="body_file")


def test_absolute_path_outside_the_root_refuses(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")
    with pytest.raises(WalletError, match="escapes"):
        resolve_within(tmp_path, str(outside), purpose="body_file")


def test_sibling_prefix_is_not_inside_the_root(tmp_path):
    """`/root-evil` must not pass a check that `/root` is a prefix of it."""
    root = tmp_path / "root"
    root.mkdir()
    sibling = tmp_path / "root-evil"
    sibling.mkdir()
    (sibling / "x.txt").write_text("secret")
    with pytest.raises(WalletError, match="escapes"):
        resolve_within(root, str(sibling / "x.txt"), purpose="body_file")


@pytest.mark.skipif(
    os.name == "nt" and not os.environ.get("OBOL_TEST_SYMLINKS"),
    reason="symlink creation on Windows needs privilege; set OBOL_TEST_SYMLINKS to run",
)
def test_symlink_pointing_out_of_the_root_refuses(tmp_path):
    """The escape a string-prefix check would let through."""
    root = tmp_path / "root"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("private key")
    (root / "link.txt").symlink_to(secret)
    with pytest.raises(WalletError, match="escapes"):
        resolve_within(root, "link.txt", purpose="body_file")


def test_null_byte_refuses(tmp_path):
    with pytest.raises(WalletError, match="null byte"):
        resolve_within(tmp_path, "a\x00.txt", purpose="body_file")


def test_the_root_itself_is_allowed(tmp_path):
    assert resolve_within(tmp_path, ".", purpose="output_file") == tmp_path.resolve()


# ---- read and write ------------------------------------------------------


def test_read_body_returns_exact_bytes(tmp_path):
    blob = bytes(range(256))  # not valid UTF-8 anywhere
    (tmp_path / "img.bin").write_bytes(blob)
    assert read_body(tmp_path, "img.bin") == blob


def test_read_body_refuses_a_directory(tmp_path):
    (tmp_path / "sub").mkdir()
    with pytest.raises(WalletError, match="not a readable file"):
        read_body(tmp_path, "sub")


def test_read_body_refuses_a_missing_file(tmp_path):
    with pytest.raises(WalletError, match="not a readable file"):
        read_body(tmp_path, "nope.txt")


def test_write_output_creates_parent_directories(tmp_path):
    written = write_output(tmp_path, "out/deep/result.png", b"\x89PNG")
    assert written.read_bytes() == b"\x89PNG"
    assert written.parent.is_dir()


def test_write_output_refuses_outside_the_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(WalletError, match="escapes"):
        write_output(root, "../escaped.png", b"x")


def test_write_output_round_trips_binary(tmp_path):
    blob = os.urandom(4096)
    written = write_output(tmp_path, "blob.bin", blob)
    assert written.read_bytes() == blob
