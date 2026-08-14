"""Show a funding code to a human, on a screen they are already looking at.

WHY THIS EXISTS. `wallet_funding_info` returns the QR as an MCP `ImageContent`
block, which is correct and which some clients render inline. Claude Code's
terminal does not - the image reaches the model and never reaches the person, so
the one party who needs to point a phone at it is the one who cannot see it.

A file plus the default browser beats a localhost server for the same job: no
port to pick, no process to outlive the request, no cleanup, and it still works
with the network down. The page is one file with the images inlined as data URIs,
so it keeps working after the process that wrote it is gone.

It writes to the system temp directory rather than the configured file root. The
root exists to confine paths an AGENT chose (see `files.py`); this path is ours,
holds nothing secret - a receiving address is public by construction - and being
ephemeral is the point.
"""

from __future__ import annotations

import base64
import html
import tempfile
import webbrowser
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ViewCard:
    """One asset's panel on the page."""

    label: str
    uri: str
    png: bytes
    why: str
    background: str
    suggested: str | None = None


_PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Obolus - fund {network}</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; padding:2rem 1rem 3rem; background:#0d0f12; color:#e8eaed;
         font:16px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif; }}
  main {{ max-width:64rem; margin:0 auto; }}
  h1 {{ font-size:1.35rem; margin:0 0 .35rem; font-weight:650; }}
  .sub {{ color:#9aa2ad; margin:0 0 2rem; font-size:.95rem; }}
  .net {{ display:inline-block; padding:.15rem .5rem; border-radius:999px;
          background:#1b2027; color:#9aa2ad; font-size:.8rem; margin-left:.4rem; }}
  .net.mainnet {{ background:#4a1d1d; color:#ffb4a8; }}
  .cards {{ display:flex; flex-wrap:wrap; gap:1.5rem; }}
  .card {{ flex:1 1 22rem; background:#14181d; border:1px solid #232a33;
           border-radius:14px; padding:1.25rem; }}
  .card h2 {{ margin:0 0 .2rem; font-size:1.05rem; }}
  .why {{ color:#9aa2ad; font-size:.9rem; margin:.35rem 0 1rem; }}
  .qr {{ display:block; width:100%; max-width:22rem; height:auto; margin:0 auto 1rem;
         border-radius:10px; }}
  .field {{ margin-top:.9rem; }}
  .field b {{ display:block; font-size:.75rem; letter-spacing:.06em;
              text-transform:uppercase; color:#7c8593; margin-bottom:.25rem; }}
  code {{ display:block; background:#0b0e11; border:1px solid #232a33; border-radius:8px;
          padding:.55rem .65rem; font:12.5px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;
          word-break:break-all; user-select:all; }}
  .note {{ margin-top:2rem; padding:1rem 1.15rem; border-left:3px solid #3b82f6;
           background:#111820; border-radius:0 10px 10px 0; color:#c6ccd4; font-size:.92rem; }}
  .note b {{ color:#e8eaed; }}
</style>
<main>
  <h1>Fund the Obolus vault<span class="net {netclass}">{network}</span></h1>
  <p class="sub">Scan with an Algorand wallet. Balances at time of writing: {balances}</p>
  <div class="cards">{cards}</div>
  <p class="note"><b>No amount is encoded in any code here.</b> Each one carries the
  receiving address and the asset id, so your wallet is pointed at the right asset
  and cannot be aimed at the wrong one by hand - which matters because sending the
  wrong asset is rejected outright rather than held. You type the amount into your
  own wallet, where you see it before confirming.</p>
</main>
"""

_CARD = """
  <section class="card">
    <h2>{label}</h2>
    <p class="why">{why}</p>
    <img class="qr" alt="{label} funding QR" src="data:image/png;base64,{b64}">
    {suggested}
    <div class="field"><b>Address</b><code>{address}</code></div>
    <div class="field"><b>ARC-26 URI</b><code>{uri}</code></div>
  </section>
"""


def render_page(
    cards: list[ViewCard], *, network: str, address: str, balances: dict[str, str]
) -> str:
    """One self-contained HTML page. No external requests, no server."""
    body = "".join(
        _CARD.format(
            label=html.escape(card.label),
            why=html.escape(card.why),
            b64=base64.b64encode(card.png).decode("ascii"),
            address=html.escape(address),
            uri=html.escape(card.uri),
            suggested=(
                f'<div class="field"><b>Suggested</b><code>{html.escape(card.suggested)}</code></div>'
                if card.suggested
                else ""
            ),
        )
        for card in cards
    )
    return _PAGE.format(
        network=html.escape(network),
        netclass="mainnet" if network == "mainnet" else "testnet",
        balances=html.escape(", ".join(f"{k} {v}" for k, v in balances.items())),
        cards=body,
    )


def write_page(page: str, *, network: str, tag: str) -> Path:
    """Write the page to a stable temp path, overwriting any previous one.

    Stable rather than unique so repeated top-ups do not litter the temp
    directory with one file per question asked.
    """
    path = Path(tempfile.gettempdir()) / f"obolus-funding-{network}-{tag}.html"
    path.write_text(page, encoding="utf-8")
    return path


def open_in_browser(path: Path) -> bool:
    """Open the page. False if there is no browser to open it with.

    Headless machines and locked-down desktops are a normal place to run an MCP
    server, so this reports failure instead of raising - the caller still has a
    path to hand back, and the URI is in the response either way.
    """
    try:
        return webbrowser.open(path.as_uri())
    except Exception:
        return False
