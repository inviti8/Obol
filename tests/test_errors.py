"""The exception hierarchy, and the property that killed the MCP server once.

`session.py` originally raised `SystemExit` for user-facing errors. That reads
fine in a CLI and is lethal in a server: SystemExit inherits from BaseException,
so it sails straight through `except Exception` and terminates the process. In
the MCP server it showed up as the whole thing dying mid-tool-call, with the
client seeing "Connection closed" and no message.
"""

from __future__ import annotations

import pytest

from obolus.errors import (
    CapExceeded,
    ObolusError,
    PaymentRefused,
    PaymentRejected,
    WalletError,
)


@pytest.mark.parametrize(
    "exc",
    [
        ObolusError("x"),
        WalletError("x"),
        PaymentRefused("x"),
        PaymentRejected("x"),
        CapExceeded("daily", "x"),
    ],
)
def test_every_error_is_catchable_as_exception(exc):
    """No library error may be a BaseException. A server must survive all of them."""
    assert isinstance(exc, Exception)
    assert not isinstance(exc, (SystemExit, KeyboardInterrupt, GeneratorExit))
    try:
        raise exc
    except Exception:
        pass  # the point: a broad handler catches it


def test_no_library_module_raises_systemexit():
    """Regression guard for the bug itself, enforced across the package.

    The CLI may exit; the library may not decide the process should end.
    """
    import pathlib

    offenders = []
    for path in pathlib.Path("obolus").rglob("*.py"):
        if path.name == "cli.py":
            continue  # the CLI is allowed to terminate; it is the process owner
        text = path.read_text(encoding="utf-8")
        if "raise SystemExit" in text:
            offenders.append(str(path))
    assert offenders == [], f"library modules must not raise SystemExit: {offenders}"


def test_cap_exceeded_carries_its_limit():
    exc = CapExceeded("daily", "over the daily cap")
    assert exc.limit == "daily"
    assert isinstance(exc, PaymentRefused)


def test_rejected_is_not_a_refusal():
    """We signed and sent - the money may have moved. Never a safe blind retry."""
    assert not issubclass(PaymentRejected, PaymentRefused)
    assert issubclass(PaymentRejected, ObolusError)
