from __future__ import annotations

import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Mapping

import phonenumbers
from phonenumbers import PhoneNumberFormat

from .models import Target

_ENV_RE = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)(?:\|([^{}]*))?\}")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class MissingCredential(RuntimeError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"missing credential: {name}")


def load_dotenv(path: Path | None = None) -> int:
    """Load KEY=VALUE pairs without overriding the real environment."""
    candidates = (
        [path]
        if path is not None
        else [Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"]
    )
    loaded = 0
    for dotenv in candidates:
        if not dotenv.is_file():
            continue
        for raw in dotenv.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key, value)
            loaded += 1
    return loaded


def render(value: Any, ctx: Mapping[str, str]) -> Any:
    """Expand {env:NAME|default} and number tokens in nested JSON structures."""
    if isinstance(value, str):

        def env_repl(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            resolved = os.environ.get(name)
            if resolved is None or resolved == "":
                if default is None:
                    raise MissingCredential(name)
                resolved = default
            return resolved

        out = _ENV_RE.sub(env_repl, value)
        for key, resolved in ctx.items():
            out = out.replace("{" + key + "}", str(resolved))
        return out

    if isinstance(value, dict):
        return {render(k, ctx): render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


def parse_target(raw: str, default_region: str = "RU") -> Target:
    text = raw.strip()
    if not text:
        raise ValueError("empty number")
    if text.startswith("00"):
        text = "+" + text[2:]

    parsed = phonenumbers.parse(text, default_region)
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("invalid phone number")

    e164 = phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
    national_pretty = phonenumbers.format_number(parsed, PhoneNumberFormat.NATIONAL)
    national = re.sub(r"\D", "", national_pretty)
    subscriber = national[1:] if national.startswith("0") else national
    local0 = national if national.startswith("0") else "0" + national
    cc = str(parsed.country_code)
    digits = e164[1:]
    country = phonenumbers.region_code_for_number(parsed) or default_region

    return Target(
        e164=e164,
        country=country,
        cc=cc,
        digits=digits,
        national=national,
        local=subscriber,
        local0=local0,
        subscriber=subscriber,
        cc_local=cc + subscriber,
    )


def build_context(target: Target, code: str | None = None) -> dict[str, str]:
    otp = code or str(secrets.randbelow(900_000) + 100_000)
    return {
        "phone": target.e164,
        "e164": target.e164,
        "intl": target.e164,
        "digits": target.digits,
        "cc": target.cc,
        "country": target.country,
        "national": target.national,
        "local": target.local,
        "local0": target.local0,
        "subscriber": target.subscriber,
        "cc_local": target.cc_local,
        "code": otp,
        "ts": str(int(time.time() * 1000)),
        "ua": UA,
    }
