"""One exception hierarchy, so callers can distinguish outcomes that differ.

The distinction that matters to anything driving this wallet - a CLI, an MCP
server, an agent - is whether money moved:

    PaymentRefused   nothing was signed. Safe to retry with different arguments.
    CapExceeded      a refusal, and the user can raise the limit that caused it.
    PaymentRejected  we signed and sent. The resource was not served, and the
                     payment may or may not have settled. NOT safe to blind-retry.

Collapsing these into one error type is how an agent ends up paying twice for
something it did not get.
"""

from __future__ import annotations


class ObolError(Exception):
    """Base for everything this package raises deliberately."""


class PaymentRefused(ObolError):
    """We declined before signing. Nothing moved.

    Every refusal names what it objected to. A wallet that says only "refused"
    forces the user to guess, and the guesses are expensive.
    """


class CapExceeded(PaymentRefused):
    """A spend control refused. `limit` names which one, so a caller can say so.

    Separate from PaymentRefused because caps are the user's to change: an agent
    hitting a daily cap should surface "raise your daily limit", not "this payment
    is impossible".
    """

    def __init__(self, limit: str, message: str) -> None:
        super().__init__(message)
        self.limit = limit


class PaymentRejected(ObolError):
    """We signed and sent, and the resource was not served.

    Deliberately not a subclass of PaymentRefused: the money may have moved.
    """
