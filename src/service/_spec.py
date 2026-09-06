"""Private: `ScenarioSpec` -- the thing that fully determines a scenario, and its hash.

`contracts/src/service.md`: "Fully determines the result -- the cache key is its hash,
stored under `data/derived/service/<hash>.json`." So the hash covers every field, and
adding a field to the spec necessarily changes every id (that is the point: a spec that
does not fully determine the result would serve a stale answer).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from datetime import date

from src.forecast import api as forecast
from src.market import api as market

from ._errors import BadSpec

POLICIES = ("baseline", "optimised")
POOL_METHODS = ("sum", "empirical", "gaussian_copula")

_SELECTORS = ("site_ids", "region", "n_sites")


@dataclass(frozen=True)
class ScenarioSpec:
    """`date`, one of `site_ids | region | n_sites`, `seed`, `product`, `tau`, `policy`,
    `pool_method` -- exactly the fields named in `contracts/src/service.md`.

    `date` is an ISO date interpreted as a **Europe/Berlin local day** (CONVENTIONS.md:
    anything shown to a human is Berlin time), converted to a UTC interval-start grid
    for every computation. That makes the 25-hour DST day a longer grid rather than a
    crash.
    """

    date: str
    site_ids: tuple[str, ...] | None = None
    region: str | None = None
    n_sites: int | None = None
    seed: int = 0
    product: str = "aFRR"
    tau: float = 0.05
    policy: str = "optimised"
    pool_method: str = "empirical"

    def __post_init__(self) -> None:
        self._validate()

    # -- validation ---------------------------------------------------------

    def _validate(self) -> None:
        try:
            date.fromisoformat(self.date)
        except (TypeError, ValueError):
            raise BadSpec(
                f"date={self.date!r} is not an ISO calendar date",
                "pass date as 'YYYY-MM-DD', e.g. '2026-03-04'",
            ) from None

        given = [name for name in _SELECTORS if getattr(self, name) is not None]
        if len(given) != 1:
            raise BadSpec(
                f"exactly one portfolio selector must be given, got {given or 'none'}",
                "pass exactly one of site_ids, region or n_sites -- two selectors would "
                "not determine the portfolio, and none would run the national table",
            )

        if self.site_ids is not None:
            if not isinstance(self.site_ids, tuple) or not self.site_ids:
                raise BadSpec(
                    "site_ids must be a non-empty list of site_id strings",
                    "pass e.g. site_ids=['BY-80331-ab12cd34'] or use n_sites instead",
                )
            if any(not isinstance(s, str) or not s for s in self.site_ids):
                raise BadSpec(
                    "site_ids must contain only non-empty strings",
                    "site_id is the canonical key from the `sites` table "
                    "(contracts/src/data.md)",
                )
        if self.region is not None and (not isinstance(self.region, str) or not self.region):
            raise BadSpec(
                "region must be a non-empty string",
                "region matches the sites table's `state` exactly, or is a postcode prefix",
            )
        if self.n_sites is not None:
            if isinstance(self.n_sites, bool) or not isinstance(self.n_sites, int):
                raise BadSpec("n_sites must be an integer", "pass e.g. n_sites=3")
            if self.n_sites < 1:
                raise BadSpec(
                    f"n_sites={self.n_sites} is not a portfolio",
                    "pass n_sites >= 1",
                )

        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise BadSpec(
                f"seed={self.seed!r} must be an integer",
                "every sampling function in this project takes an int seed "
                "(contracts/CONVENTIONS.md, Determinism)",
            )

        if self.product not in market.PRODUCTS:
            raise BadSpec(
                f"unknown product {self.product!r}",
                f"known products: {sorted(market.PRODUCTS)} (src/market.PRODUCTS)",
            )

        if not isinstance(self.tau, (int, float)) or isinstance(self.tau, bool):
            raise BadSpec(f"tau={self.tau!r} must be a number", "pass e.g. tau=0.05")
        if float(self.tau) not in [float(q) for q in forecast.QUANTILES]:
            raise BadSpec(
                f"tau={self.tau} has no fitted quantile behind it",
                f"pass one of {list(forecast.QUANTILES)} -- src/forecast fits exactly these "
                "quantiles, and sizing firm capacity off an interpolated one would be an "
                "invented number",
            )

        if self.policy not in POLICIES:
            raise BadSpec(
                f"unknown policy {self.policy!r}", f"policy must be one of {list(POLICIES)}"
            )
        if self.pool_method not in POOL_METHODS:
            raise BadSpec(
                f"unknown pool_method {self.pool_method!r}",
                f"pool_method must be one of {list(POOL_METHODS)} (contracts/src/market.md)",
            )

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict:
        """Plain-JSON form; `site_ids` becomes a list so the wire and the hash agree."""
        out = asdict(self)
        out["site_ids"] = list(self.site_ids) if self.site_ids is not None else None
        out["tau"] = float(self.tau)
        return out

    @classmethod
    def from_dict(cls, payload: object) -> "ScenarioSpec":
        """Build from a request body, rejecting unknown keys rather than ignoring them.

        An ignored key would silently not take part in the cache hash, so two different
        requests would share one answer -- the exact failure the hash exists to prevent.
        """
        if not isinstance(payload, dict):
            raise BadSpec(
                f"expected a JSON object for the scenario spec, got {type(payload).__name__}",
                "POST a JSON object, e.g. {\"date\": \"2026-03-04\", \"n_sites\": 3}",
            )
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(payload) - known)
        if unknown:
            raise BadSpec(
                f"unknown spec field(s): {unknown}",
                f"the spec is exactly {sorted(known)} (contracts/src/service.md); an "
                "ignored field would not enter the cache key and two different requests "
                "would silently share one answer",
            )
        kwargs = dict(payload)
        if kwargs.get("site_ids") is not None:
            value = kwargs["site_ids"]
            if isinstance(value, str) or not isinstance(value, (list, tuple)):
                raise BadSpec(
                    "site_ids must be a list of site_id strings",
                    "pass site_ids as a JSON array, e.g. [\"BY-80331-ab12cd34\"]",
                )
            kwargs["site_ids"] = tuple(value)
        try:
            return cls(**kwargs)
        except TypeError as exc:  # pragma: no cover - guarded by the unknown-key check
            raise BadSpec(str(exc), "see contracts/src/service.md for the spec fields") from exc

    @property
    def id(self) -> str:
        """sha256 of the canonical spec JSON, truncated -- the cache key on disk."""
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
