from __future__ import annotations

import json
import os
from importlib import resources
from pathlib import Path
from typing import Any

from .config import MissingCredential, render
from .models import Provider


def default_path() -> Path:
    return Path(str(resources.files("pacification").joinpath("providers.json")))


def load_specs(path: str | Path | None = None) -> list[dict[str, Any]]:
    source = Path(path) if path else default_path()
    if not source.is_file():
        raise FileNotFoundError(f"provider registry not found: {source}")
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    specs = data["providers"] if isinstance(data, dict) else data
    if not isinstance(specs, list):
        raise ValueError("provider registry must be a list or an object with 'providers'")

    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(specs):
        if not isinstance(raw, dict):
            raise ValueError(f"provider #{index} is not an object")
        name = str(raw.get("name", "")).strip()
        url = str(raw.get("url", "")).strip()
        if not name or not url:
            raise ValueError(f"provider #{index} needs both 'name' and 'url'")
        item = dict(raw)
        item["name"] = name
        item["url"] = url
        item["method"] = str(raw.get("method", "GET")).upper()
        item["kind"] = str(raw.get("kind", "sms")).lower()
        item["enabled"] = bool(raw.get("enabled", True))
        normalized.append(item)
    return normalized


def prepare(spec: dict[str, Any], ctx: dict[str, str], country: str) -> tuple[Provider | None, str]:
    if not spec["enabled"]:
        return None, "disabled in registry"

    countries = spec.get("countries") or ()
    if isinstance(countries, str):
        countries = (countries,)
    countries = tuple(str(c).upper() for c in countries)
    if countries and country.upper() not in countries:
        return None, f"region {country}"

    try:
        ready = render(spec, ctx)
    except MissingCredential as exc:
        return None, f"no {exc.name}"

    success = ready.get("success") or {"status": [200, 201, 202, 204]}
    provider = Provider(
        name=str(ready["name"]),
        kind=str(ready["kind"]),
        method=str(ready["method"]),
        url=str(ready["url"]),
        enabled=True,
        countries=countries,
        headers=ready.get("headers") or {},
        query=ready.get("query") or {},
        body=ready.get("body"),
        body_type=str(ready.get("body_type", "json")).lower(),
        auth=ready.get("auth") or {},
        timeout=float(ready.get("timeout", 10.0)),
        success=success,
        note=str(ready.get("note", "")),
    )
    return provider, ""


def available(
    specs: list[dict[str, Any]], ctx: dict[str, str], country: str
) -> tuple[list[Provider], list[tuple[str, str]]]:
    ready: list[Provider] = []
    skipped: list[tuple[str, str]] = []
    for spec in specs:
        provider, reason = prepare(spec, ctx, country)
        if provider is None:
            skipped.append((str(spec["name"]), reason))
        else:
            ready.append(provider)
    return ready, skipped
