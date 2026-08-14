"""The funding page shown to a human.

It exists because an MCP `ImageContent` block reaches the model and, in a
terminal client, never reaches the person who has to point a phone at the code.
So the page's job is to be openable and self-contained; these tests pin the two
properties that would silently break that.
"""

from __future__ import annotations

from obolus.view import ViewCard, render_page, write_page

CARD = ViewCard(
    label="USDC",
    uri="algorand://ADDR?asset=10458941",
    png=b"\x89PNG\r\n\x1a\nfake",
    why="Send this only after the opt-in.",
    background="#2775CA",
    suggested=None,
)
ADDRESS = "NJH7PU3LXJ2DYHQIZRHCDY5O4QW7HRIUQ5X3EO24VVF6MXA4BS6VNYYQFA"


def page(**kw) -> str:
    return render_page(
        [CARD], network="testnet", address=ADDRESS, balances={"ALGO": "1.0"}, **kw
    )


def test_page_makes_no_external_requests():
    """A funding page that needs the network is a funding page that can fail."""
    html = page()
    assert "http://" not in html and "https://" not in html
    assert "data:image/png;base64," in html, "the QR must be inlined, not linked"


def test_page_carries_the_address_and_uri():
    html = page()
    assert ADDRESS in html
    assert "algorand://ADDR?asset=10458941" in html


def test_page_states_that_no_amount_is_encoded():
    """The one thing a human must know before scanning."""
    assert "No amount is encoded" in page()


def test_mainnet_is_visually_distinct():
    plain = render_page([CARD], network="testnet", address=ADDRESS, balances={})
    live = render_page([CARD], network="mainnet", address=ADDRESS, balances={})
    assert 'class="net mainnet"' in live
    assert 'class="net mainnet"' not in plain


def test_content_is_escaped():
    """Card text reaches the page as text, never as markup."""
    nasty = ViewCard(
        label="<script>alert(1)</script>",
        uri="algorand://X",
        png=b"x",
        why="&<>\"'",
        background="#000000",
    )
    html = render_page([nasty], network="testnet", address=ADDRESS, balances={})
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_write_page_is_stable_not_unique(tmp_path, monkeypatch):
    """Repeated top-ups must not litter temp with a file per question asked."""
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    first = write_page("<p>a</p>", network="testnet", tag="usdc")
    second = write_page("<p>b</p>", network="testnet", tag="usdc")
    assert first == second
    assert second.read_text(encoding="utf-8") == "<p>b</p>"
    assert len(list(tmp_path.iterdir())) == 1


def test_write_page_separates_networks_and_assets(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    paths = {
        write_page("x", network=n, tag=t)
        for n in ("testnet", "mainnet")
        for t in ("algo", "usdc")
    }
    assert len(paths) == 4, "a mainnet page must never overwrite a testnet one"
