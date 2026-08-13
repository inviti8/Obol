"""ARC-26 URI construction.

The QR rendering is a thin wrapper over segno and not worth testing; the URI is
where a bug costs money. Two ways it could:

  * a wrong `amount` unit - the spec says BASE units, so treating a figure as
    whole tokens is a 10^decimals error in whichever direction is worse;
  * a wrong or malformed address rendered into something scannable, which is
    then permanent in a way a typo in a terminal is not.

So the address is validated with algosdk, and the amount is only ever emitted
when a caller passes base units explicitly.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from algosdk import account as algo_account

from obol.errors import WalletError
from obol.funding import arc26_uri, funding_targets, qr_bytes, qr_terminal

# A real, checksum-valid address; the module rejects anything else.
_, ADDR = algo_account.generate_account()
USDC_TESTNET = 10458941


def q(uri: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(uri).query)


# ---- shape ---------------------------------------------------------------


def test_bare_address_has_no_query():
    assert arc26_uri(ADDR) == f"algorand://{ADDR}"


def test_scheme_and_address_placement():
    uri = arc26_uri(ADDR, asset=USDC_TESTNET)
    assert uri.startswith("algorand://")
    # The address is the path, immediately after the scheme - not a query param.
    assert uri[len("algorand://"):].split("?")[0] == ADDR


def test_asset_id_is_emitted():
    assert q(arc26_uri(ADDR, asset=USDC_TESTNET))["asset"] == [str(USDC_TESTNET)]


# ---- the units, which are the whole risk ---------------------------------


def test_amount_is_omitted_unless_asked_for():
    """The default must never pre-fill a figure. See the module docstring."""
    assert "amount" not in q(arc26_uri(ADDR, asset=USDC_TESTNET))


def test_amount_is_passed_through_as_base_units_verbatim():
    """$1.00 of a 6-decimal asset is 1000000 base units, per the spec."""
    assert q(arc26_uri(ADDR, asset=USDC_TESTNET, amount_base_units=1_000_000))[
        "amount"
    ] == ["1000000"]


def test_spec_example_shape_reproduces():
    """ARC-26's own ASA example is `?amount=150&asset=45` - 150 BASE units."""
    parsed = q(arc26_uri(ADDR, asset=45, amount_base_units=150))
    assert parsed["amount"] == ["150"]
    assert parsed["asset"] == ["45"]


def test_zero_amount_is_allowed():
    assert q(arc26_uri(ADDR, amount_base_units=0))["amount"] == ["0"]


def test_negative_amount_refuses():
    with pytest.raises(WalletError, match="negative"):
        arc26_uri(ADDR, amount_base_units=-1)


def test_fractional_amount_refuses():
    """A float here means someone is thinking in whole tokens. Refuse loudly."""
    with pytest.raises(WalletError, match="whole number of base units"):
        arc26_uri(ADDR, amount_base_units=1.5)  # type: ignore[arg-type]


def test_bool_is_not_an_amount():
    with pytest.raises(WalletError, match="whole number of base units"):
        arc26_uri(ADDR, amount_base_units=True)  # type: ignore[arg-type]


# ---- the address ---------------------------------------------------------


def test_invalid_address_refuses():
    with pytest.raises(WalletError, match="Not a valid Algorand address"):
        arc26_uri("NOT-AN-ADDRESS")


def test_address_with_one_character_changed_refuses():
    """The checksum is the point - a plausible typo must not render scannable."""
    swapped = ("B" if ADDR[0] != "B" else "C") + ADDR[1:]
    with pytest.raises(WalletError):
        arc26_uri(swapped)


def test_negative_asset_refuses():
    with pytest.raises(WalletError, match="asset"):
        arc26_uri(ADDR, asset=-1)


# ---- text fields ---------------------------------------------------------


def test_label_and_note_are_percent_encoded():
    uri = arc26_uri(ADDR, label="Obol vault (testnet)", note="a&b=c d")
    assert " " not in uri, "a raw space would truncate the URI in some scanners"
    assert q(uri)["label"] == ["Obol vault (testnet)"]
    assert q(uri)["note"] == ["a&b=c d"], "ampersand must not split the query"


def test_empty_label_is_omitted():
    assert "label" not in q(arc26_uri(ADDR, label=""))


# ---- the two funding targets ---------------------------------------------


def test_two_targets_algo_first():
    """ALGO first, because the order is forced (section 3.1) and the list says so."""
    targets = funding_targets(
        ADDR, USDC_TESTNET, network="testnet", algo_needed_micro=210_000
    )
    assert [t.what for t in targets] == ["ALGO", "USDC"]


def test_algo_target_carries_no_asset_id():
    """An `asset` param on the ALGO URI would ask a wallet to send the wrong thing."""
    algo = funding_targets(
        ADDR, USDC_TESTNET, network="testnet", algo_needed_micro=210_000
    )[0]
    assert "asset" not in q(algo.uri)


def test_asset_target_carries_the_asset_id():
    asa = funding_targets(
        ADDR, USDC_TESTNET, network="testnet", algo_needed_micro=210_000
    )[1]
    assert q(asa.uri)["asset"] == [str(USDC_TESTNET)]


def test_targets_state_the_forced_order():
    targets = funding_targets(
        ADDR, USDC_TESTNET, network="testnet", algo_needed_micro=210_000
    )
    assert "FIRST" in targets[0].why
    assert "rejected outright" in targets[1].why


# ---- rendering -----------------------------------------------------------


def test_terminal_qr_is_non_empty_text():
    out = qr_terminal(arc26_uri(ADDR, asset=USDC_TESTNET))
    assert out.strip() and "\n" in out


def test_png_bytes_have_a_png_header():
    assert qr_bytes(arc26_uri(ADDR), kind="png")[:8].hex() == "89504e470d0a1a0a"


def test_svg_bytes_are_svg():
    assert b"<svg" in qr_bytes(arc26_uri(ADDR), kind="svg")


# ---- the Windows console, which crashed this outright --------------------


class _FakeStream:
    def __init__(self, encoding): self.encoding = encoding


def test_blocks_are_not_printable_on_cp1252():
    """A stock Windows console has no U+2588 and raises rather than degrading."""
    from obol.funding import blocks_are_printable

    assert not blocks_are_printable(_FakeStream("cp1252"))
    assert blocks_are_printable(_FakeStream("utf-8"))


def test_unknown_encoding_falls_back_rather_than_raising():
    from obol.funding import blocks_are_printable

    assert not blocks_are_printable(_FakeStream("not-a-real-codec"))


def test_ascii_fallback_survives_cp1252():
    """The regression: `obol vault qr` died with UnicodeEncodeError on Windows."""
    out = qr_terminal(arc26_uri(ADDR), compact=False)
    out.encode("cp1252")  # must not raise
    assert out.isascii()


def test_compact_form_is_shorter():
    uri = arc26_uri(ADDR)
    assert qr_terminal(uri, compact=True).count("\n") < qr_terminal(
        uri, compact=False
    ).count("\n")


# ---- styled codes --------------------------------------------------------

import struct

import segno

from obol.funding import LOGO_FRACTION, STYLED_ERROR, default_logo, qr_styled_png

pillow = pytest.importorskip if False else None


def png_size(png: bytes) -> tuple[int, int]:
    return struct.unpack(">II", png[16:24])


def test_styled_uses_high_error_correction():
    """A logo removes modules and inversion costs margin - level M is too thin."""
    assert STYLED_ERROR == "h"
    uri = arc26_uri(ADDR)
    assert len(list(segno.make(uri, error="h").matrix)) >= len(
        list(segno.make(uri, error="m").matrix)
    )


def test_logo_stays_conservatively_small():
    """Past roughly a third of the width, real phones start failing."""
    assert LOGO_FRACTION <= 0.25


def test_styled_without_logo_needs_no_pillow():
    """Colours are segno's job; only the composite needs the optional extra."""
    png = qr_styled_png(arc26_uri(ADDR))
    assert png[:8].hex() == "89504e470d0a1a0a"


def test_styled_geometry_matches_the_symbol():
    uri = arc26_uri(ADDR, asset=USDC_TESTNET)
    scale, border = 12, 4
    png = qr_styled_png(uri, scale=scale, border=border)
    modules = len(list(segno.make(uri, error=STYLED_ERROR).matrix))
    expected = (modules + 2 * border) * scale
    assert png_size(png) == (expected, expected)


def test_background_is_dark_and_modules_are_light():
    """The inversion the whole feature is about, asserted on real pixels."""
    Image = pytest.importorskip("PIL.Image", reason="Pillow is a base dependency; skipped only on a stripped install")
    import io

    png = qr_styled_png(arc26_uri(ADDR))
    img = Image.open(io.BytesIO(png)).convert("L")
    # The quiet zone is background: top-left corner sits inside the border.
    assert img.getpixel((2, 2)) < 40, "quiet zone should be dark"
    # A finder pattern's centre is a module: it must be light.
    scale, border = 12, 4
    centre = (border + 3) * scale + scale // 2
    assert img.getpixel((centre, centre)) > 200, "finder centre should be light"


def test_logo_is_composited_into_the_centre():
    Image = pytest.importorskip("PIL.Image", reason="Pillow is a base dependency; skipped only on a stripped install")
    import io

    logo = default_logo()
    assert logo is not None, "the mark must ship inside the package"
    uri = arc26_uri(ADDR, asset=USDC_TESTNET)
    plain = qr_styled_png(uri)
    branded = qr_styled_png(uri, logo=logo)
    assert plain != branded

    a = Image.open(io.BytesIO(plain)).convert("L")
    b = Image.open(io.BytesIO(branded)).convert("L")
    assert a.size == b.size, "compositing must not resize the code"
    # The corners are untouched; only the middle changes.
    assert a.getpixel((2, 2)) == b.getpixel((2, 2))
    cx = a.width // 2
    assert a.crop((cx - 20, cx - 20, cx + 20, cx + 20)).tobytes() != b.crop(
        (cx - 20, cx - 20, cx + 20, cx + 20)
    ).tobytes()


def test_packaged_logo_resolves_as_a_resource():
    """It must load from an installed wheel, not just a source checkout."""
    logo = default_logo()
    assert logo is not None and logo[:8].hex() == "89504e470d0a1a0a"


# ---- per-asset themes ----------------------------------------------------


def test_algo_and_usdc_get_distinct_backgrounds():
    """Colour is a claim about which asset this is, so they must not collide."""
    from obol.funding import THEME_ALGO, THEME_USDC

    assert THEME_ALGO.background != THEME_USDC.background
    assert THEME_ALGO.label == "ALGO" and THEME_USDC.label == "USDC"


def test_unknown_asset_does_not_borrow_usdc_blue():
    from obol.funding import THEME_USDC, theme_for

    theme = theme_for("ASA 769120200")
    assert theme.background != THEME_USDC.background
    assert theme.label == "ASA 769120200"


def test_theme_lookup_is_case_insensitive():
    from obol.funding import THEME_USDC, theme_for

    assert theme_for("usdc").background == THEME_USDC.background


def test_targets_carry_their_theme():
    targets = funding_targets(
        ADDR, USDC_TESTNET, network="testnet", algo_needed_micro=210_000
    )
    assert targets[0].theme.key == "algo"
    assert targets[1].theme.key == "usdc"


def test_stand_in_asset_is_not_called_usdc():
    """A self-minted ASA must not be labelled USDC anywhere a human reads it."""
    targets = funding_targets(
        ADDR,
        769120200,
        network="testnet",
        algo_needed_micro=210_000,
        asset_label="ASA 769120200",
    )
    assert targets[1].what == "ASA 769120200"
    assert "USDC" not in targets[1].what


def test_white_on_usdc_blue_clears_the_scanning_threshold():
    """Contrast is a scanability property, not a taste one."""
    from obol.funding import THEME_USDC

    def luminance(hexc: str) -> float:
        parts = [int(hexc[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        parts = [
            v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in parts
        ]
        return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]

    hi = luminance(THEME_USDC.modules)
    lo = luminance(THEME_USDC.background)
    hi, lo = max(hi, lo), min(hi, lo)
    assert (hi + 0.05) / (lo + 0.05) >= 3.0


def test_caption_grows_the_image_downward_only():
    """A caption must sit outside the symbol - inside it would eat error correction."""
    pytest.importorskip("PIL.Image", reason="Pillow is a base dependency; skipped only on a stripped install")
    uri = arc26_uri(ADDR)
    plain = qr_styled_png(uri)
    captioned = qr_styled_png(uri, caption="USDC")
    pw, ph = png_size(plain)
    cw, ch = png_size(captioned)
    assert cw == pw, "caption must not change the code's width"
    assert ch > ph, "caption occupies a band below the code"
