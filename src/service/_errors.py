"""Private: the one error shape this lane puts on the wire.

`contracts/src/service.md`: "Errors are JSON `{error, detail, how_to_fix}` with the
real HTTP status -- never a 200 with an empty body." Every failure path in this lane
raises a `ServiceError` subclass so the status code and the three keys are decided in
one place rather than per route.
"""

from __future__ import annotations


class ServiceError(Exception):
    """Base: carries the wire shape and the HTTP status together."""

    status_code: int = 500
    error: str = "internal_error"

    def __init__(self, detail: str, how_to_fix: str) -> None:
        super().__init__(detail)
        self.detail = detail
        self.how_to_fix = how_to_fix

    def to_dict(self) -> dict:
        return {"error": self.error, "detail": self.detail, "how_to_fix": self.how_to_fix}


class BadSpec(ServiceError):
    """The caller's ScenarioSpec (or query/body) cannot be honoured as written."""

    status_code = 400
    error = "bad_spec"


class ScenarioNotFound(ServiceError):
    """A scenario id that has never been run (or whose cache entry is gone)."""

    status_code = 404
    error = "scenario_not_found"


class UpstreamUnavailable(ServiceError):
    """A lane this one composes has not implemented a function the contract names.

    Deliberately NOT swallowed into a plausible-looking zero: a pipeline stage that
    does not exist is a fact the operator needs (contracts/CONVENTIONS.md), so it is
    either a 503 (the stage is load-bearing) or a `warnings[]` entry plus a null
    (the stage only feeds one figure).
    """

    status_code = 503
    error = "upstream_unavailable"


class MissingData(ServiceError):
    """A canonical table this scenario needs has not been built, or does not cover it."""

    status_code = 503
    error = "missing_data"


class ScenarioFailed(ServiceError):
    """The pipeline ran and refused to produce a result (e.g. an infeasible instance)."""

    status_code = 500
    error = "scenario_failed"
