"""`body` must survive the MCP framework's JSON pre-parsing.

Measured on 2026-09-01 against a live $0.25 endpoint: `x402_fetch(body='{...}')`
- the obvious call, and the one the tool schema asks for - failed validation
before any payment. `mcp.server.mcpserver.utilities.func_metadata.pre_parse_json`
runs `json.loads` on every string argument whose annotation is not *exactly*
`str`, to help clients that stringify objects. `body: str | None` is not exactly
`str`, so the string became a dict and pydantic rejected it as "not a valid
string". JSON is the most common paid-endpoint body type.

These tests drive the real framework rather than a mock, because the bug lives in
the framework's contract with our annotations, not in our code. A signature that
merely looks right passes a naive test and still cannot be called.
"""

from __future__ import annotations

import json

import pytest

from mcp.server.mcpserver.utilities.func_metadata import func_metadata

from obolus.mcp import tools


@pytest.fixture(scope="module")
def arg_model():
    """The pydantic model the framework builds from the real tool signature."""
    captured = {}

    class FakeServer:
        def tool(self, name, description):
            def deco(fn):
                captured[name] = fn
                return fn
            return deco

    tools.register(FakeServer(), wallet=None)
    return func_metadata(captured["x402_fetch"])


def validate(md, **kwargs):
    """Exactly what the server does to a tools/call payload."""
    return md.arg_model.model_validate(md.pre_parse_json(kwargs))


@pytest.mark.parametrize(
    "body",
    [
        '{"content":"hello","n":1}',   # the call that failed
        "[1, 2, 3]",                   # arrays pre-parse too
        {"content": "hello", "n": 1},  # a client that sends a real object
        [1, 2, 3],
        "hello world",                 # not JSON: must stay a plain string
        '{"nested":{"a":[1,{"b":2}]}}',
    ],
)
def test_body_accepts_json_in_either_shape(arg_model, body):
    validate(arg_model, url="https://example.test", body=body)


def test_a_plain_string_is_not_reinterpreted(arg_model):
    """Text, XML and form encoding must reach the wire untouched."""
    for raw in ("hello world", "<xml/>", "a=1&b=2", '"quoted"'):
        assert validate(arg_model, url="https://example.test", body=raw).body == raw


def test_object_body_is_serialised_and_labelled():
    """An object body must become JSON bytes AND carry the media type.

    The Content-Type half is not cosmetic: a paid endpoint verifies payment
    before its handler runs, so one that requires the header rejects after the
    money has moved.
    """
    from obolus.mcp.wallet import encode_body

    assert encode_body({"a": 1}, None) == (b'{"a":1}', "application/json")
    assert encode_body([1, 2], None) == (b"[1,2]", "application/json")

    # A caller who names a media type keeps it - some endpoints want a +json
    # subtype, and guessing over an explicit choice is not ours to do.
    assert encode_body({"a": 1}, "application/ld+json")[1] == "application/ld+json"


def test_string_bodies_reach_the_wire_untouched():
    """No re-encoding, and no Content-Type invented for something we did not build."""
    from obolus.mcp.wallet import encode_body

    for raw in ('{"a":  1}', "<xml/>", "a=1&b=2", "plain"):
        assert encode_body(raw, None) == (raw.encode(), None)
