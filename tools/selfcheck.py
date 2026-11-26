from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any

from pacification.config import build_context, parse_target
from pacification.engine import Engine
from pacification.models import Settings
from pacification.providers import available, load_specs, set_enabled

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

KEEP_ENABLED = {"textbelt"}


async def probe(specs: list[dict[str, Any]], region: str, raw: str) -> set[str]:
    target = parse_target(raw, region)
    providers, _ = available(specs, build_context(target), target.country)
    results = await Engine(target, SETTINGS, providers).run()
    alive = sorted(item.provider for item in results if item.ok)
    dead = sorted(item.provider for item in results if not item.ok)
    print(f"{target.country}: ready={len(providers)} alive={len(alive)} dead={len(dead)}")
    print("ALIVE:", ", ".join(alive))
    print("DEAD:", ", ".join(dead))
    return set(dead) - KEEP_ENABLED


async def main() -> None:
    parser = argparse.ArgumentParser(description="Live provider registry self-check")
    parser.add_argument(
        "--prune",
        action="store_true",
        help="disable providers that failed this check, excluding quota-limited textbelt",
    )
    args = parser.parse_args()

    registry = os.environ.get("PACIFICATION_PROVIDERS")
    specs = load_specs(registry)
    print(f"registry: {len(specs)}")

    dead: set[str] = set()
    for region, raw in CASES:
        dead.update(await probe(specs, region, raw))

    if not args.prune:
        return
    if not dead:
        print("PRUNE: nothing to disable")
        return

    source, backup = set_enabled(dead, False, registry)
    print(f"PRUNED: {len(dead)} -> {source}")
    print(f"BACKUP: {backup}")


if __name__ == "__main__":
    asyncio.run(main())
