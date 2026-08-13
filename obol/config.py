"""Settings, network profiles and the data directory.

Two rules this module exists to enforce.

**The payment asset is configuration, never a constant.** Mainnet USDC is
31566704 and testnet USDC is 10458941, but the `exact` scheme takes any ASA id
and the Authen build spent a while on a self-minted stand-in when the faucet
would not pay out. Hardcoding an id means opting a session into the wrong asset
and failing every payment on the rail you are actually developing against.

**Testnet is the default.** Mainnet is never disabled - Obol is meant to make a
real payment - but it is never the thing you get by forgetting to choose.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from .errors import WalletError

# The real Circle USDC asset ids. Anything else is not USDC and is not called it.
USDC_ASSET_IDS = frozenset({31566704, 10458941})

# Algorand minimum balance: 0.1 ALGO for the account, plus 0.1 per ASA held.
MIN_BALANCE_MICRO = 100_000
ASA_MIN_BALANCE_MICRO = 100_000

# What the vault sends a session at setup: 0.1 account minimum + 0.1 for the USDC
# slot + 0.01 headroom. The headroom covers the session's own fees - its opt-in at
# setup and its two teardown transactions - since the buyer pays no fee only for
# the x402 payment itself, which the facilitator sponsors.
SESSION_FUNDING_MICRO = 210_000

# What a vault needs before it can hold the payment asset at all. See DESIGN.md
# section 3.1: this must arrive before the opt-in, and the opt-in before any USDC.
VAULT_MIN_ALGO_MICRO = MIN_BALANCE_MICRO + ASA_MIN_BALANCE_MICRO + 10_000


@dataclass(frozen=True)
class NetworkProfile:
    """One chain, one asset, one algod.

    `caip2` and `slug` are both here because the facilitator wants different ones
    in different places: `/discovery/resources` takes the CAIP-2 id and silently
    returns `total: 0` for the slug, while `/data/*` takes the slug.
    """

    name: str
    caip2: str
    slug: str
    payment_asa: int
    decimals: int
    algod_url: str

    @property
    def is_mainnet(self) -> bool:
        return self.name == "mainnet"

    @property
    def asset_label(self) -> str:
        """What to call the payment asset in front of a human.

        Only the two real Circle USDC ids earn the name. A stand-in ASA set via
        config gets its number, because calling something USDC when it is not is
        the kind of small dishonesty that ends up in a support thread.
        """
        return "USDC" if self.payment_asa in USDC_ASSET_IDS else f"ASA {self.payment_asa}"


    def to_units(self, amount: float) -> int:
        return int(round(amount * 10**self.decimals))

    def fmt(self, micro: int) -> str:
        return f"{micro / 10**self.decimals:.{self.decimals}f}"


PROFILES: dict[str, NetworkProfile] = {
    "mainnet": NetworkProfile(
        name="mainnet",
        caip2="algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=",
        slug="algorand-mainnet",
        payment_asa=31566704,
        decimals=6,
        algod_url="https://mainnet-api.algonode.cloud",
    ),
    "testnet": NetworkProfile(
        name="testnet",
        caip2="algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=",
        slug="algorand-testnet",
        payment_asa=10458941,
        decimals=6,
        algod_url="https://testnet-api.algonode.cloud",
    ),
}


@dataclass(frozen=True)
class Caps:
    """In-process spend controls.

    None of these bounds a loss on their own - the session balance does that, on
    chain, because a session account cannot spend what it does not hold. These are
    defence in depth, and they protect the user's own money, which is why they
    earn their place regardless of who funded the wallet.
    """

    per_call_micro: int = 500_000          # $0.50
    daily_micro: int | None = None          # off unless the user sets it
    session_balance_micro: int = 5_000_000  # $5, the suggested default
    allowlist: tuple[str, ...] = ()         # payTo addresses; empty means off


@dataclass(frozen=True)
class Config:
    network: NetworkProfile
    data_dir: Path
    caps: Caps
    # The one directory `body_file` may read from and `output_file` may write to.
    # None - the default - disables both parameters outright. See obol/files.py:
    # moving bytes off the machine is not something a spend cap can bound, so a
    # default install does not offer the capability at all.
    file_root: Path | None = None
    # How long a session may sit unused before the server closes it and sweeps the
    # balance back. MCP gives no reliable session-end signal on any transport
    # (DESIGN.md section 9), so this plus reaping on next start is the answer. Ten
    # minutes: long enough that an agent thinking between calls does not pay to
    # reopen, short enough that a forgotten client does not hold funds overnight.
    idle_timeout_seconds: int = 600

    @property
    def ledger_path(self) -> Path:
        # Per network: a testnet session address means nothing on mainnet, and
        # reaping across the two would be an expensive kind of nonsense.
        return self.data_dir / f"ledger-{self.network.name}.json"

    @property
    def seed_path(self) -> Path:
        # Deliberately NOT per network. One Ed25519 key is one address on both,
        # and two vaults would double the bootstrap in section 3.1 for no benefit.
        return self.data_dir / "vault_seed.bin"


def default_data_dir() -> Path:
    if env := os.environ.get("OBOL_DATA_DIR"):
        return Path(env).expanduser()
    if os.name == "nt" and (local := os.environ.get("LOCALAPPDATA")):
        return Path(local) / "Obol"
    return Path.home() / ".obol"


def load_config(network: str | None = None, data_dir: Path | None = None) -> Config:
    """Resolve configuration: defaults, then `config.toml`, then env, then args.

    The TOML is optional and lives in the data dir, not the repo - it describes
    this install, not this checkout.
    """
    data_dir = data_dir or default_data_dir()
    raw: dict = {}
    cfg_file = data_dir / "config.toml"
    if cfg_file.exists():
        raw = tomllib.loads(cfg_file.read_text(encoding="utf-8"))

    name = (
        network
        or os.environ.get("OBOL_NETWORK")
        or raw.get("network")
        or "testnet"
    )
    if name not in PROFILES:
        raise WalletError(
            f"Unknown network {name!r}. Known: {', '.join(sorted(PROFILES))}."
        )
    profile = PROFILES[name]

    # A profile override exists for one honest reason: a testnet rail may run on a
    # stand-in ASA when the faucet will not cooperate.
    for field in ("payment_asa", "decimals", "algod_url", "caip2", "slug"):
        value = raw.get("networks", {}).get(name, {}).get(field)
        if value is not None:
            profile = replace(profile, **{field: value})

    caps_raw = raw.get("caps", {})
    caps = Caps(
        per_call_micro=int(caps_raw.get("per_call_micro", Caps.per_call_micro)),
        daily_micro=(
            int(caps_raw["daily_micro"]) if caps_raw.get("daily_micro") else None
        ),
        session_balance_micro=int(
            caps_raw.get("session_balance_micro", Caps.session_balance_micro)
        ),
        allowlist=tuple(caps_raw.get("allowlist", ())),
    )
    idle = int(
        raw.get("session", {}).get("idle_timeout_seconds", Config.idle_timeout_seconds)
    )
    root_raw = raw.get("files", {}).get("root")
    file_root = Path(root_raw).expanduser() if root_raw else None
    return Config(
        network=profile,
        data_dir=data_dir,
        caps=caps,
        idle_timeout_seconds=idle,
        file_root=file_root,
    )
