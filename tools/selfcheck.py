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


async def probe(specs: list[dict[str, Any]], region: str, raw: str) -> tuple[set[str], set[str]]:
    target = parse_target(raw, region)
    providers, _ = available(specs, build_context(target), target.country)
    results = await Engine(target, SETTINGS, providers).run()
    alive = sorted(item.provider for item in results if item.ok)
    dead = sorted(item.provider for item in results if not item.ok)
    print(f"{target.country}: ready={len(providers)} alive={len(alive)} dead={len(dead)}")
    print("ALIVE:", ", ".join(alive))
    print("DEAD:", ", ".join(dead))
    return set(alive), set(dead) - KEEP_ENABLED


async def sweep(specs: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    alive_all: set[str] = set()
    dead_all: set[str] = set()
    for region, raw in CASES:
        alive, dead = await probe(specs, region, raw)
        alive_all.update(alive)
        dead_all.update(dead)
    return alive_all, dead_all


async def main() -> None:
    parser = argparse.ArgumentParser(description="Live provider registry self-check")
    parser.add_argument(
        "--prune",
        action="store_true",
        help="disable providers that failed this check, excluding quota-limited textbelt",
    )
    parser.add_argument(
        "--until-success",
        action="store_true",
        help="repeat live checks until at least one provider confirms a send",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=50,
        help="cycle limit for --until-success (default: 50)",
    )
    args = parser.parse_args()

    registry = os.environ.get("PACIFICATION_PROVIDERS")
    specs = load_specs(registry)
    print(f"registry: {len(specs)}")

    dead_total: set[str] = set()
    if not args.until_success:
        _, dead_total = await sweep(specs)
        if args.prune and dead_total:
            source, backup = set_enabled(dead_total, False, registry)
            print(f"PRUNED: {len(dead_total)} -> {source}")
            print(f"BACKUP: {backup}")
        elif args.prune:
            print("PRUNE: nothing to disable")
        return

    alive_total: set[str] = set()
    for cycle in range(1, max(1, args.max_cycles) + 1):
        alive, dead = await sweep(specs)
        alive_total.update(alive)
        dead_total.update(dead)
        if alive:
            print(f"NOTIFICATION: {', '.join(sorted(alive))}")
            break
        print(f"cycle {cycle}: no notification")
        if cycle < max(1, args.max_cycles):
            await asyncio.sleep(1.0)

    if args.prune and dead_total:
        source, backup = set_enabled(dead_total, False, registry)
        print(f"PRUNED: {len(dead_total)} -> {source}")
        print(f"BACKUP: {backup}")

    if not alive_total:
        print(f"STOP: no notification after {max(1, args.max_cycles)} cycles")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
