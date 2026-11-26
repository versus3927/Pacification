from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt
from rich.table import Table

from . import __version__
from .config import build_context, load_dotenv, parse_target
from .engine import Engine
from .logger import RunLog
from .models import AttemptResult, Settings
from .providers import available, default_path, load_specs, set_enabled

console = Console()

MENU = """
[bold cyan]PACIFICATION[/bold cyan] [dim]v{version}[/dim]  --  {mode}

  [bold green]1[/bold green]  ЗАПУСК   -- номер -> подтверждение -> отправка
  [yellow]2[/yellow]  Настройки -- раунды, задержка, потоки, режим
  [yellow]3[/yellow]  Провайдеры -- реестр и причины пропуска
  [yellow]4[/yellow]  Сухой прогон -- план без сети
  [yellow]5[/yellow]  Фильтр живых -- отключить мертвые эндпоинты
  [yellow]6[/yellow]  Восстановить -- включить все провайдеры

  [dim]0[/dim]  Выход
"""


def _registry_path() -> str | Path | None:
    return os.environ.get("PACIFICATION_PROVIDERS")


def show_registry() -> None:
    try:
        specs = load_specs(_registry_path())
    except (OSError, ValueError) as exc:
        console.print(f"[red]реестр не прочитан:[/red] {exc}")
        return

    table = Table(title="Провайдеры Pacification", expand=True)
    table.add_column("Имя", style="cyan", no_wrap=True)
    table.add_column("Тип", style="magenta")
    table.add_column("Метод", style="dim")
    table.add_column("Регион", style="yellow")
    table.add_column("Статус", style="green")
    for spec in specs:
        countries = spec.get("countries") or ()
        if isinstance(countries, str):
            countries = (countries,)
        status = "on" if spec.get("enabled", True) else "off"
        table.add_row(
            spec["name"],
            spec.get("kind", "sms"),
            spec.get("method", "GET"),
            ",".join(countries) or "ANY",
            status,
        )
    console.print(table)
    console.print(f"[dim]реестр: {_registry_path() or default_path()}[/dim]")


def settings_menu(settings: Settings) -> Settings:
    while True:
        console.print(
            Panel(
                f"раунды [cyan]{settings.rounds}[/cyan]  | "
                f"задержка [cyan]{settings.delay}s[/cyan]  | "
                f"потоки [cyan]{settings.concurrency}[/cyan]  | "
                f"rps [cyan]{settings.rps}[/cyan]\n"
                f"режим [cyan]{settings.mode}[/cyan]  | "
                f"таймаут [cyan]{settings.timeout}s[/cyan]  | "
                f"cooldown [cyan]{settings.cooldown}s[/cyan]",
                title="Настройки",
                border_style="yellow",
            )
        )
        choice = Prompt.ask(
            "1-5 изменить, 0 назад",
            choices=["0", "1", "2", "3", "4", "5"],
            default="0",
        )
        if choice == "0":
            return settings
        if choice == "1":
            value = IntPrompt.ask("Раунды (1-100)", default=settings.rounds)
            settings = settings.with_changes(rounds=max(1, min(100, value)))
        elif choice == "2":
            value = FloatPrompt.ask("Задержка между запусками, сек", default=settings.delay)
            settings = settings.with_changes(delay=max(0.0, min(60.0, value)))
        elif choice == "3":
            value = IntPrompt.ask("Одновременных запросов (1-64)", default=settings.concurrency)
            settings = settings.with_changes(concurrency=max(1, min(64, value)))
        elif choice == "4":
            value = FloatPrompt.ask("Запросов в секунду (0.1-20)", default=settings.rps)
            settings = settings.with_changes(rps=max(0.1, min(20.0, value)))
        elif choice == "5":
            settings = settings.with_changes(
                mode=Prompt.ask(
                    "Режим",
                    choices=["sms", "call", "both"],
                    default=settings.mode,
                )
            )


def show_summary(results: list[AttemptResult], elapsed_s: float) -> None:
    ok = sum(1 for item in results if item.ok and not item.dry_run)
    dry = sum(1 for item in results if item.dry_run)
    skipped = sum(1 for item in results if item.detail in {"cooldown", "stopped"})
    failed = len(results) - ok - dry - skipped

    table = Table(title="Итог", expand=True)
    table.add_column("Метрика", style="cyan")
    table.add_column("Значение", justify="right")
    table.add_row("Отправлено", str(ok))
    table.add_row("Сухой прогон", str(dry))
    table.add_row("Ошибок", str(failed))
    table.add_row("Пропущено", str(skipped))
    table.add_row("Время", f"{elapsed_s:.1f}s")
    console.print(table)

    failures = [item for item in results if not item.ok and item.detail not in {"cooldown", "stopped"}]
    if failures:
        detail_table = Table(title="Последние ошибки", expand=True)
        detail_table.add_column("Провайдер", style="red", no_wrap=True)
        detail_table.add_column("Деталь")
        for item in failures[-12:]:
            detail_table.add_row(item.provider, item.detail)
        console.print(detail_table)


def maybe_prune(results: list[AttemptResult], dry: bool) -> None:
    if dry or not results:
        return
    dead = sorted(
        {
            item.provider
            for item in results
            if not item.ok
            and item.detail not in {"cooldown", "stopped"}
            and item.provider != "textbelt"
        }
    )
    if not dead:
        return
    if not Confirm.ask(f"Отключить {len(dead)} провайдеров с ошибками в реестре?", default=True):
        console.print("[dim]реестр не тронут.[/dim]")
        return
    try:
        source, backup = set_enabled(set(dead), False, _registry_path())
    except (OSError, ValueError) as exc:
        console.print(f"[red]реестр не обновлен:[/red] {exc}")
        return
    console.print(f"[green]реестр обновлен:[/green] {source}")
    console.print(f"[dim]бэкап: {backup}[/dim]")


def run_campaign(settings: Settings, dry: bool = False) -> None:
    raw = Prompt.ask("Номер телефона", default="")
    try:
        target = parse_target(raw)
    except Exception as exc:
        console.print(f"[red]номер не разобран:[/red] {exc}")
        return

    context = build_context(target)
    try:
        specs = load_specs(_registry_path())
    except (OSError, ValueError) as exc:
        console.print(f"[red]реестр не прочитан:[/red] {exc}")
        return

    providers, skipped = available(specs, context, target.country)
    console.print(f"[cyan]нормализовано:[/cyan] {target.e164} ({target.country})")
    console.print(f"[green]готово:[/green] {len(providers)} провайдеров | [dim]пропущено {len(skipped)}[/dim]")
    if skipped:
        preview = ", ".join(f"{name} ({reason})" for name, reason in skipped[:8])
        console.print(f"[dim]{preview}[/dim]")
    if not providers:
        console.print("[yellow]нет доступных провайдеров. Проверь .env и режим.[/yellow]")
        return

    active = settings.with_changes(dry_run=dry or settings.dry_run)
    kind = "СУХОЙ ПРОГОН" if active.dry_run else "ОТПРАВКА"
    if not Confirm.ask(
        f"{kind}: {target.e164}, {len(providers)} провайдеров, "
        f"{active.rounds} раунд(ов), режим {active.mode}. Подтвердить?",
        default=False,
    ):
        console.print("[dim]отменено.[/dim]")
        return

    log = RunLog()
    engine = Engine(target, active, providers)
    wall = time.perf_counter()
    try:
        results = asyncio.run(engine.run())
    except KeyboardInterrupt:
        engine.stop()
        results = []
        console.print("[yellow]прервано.[/yellow]")
    elapsed = time.perf_counter() - wall
    for result in results:
        log.write(result)
    show_summary(results, elapsed)
    maybe_prune(results, active.dry_run)
    console.print(f"[dim]лог: {log.path}[/dim]")


def run_health_filter(settings: Settings) -> None:
    raw = Prompt.ask("Номер для проверки", default="")
    try:
        target = parse_target(raw)
    except Exception as exc:
        console.print(f"[red]номер не разобран:[/red] {exc}")
        return

    context = build_context(target)
    try:
        specs = load_specs(_registry_path())
    except (OSError, ValueError) as exc:
        console.print(f"[red]реестр не прочитан:[/red] {exc}")
        return

    providers, _ = available(specs, context, target.country)
    if not providers:
        console.print("[yellow]проверять нечего. Проверь .env и режим.[/yellow]")
        return

    probe = settings.with_changes(
        rounds=1,
        delay=0.0,
        dry_run=False,
        cooldown=0.0,
        max_failures=1,
    )
    if not Confirm.ask(
        f"Проверить {len(providers)} провайдеров на {target.e164}? Будут реальные запросы.",
        default=False,
    ):
        console.print("[dim]отменено.[/dim]")
        return

    log = RunLog("pacification-health.log")
    engine = Engine(target, probe, providers)
    wall = time.perf_counter()
    try:
        results = asyncio.run(engine.run())
    except KeyboardInterrupt:
        engine.stop()
        results = []
        console.print("[yellow]прервано.[/yellow]")
    elapsed = time.perf_counter() - wall
    for result in results:
        log.write(result)

    alive = sorted({item.provider for item in results if item.ok})
    dead = sorted(
        {
            item.provider
            for item in results
            if not item.ok and item.detail not in {"cooldown", "stopped"}
        }
    )
    console.print(
        f"[green]живые:[/green] {len(alive)} | "
        f"[red]мертвые:[/red] {len(dead)} | [dim]{elapsed:.1f}s[/dim]"
    )
    if dead:
        preview = ", ".join(dead[:12])
        console.print(f"[dim]{preview}[/dim]")
        if Confirm.ask(f"Отключить {len(dead)} мертвых в реестре?", default=True):
            try:
                source, backup = set_enabled(set(dead), False, _registry_path())
            except (OSError, ValueError) as exc:
                console.print(f"[red]реестр не обновлен:[/red] {exc}")
                return
            console.print(f"[green]реестр:[/green] {source}")
            console.print(f"[dim]бэкап: {backup}[/dim]")
    console.print(f"[dim]лог: {log.path}[/dim]")


def restore_registry() -> None:
    try:
        specs = load_specs(_registry_path())
    except (OSError, ValueError) as exc:
        console.print(f"[red]реестр не прочитан:[/red] {exc}")
        return
    if not Confirm.ask(f"Включить все {len(specs)} провайдеров?", default=False):
        console.print("[dim]отменено.[/dim]")
        return
    try:
        source, backup = set_enabled(None, True, _registry_path())
    except (OSError, ValueError) as exc:
        console.print(f"[red]реестр не обновлен:[/red] {exc}")
        return
    console.print(f"[green]реестр:[/green] {source}")
    console.print(f"[dim]бэкап: {backup}[/dim]")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    load_dotenv()
    settings = Settings()
    console.print(
        Panel(
            "[bold cyan]PACIFICATION[/bold cyan]  | ofx in the corner\n"
            "[dim]консольный бомбер с реестром провайдеров[/dim]",
            border_style="cyan",
        )
    )

    while True:
        console.print(MENU.format(version=__version__, mode=settings.mode))
        choice = Prompt.ask(
            "команда",
            choices=["0", "1", "2", "3", "4", "5", "6"],
            default="1",
        )
        if choice == "0":
            console.print("[dim]до следующего knocking.[/dim]")
            return
        if choice == "1":
            run_campaign(settings)
        elif choice == "2":
            settings = settings_menu(settings)
        elif choice == "3":
            show_registry()
        elif choice == "4":
            run_campaign(settings, dry=True)
        elif choice == "5":
            run_health_filter(settings)
        elif choice == "6":
            restore_registry()


if __name__ == "__main__":
    main()
