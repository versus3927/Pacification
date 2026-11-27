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


async def probe(
    specs: list[dict[str, Any]], region: str, raw: str, target_count: int = 1
) -> tuple[list[str], set[str]]:
    target = parse_target(raw, region)
    providers, _ = available(specs, build_context(target), target.country)
    settings = SETTINGS.with_changes(target_count=target_count)
    results = await Engine(target, settings, providers).run()
    alive = [item.provider for item in results if item.ok and not item.dry_run]
    dead = sorted(
        item.provider
        for item in results
        if not item.ok and item.detail not in {"cooldown", "stopped"}
    )
    print(
        f"{target.country}: ready={len(providers)} confirmed={len(alive)} dead={len(dead)}"
    )
    print("ALIVE:", ", ".join(alive))
    print("DEAD:", ", ".join(dead))
    return alive, set(dead) - KEEP_ENABLED


async def sweep(
    specs: list[dict[str, Any]], cases: tuple[tuple[str, str], ...], target_count: int = 1
) -> tuple[list[str], set[str]]:
    alive_all: list[str] = []
    dead_all: set[str] = set()
    for region, raw in cases:
        alive, dead = await probe(specs, region, raw, target_count=target_count)
        alive_all.extend(alive)
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
        help="repeat live checks until target confirmations are reached",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=50,
        help="cycle limit for --until-success (default: 50)",
    )
    parser.add_argument(
        "--target",
        type=int,
        default=1,
        help="number of API-confirmed sends to reach (default: 1)",
    )
    parser.add_argument(
        "--phone",
        default="",
        help="probe a specific phone number instead of built-in test cases",
    )
    parser.add_argument(
        "--region",
        default="RU",
        help="default region for --phone (default: RU)",
    )
    args = parser.parse_args()

    registry = os.environ.get("PACIFICATION_PROVIDERS")
    specs = load_specs(registry)
    print(f"registry: {len(specs)}")

    cases = CASES
    if args.phone:
        cases = ((args.region, args.phone),)

    target_count = max(1, args.target)
    if args.until_success and target_count == 1:
        # keep old behavior explicit
        target_count = max(1, args.target)

    dead_total: set[str] = set()
    if not args.until_success:
        alive, dead_total = await sweep(specs, cases, target_count=target_count)
        if args.prune and dead_total:
            source, backup = set_enabled(dead_total, False, registry)
            print(f"PRUNED: {len(dead_total)} -> {source}")
            print(f"BACKUP: {backup}")
        elif args.prune:
            print("PRUNE: nothing to disable")
        if target_count > 1 and len(alive) < target_count:
            print(f"STOP: confirmed {len(alive)}/{target_count}")
            raise SystemExit(1)
        return

    confirmed_total = 0
    confirmed_names: list[str] = []
    for cycle in range(1, max(1, args.max_cycles) + 1):
        remaining = target_count - confirmed_total
        alive, dead = await sweep(specs, cases, target_count=remaining)
        confirmed_total += len(alive)
        confirmed_names.extend(alive)
        dead_total.update(dead)
        if confirmed_total >= target_count:
            print(f"NOTIFICATION: {', '.join(confirmed_names[:20])}")
            print(f"CONFIRMED: {confirmed_total}/{target_count}")
            break
        print(
            f"cycle {cycle}: confirmed {confirmed_total}/{target_count}, need {target_count - confirmed_total}"
        )
        if cycle < max(1, args.max_cycles):
            await asyncio.sleep(1.0)

    if args.prune and dead_total:
        source, backup = set_enabled(dead_total, False, registry)
        print(f"PRUNED: {len(dead_total)} -> {source}")
        print(f"BACKUP: {backup}")

    if confirmed_total < target_count:
        print(f"STOP: confirmed {confirmed_total}/{target_count} after {max(1, args.max_cycles)} cycles")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
