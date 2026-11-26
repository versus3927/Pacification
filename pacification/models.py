from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping


@dataclass(frozen=True)
class Provider:
    name: str
    kind: str
    method: str
    url: str
    enabled: bool = True
    countries: tuple[str, ...] = ()
    headers: Mapping[str, Any] = field(default_factory=dict)
    query: Mapping[str, Any] = field(default_factory=dict)
    body: Any = None
    body_type: str = "json"
    auth: Mapping[str, Any] = field(default_factory=dict)
    timeout: float = 10.0
    success: Mapping[str, Any] = field(
        default_factory=lambda: {"status": [200, 201, 202, 204]}
    )
    note: str = ""


@dataclass(frozen=True)
class Target:
    e164: str
    country: str
    cc: str
    digits: str
    national: str
    local: str
    local0: str
    subscriber: str
    cc_local: str


@dataclass(frozen=True)
class Settings:
    rounds: int = 1
    delay: float = 1.5
    concurrency: int = 6
    rps: float = 2.0
    mode: str = "sms"
    timeout: float = 10.0
    dry_run: bool = False
    cooldown: float = 45.0
    max_failures: int = 3

    def with_changes(self, **changes: Any) -> "Settings":
        return replace(self, **changes)


@dataclass(frozen=True)
class AttemptResult:
    provider: str
    kind: str
    ok: bool
    status: int | None
    detail: str
    elapsed_ms: int
    dry_run: bool = False
