from __future__ import annotations

import asyncio
import random
import re
import time
from collections import deque
from dataclasses import replace
from typing import Awaitable, Callable

import httpx

from .models import AttemptResult, Provider, Settings, Target

Event = Callable[[AttemptResult], None]
INTERNAL_RESULTS = {"cooldown", "stopped"}
NEGATIVE_STATUSES = {"error", "failed", "failure", "invalid", "false", "blocked", "captcha"}
NEGATIVE_TEXT = ("error", "failed", "invalid", "captcha", "blocked", "not sent", "rate limit")


def _negative_signal(payload: object, text: str) -> str:
    if isinstance(payload, dict):
        if payload.get("success") is False:
            return "json success=false"
        error = payload.get("error")
        if error:
            return f"json error={str(error)[:80]}"
        status = str(payload.get("status", "")).lower()
        if status in NEGATIVE_STATUSES:
            return f"json status={status}"
        for key in ("message", "msg", "detail", "description"):
            value = str(payload.get(key, "")).lower()
            for token in NEGATIVE_TEXT:
                if token in value:
                    return f"json {key} contains {token}"

    lowered = text.lower()
    for token in ('"success":false', '"success": false', '"error":', "captcha", "too many requests", "invalid phone", "not sent"):
        if token in lowered:
            return f"body contains {token}"
    return ""


def _json_get(data: object, path: str) -> object:
    current = data
    for part in path.split("."):
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


class Engine:
    def __init__(
        self,
        target: Target,
        settings: Settings,
        providers: list[Provider],
        on_event: Event | None = None,
    ) -> None:
        self.target = target
        self.settings = settings
        self.providers = providers
        self.on_event = on_event
        self._sem = asyncio.Semaphore(max(1, settings.concurrency))
        self._lock = asyncio.Lock()
        self._times: deque[float] = deque()
        self._failures: dict[str, int] = {}
        self._cooldown: dict[str, float] = {}
        self._stop = False
        self._client: httpx.AsyncClient | None = None

    def stop(self) -> None:
        self._stop = True

    def _kinds(self) -> set[str]:
        if self.settings.mode == "sms":
            return {"sms", "otp"}
        if self.settings.mode == "call":
            return {"call"}
        return {"sms", "otp", "call"}

    def _cooling(self, name: str) -> bool:
        return time.monotonic() < self._cooldown.get(name, 0.0)

    async def _rate_limit(self) -> None:
        limit = max(1, int(self.settings.rps))
        wait = 0.0
        async with self._lock:
            now = time.monotonic()
            while self._times and self._times[0] <= now - 1.0:
                self._times.popleft()
            if len(self._times) >= limit:
                wait = max(0.0, self._times[0] + 1.0 - now)
                self._times.append(self._times[0] + 1.0)
            else:
                self._times.append(now)
        if wait:
            await asyncio.sleep(wait)

    def _evaluate(self, response: httpx.Response, provider: Provider) -> tuple[bool, str]:
        config = provider.success
        statuses = config.get("status")
        if statuses:
            allowed = {int(status) for status in statuses}
            if response.status_code not in allowed:
                snippet = (response.text or "").strip()[:140]
                return False, f"HTTP {response.status_code} {snippet}".strip()

        payload: object = None
        try:
            payload = response.json()
        except ValueError:
            pass

        negative = _negative_signal(payload, response.text or "")
        if negative:
            return False, negative[:240]

        path = config.get("json_path")
        exists = config.get("exists")
        equality = config.get("equals")
        if path or exists:
            lookup = path or exists
            value = _json_get(payload, lookup) if payload is not None else None
            if equality is not None:
                if value != equality:
                    return False, f"json {lookup}={value!r}"
            elif value is None:
                return False, f"json {lookup} missing"

        pattern = config.get("regex")
        if pattern and not re.search(str(pattern), response.text or "", re.IGNORECASE):
            return False, "regex miss"

        detail = f"HTTP {response.status_code}"
        if path and payload is not None:
            detail += f" {path}={_json_get(payload, path)!r}"
        return True, detail[:240]

    async def _send(self, provider: Provider) -> AttemptResult:
        started = time.perf_counter()
        headers: dict[str, str] = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Pacification/1.0",
            "Accept": "application/json, text/plain, */*",
        }
        headers.update({str(k): str(v) for k, v in provider.headers.items()})

        auth: httpx.BasicAuth | None = None
        auth_config = provider.auth
        auth_type = str(auth_config.get("type", "")).lower()
        if auth_type == "basic":
            auth = httpx.BasicAuth(
                str(auth_config.get("username", "")),
                str(auth_config.get("password", "")),
            )
        elif auth_type == "bearer":
            headers["Authorization"] = "Bearer " + str(auth_config.get("token", ""))

        kwargs: dict[str, object] = {}
        if provider.body is not None:
            if provider.body_type == "form":
                kwargs["data"] = provider.body
            elif provider.body_type == "raw":
                kwargs["content"] = str(provider.body).encode("utf-8")
            else:
                kwargs["json"] = provider.body

        assert self._client is not None
        response = await self._client.request(
            provider.method,
            provider.url,
            params=provider.query or None,
            headers=headers,
            auth=auth,
            timeout=provider.timeout,
            **kwargs,
        )
        ok, detail = self._evaluate(response, provider)
        return AttemptResult(
            provider=provider.name,
            kind=provider.kind,
            ok=ok,
            status=response.status_code,
            detail=detail,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    def _register_health(self, result: AttemptResult) -> None:
        if result.detail in INTERNAL_RESULTS or result.dry_run:
            return
        if result.ok:
            self._failures[result.provider] = 0
            return
        failures = self._failures.get(result.provider, 0) + 1
        self._failures[result.provider] = failures
        if failures >= max(1, self.settings.max_failures):
            self._cooldown[result.provider] = time.monotonic() + self.settings.cooldown

    async def _dispatch(self, round_no: int, provider: Provider) -> AttemptResult:
        await self._sem.acquire()
        result: AttemptResult | None = None
        try:
            if self._stop:
                result = AttemptResult(provider.name, provider.kind, False, None, "stopped", 0)
            elif self._cooling(provider.name):
                result = AttemptResult(provider.name, provider.kind, False, None, "cooldown", 0)
            elif self.settings.dry_run:
                result = AttemptResult(
                    provider.name,
                    provider.kind,
                    True,
                    None,
                    f"dry-run round {round_no}",
                    0,
                    dry_run=True,
                )
            else:
                await self._rate_limit()
                result = await self._send(provider)
        except Exception as exc:  # network and HTTP failures are results, not crashes
            result = AttemptResult(
                provider.name,
                provider.kind,
                False,
                None,
                f"{type(exc).__name__}: {exc}"[:240],
                0,
            )
        finally:
            self._sem.release()

        self._register_health(result)
        if self.on_event is not None:
            self.on_event(result)
        return result

    async def run(self) -> list[AttemptResult]:
        kinds = self._kinds()
        jobs: list[tuple[int, Provider]] = []
        for round_no in range(1, max(1, self.settings.rounds) + 1):
            batch = [provider for provider in self.providers if provider.kind in kinds]
            random.shuffle(batch)
            jobs.extend((round_no, provider) for provider in batch)

        timeout = httpx.Timeout(max(5.0, self.settings.timeout))
        limits = httpx.Limits(max_connections=max(1, self.settings.concurrency) * 2)
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=timeout, limits=limits
        ) as client:
            self._client = client
            tasks: list[Awaitable[AttemptResult]] = []
            for round_no, provider in jobs:
                if self._stop:
                    break
                tasks.append(asyncio.create_task(self._dispatch(round_no, provider)))
                if self.settings.delay > 0:
                    await asyncio.sleep(self.settings.delay)

            gathered = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[AttemptResult] = []
        for item in gathered:
            if isinstance(item, BaseException):
                results.append(AttemptResult("engine", "-", False, None, str(item)[:240], 0))
            else:
                results.append(replace(item))
        return results
