from __future__ import annotations

import asyncio
from typing import Any

from pacification.config import build_context, parse_target
from pacification.engine import Engine
from pacification.models import Settings
from pacification.providers import available, load_specs

CASES = (
    ("RU", "+7 999 123-45-67"),
    ("IN", "+91 98765 43210"),
    ("IR", "+98 912 345 6789"),
)

SETTINGS = Settings(
    delay=0.0,
    rps=5.0,
    concurrency=8,
    rounds=1,
    timeout=8.0,
    cooldown=0.0,
    max_failures=1,
)


async def probe(specs: list[dict[str, Any]], region: str, raw: str) -> None:
    target = parse_target(raw, region)
    providers, _ = available(specs, build_context(target), target.country)
    results = await Engine(target, SETTINGS, providers).run()
    alive = sorted(item.provider for item in results if item.ok)
    dead = sorted(item.provider for item in results if not item.ok)
    print(f"{target.country}: ready={len(providers)} alive={len(alive)} dead={len(dead)}")
    print("ALIVE:", ", ".join(alive))
    print("DEAD:", ", ".join(dead))


async def main() -> None:
    specs = load_specs()
    print(f"registry: {len(specs)}")
    for region, raw in CASES:
        await probe(specs, region, raw)


if __name__ == "__main__":
    asyncio.run(main())
