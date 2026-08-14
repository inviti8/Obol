"""Funding convenience: ARC-26 payment URIs and the QR codes that carry them.

The vault address alone is not the hard part of funding - `DESIGN.md` section 3.1
is. A user has to send ALGO first, then wait for an opt-in, then send USDC, and
the failure mode for getting the asset wrong is that the transfer is REJECTED
rather than held. So the QR's job is not "save typing an address". It is to carry
the *asset id* alongside the address, so the sending wallet is already pointed at
the right thing and cannot be aimed at the wrong one by hand.

ARC-26 is the Algorand URI scheme wallets scan. Verified against the spec at
`algorandfoundation/ARCs/ARCs/arc-0026.md` on 2026-08-13 rather than recalled:

    algorand://<ADDRESS>?amount=<base units>&asset=<id>&label=&note=&xnote=

**`amount` is in the asset's BASE units, not whole ones.** The spec is explicit:
*"If an amount is provided, it MUST be specified in basic unit of the asset ...
for 100 Algos, the amount needs to be 100000000"*. Its own ASA example is
`?amount=150&asset=45` - 150 base units, not 150 whole tokens.

That unit is why `amount` is **omitted by default here**. A funding QR's job is
address and asset correctness; the human types the figure into their own wallet
and sees it before confirming. Pre-filling a number multiplies any mistake in
this module by 10^decimals, and there is no version of that trade worth taking
for a convenience feature.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import segno
from algosdk import encoding as algo_encoding

from .errors import WalletError

ARC26_SCHEME = "algorand://"


def arc26_uri(
    address: str,
    *,
    asset: int | None = None,
    amount_base_units: int | None = None,
    label: str | None = None,
    note: str | None = None,
) -> str:
    """Build an ARC-26 URI. `amount_base_units` is BASE units - see module docs.

    The address is validated with algosdk rather than by length, because a
    mistyped-but-plausible address is exactly the input a QR would otherwise
    render scannable and permanent.
    """
    if not algo_encoding.is_valid_address(address):
        raise WalletError(f"Not a valid Algorand address: {address!r}")

    params: list[str] = []
    if amount_base_units is not None:
        if not isinstance(amount_base_units, int) or isinstance(amount_base_units, bool):
            raise WalletError("amount must be a whole number of base units.")
        if amount_base_units < 0:
            raise WalletError("amount must not be negative.")
        params.append(f"amount={amount_base_units}")
    if asset is not None:
        if not isinstance(asset, int) or asset < 0:
            raise WalletError("asset must be a non-negative asset id.")
        params.append(f"asset={asset}")
    if label:
        params.append(f"label={quote(label, safe='')}")
    if note:
        params.append(f"note={quote(note, safe='')}")

    uri = ARC26_SCHEME + address
    return f"{uri}?{'&'.join(params)}" if params else uri


def blocks_are_printable(stream=None) -> bool:
    """Can this stream encode the half-block glyphs a compact QR needs?

    On a stock Windows console the answer is no - cp1252 has no U+2588, and
    printing one raises UnicodeEncodeError rather than degrading. That crashed
    `obol vault qr` outright, which is a poor outcome for a convenience feature.
    """
    import sys

    stream = stream if stream is not None else sys.stdout
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        "█▀▄".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def qr_terminal(data: str, *, border: int = 2, compact: bool | None = None) -> str:
    """A QR as text, for a terminal.

    Compact uses half-block characters so the code stays square in a cell grid
    that is taller than it is wide - a QR drawn one row per module comes out
    stretched and some scanners will not read it. The non-compact form is pure
    ASCII plus ANSI colour, twice as tall but printable anywhere, and is chosen
    automatically when the output stream cannot encode the blocks.
    """
    import io

    if compact is None:
        compact = blocks_are_printable()
    buf = io.StringIO()
    segno.make(data, error="m").terminal(buf, border=border, compact=compact)
    return buf.getvalue()


def qr_bytes(data: str, *, kind: str = "png", scale: int = 8, border: int = 4) -> bytes:
    """A QR as file bytes. `kind` is any format segno writes - png, svg, pdf."""
    import io

    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind=kind, scale=scale, border=border)
    return buf.getvalue()


# Error correction for anything styled. Level H recovers ~30% of the symbol, and
# both things we do here eat into that budget: a centre logo removes modules
# outright, and inverting the colours makes some scanners work harder. Level M
# (~15%) is fine for a plain code and too thin for this one.
STYLED_ERROR = "h"

# Logo width as a fraction of the QR's. 0.20 covers ~4% of the area, plus the
# clear-zone box around it - comfortably inside level H's budget. Pushing toward
# 0.30 is where codes start failing on real phones, so this stays conservative.
LOGO_FRACTION = 0.20


def qr_styled_png(
    data: str,
    *,
    logo: bytes | None = None,
    scale: int = 12,
    border: int = 4,
    dark: str = "#FFFFFF",
    light: str = "#000000",
    caption: str | None = None,
    logo_fraction: float = LOGO_FRACTION,
) -> bytes:
    """A QR with inverted colours and an optional centred logo.

    `dark` is the colour of the code's modules and `light` is the background -
    segno's naming, which reads backwards here because we invert them: white
    modules on a black field.

    **INVERSION AND A LOGO BOTH COST SCAN RELIABILITY.** The QR spec assumes dark
    modules on a light field; most modern phone cameras handle the inverse, but
    not every scanner does, and a logo removes modules that error correction then
    has to reconstruct. Level H plus a conservative logo size keeps the margin
    wide, but this is a case where the only real test is scanning it with a
    phone. Plain `qr_bytes` remains the safe option.

    Pillow is a base dependency, so the composite always works. The import is
    still guarded because a stripped install is a thing people do, and a caller
    who asked for a logo should be told it is missing rather than handed an
    unbranded code.
    """
    import io

    symbol = segno.make(data, error=STYLED_ERROR)
    buf = io.BytesIO()
    symbol.save(buf, kind="png", scale=scale, border=border, dark=dark, light=light)
    png = buf.getvalue()
    if logo is None and caption is None:
        return png

    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise WalletError(
            "A centred logo needs Pillow, which is a dependency of this "
            "package - reinstall it. Colours work without it, so omitting the "
            "logo still produces a scannable code."
        ) from exc

    qr_img = Image.open(io.BytesIO(png)).convert("RGBA")
    if logo is None:
        return _captioned(qr_img, caption, dark, light)

    mark = Image.open(io.BytesIO(logo)).convert("RGBA")

    # The official asset is mostly transparent padding - its ink occupies about a
    # third of the canvas. Cropping to the alpha bounding box first is what stops
    # the logo rendering as a speck in the middle of the code.
    if (bbox := mark.getbbox()) is not None:
        mark = mark.crop(bbox)

    target = max(1, int(qr_img.width * logo_fraction))
    ratio = target / max(mark.width, mark.height)
    mark = mark.resize(
        (max(1, round(mark.width * ratio)), max(1, round(mark.height * ratio))),
        Image.LANCZOS,
    )

    # Clear a background-coloured box behind the logo. Without it a white logo
    # sits directly against white modules and the boundary disappears - which
    # hurts a human reading it and a scanner finding the timing patterns.
    pad = max(2, target // 6)
    box_w, box_h = mark.width + 2 * pad, mark.height + 2 * pad
    box = Image.new("RGBA", (box_w, box_h), light)
    box.alpha_composite(mark, (pad, pad))

    qr_img.alpha_composite(
        box, ((qr_img.width - box_w) // 2, (qr_img.height - box_h) // 2)
    )
    return _captioned(qr_img, caption, dark, light)


def _captioned(qr_img, caption: str | None, dark: str, light: str) -> bytes:
    """Add a caption band BELOW the code, never inside it.

    Text laid over the symbol would eat error correction on top of what the logo
    already costs. A band underneath costs nothing at all, and is easier to read.
    """
    import io

    from PIL import Image, ImageDraw, ImageFont

    if not caption:
        out = io.BytesIO()
        qr_img.save(out, format="PNG")
        return out.getvalue()

    size = max(16, qr_img.width // 10)
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - very old Pillow
        font = ImageFont.load_default()

    pad = size // 2
    scratch = ImageDraw.Draw(qr_img)
    left, top, right, bottom = scratch.textbbox((0, 0), caption, font=font)
    band = (bottom - top) + 2 * pad

    canvas = Image.new("RGBA", (qr_img.width, qr_img.height + band), light)
    canvas.alpha_composite(qr_img, (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.text(
        ((canvas.width - (right - left)) // 2 - left, qr_img.height + pad - top),
        caption,
        font=font,
        fill=dark,
    )
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def load_logo(path) -> bytes | None:
    """Read a logo asset, or None if it is not there.

    Missing branding is not an error - the code still works without it, and a
    funding QR that refuses to render because an image is absent would be a
    worse failure than an unbranded one.
    """
    from pathlib import Path

    p = Path(path)
    return p.read_bytes() if p.is_file() else None


def default_logo() -> bytes | None:
    """The Algorand mark shipped inside the package.

    Read through `importlib.resources` rather than a path relative to this file,
    so it still resolves when Obol is installed as a wheel or a zipapp rather
    than run from a checkout.
    """
    from importlib import resources

    try:
        return (
            resources.files("obolus.assets")
            .joinpath("algorand-logo.png")
            .read_bytes()
        )
    except (FileNotFoundError, ModuleNotFoundError):
        return None


@dataclass(frozen=True)
class AssetTheme:
    """How one asset's QR looks, so the two are never confused at a glance.

    Colour and caption both exist for the same reason the asset id is in the URI:
    sending the wrong asset is rejected outright, so the failure is worth
    designing against twice - once for the wallet, once for the human holding the
    phone.

    Modules stay light on a dark field in both themes. White on the USDC blue
    measures 4.68:1, comfortably past the ~3:1 that scanners want and past the
    4.5:1 accessibility bar; a lighter brand blue would not be.
    """

    key: str
    label: str
    background: str
    modules: str = "#FFFFFF"


THEME_ALGO = AssetTheme(key="algo", label="ALGO", background="#000000")
# Circle's USDC blue. Cosmetic, so change it freely - but re-check the contrast
# in `blocks_are_printable`'s neighbour test if you lighten it.
THEME_USDC = AssetTheme(key="usdc", label="USDC", background="#2775CA")


def theme_for(asset_label: str) -> AssetTheme:
    """Theme by asset label, defaulting to the ALGO look for anything unknown.

    An unrecognised ASA gets the neutral dark theme rather than borrowing USDC's
    blue, because the colour is a claim about which asset this is.
    """
    if asset_label.upper() == "ALGO":
        return THEME_ALGO
    if asset_label.upper() == "USDC":
        return THEME_USDC
    return AssetTheme(key=asset_label.lower(), label=asset_label, background="#000000")


@dataclass(frozen=True)
class FundingTarget:
    """One thing a human can send, with the URI a wallet should scan."""

    what: str          # "ALGO" or the asset's name/id
    why: str
    uri: str
    suggested: str | None = None
    theme: AssetTheme = THEME_ALGO


def funding_targets(
    address: str,
    payment_asa: int,
    *,
    network: str,
    algo_needed_micro: int,
    asset_label: str = "USDC",
) -> list[FundingTarget]:
    """The two things a vault can be sent, each as a scannable URI.

    Deliberately two entries and not one: they are different assets and the order
    between them is forced (section 3.1). A single QR cannot express "this one
    first, then that one".
    """
    label = f"Obol vault ({network})"
    return [
        FundingTarget(
            what="ALGO",
            why=(
                "Send this FIRST. Without it the vault cannot pay the fee for its "
                "own asset opt-in, and 0.1 is locked as the asset slot minimum."
            ),
            uri=arc26_uri(address, label=label, note="Obol vault funding: ALGO"),
            suggested=f"{algo_needed_micro / 1e6:.2f} ALGO",
            theme=THEME_ALGO,
        ),
        FundingTarget(
            what=asset_label,
            why=(
                "Send this ONLY after the vault is opted in. Before that the "
                "transfer is rejected outright - it does not sit pending."
            ),
            uri=arc26_uri(
                address,
                asset=payment_asa,
                label=label,
                note=f"Obol vault funding: ASA {payment_asa}",
            ),
            suggested=None,
            theme=theme_for(asset_label),
        ),
    ]
