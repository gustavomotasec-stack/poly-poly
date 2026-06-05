"""
Polymarket Trading Bot — Entry Point

Usage:
  python main.py              # Simulation mode + dashboard
  python main.py --live       # Live trading (requires .env credentials)
  python main.py --no-dashboard
"""

import argparse
import asyncio
import sys
import threading

import uvicorn
from rich.console import Console
from rich.panel import Panel

import config
from bot.engine import BotEngine
from dashboard.server import app as dashboard_app, set_engine

console = Console()


def parse_args():
    parser = argparse.ArgumentParser(description="Polymarket Trading Bot")
    parser.add_argument("--live", action="store_true", help="Enable live trading (real money)")
    parser.add_argument("--no-dashboard", action="store_true", help="Disable web dashboard")
    parser.add_argument("--host", default=config.DASHBOARD_HOST, help="Dashboard host")
    parser.add_argument("--port", type=int, default=config.DASHBOARD_PORT, help="Dashboard port")
    return parser.parse_args()


def run_dashboard(host: str, port: int):
    uvicorn.run(
        dashboard_app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )


async def main():
    args = parse_args()
    simulation_mode = not args.live

    # Safety gate: live mode requires credentials
    if not simulation_mode:
        missing = [
            k for k in ("POLYMARKET_PK", "POLYMARKET_API_KEY", "POLYMARKET_API_SECRET")
            if not getattr(config, k)
        ]
        if missing:
            console.print(
                f"[bold red]ERROR: Live mode requires credentials in .env: {', '.join(missing)}[/bold red]"
            )
            sys.exit(1)

    # Override global simulation mode
    config.SIMULATION_MODE = simulation_mode

    # Banner
    mode_label = "[yellow]SIMULATION[/yellow]" if simulation_mode else "[bold red]⚠ LIVE TRADING[/bold red]"
    console.print(
        Panel(
            f"[bold cyan]Polymarket Bot[/bold cyan]\n"
            f"Mode: {mode_label}\n"
            f"Dashboard: http://localhost:{args.port}",
            border_style="cyan",
            padding=(1, 4),
        )
    )

    # Create engine
    engine = BotEngine(simulation_mode=simulation_mode)
    set_engine(engine)

    # Start dashboard in background thread
    if not args.no_dashboard:
        dash_thread = threading.Thread(
            target=run_dashboard,
            args=(args.host, args.port),
            daemon=True,
            name="dashboard",
        )
        dash_thread.start()
        console.print(f"[green]Dashboard started at http://localhost:{args.port}[/green]")

    # Run bot
    try:
        await engine.start()
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down…[/yellow]")
        await engine.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("[yellow]Bye![/yellow]")
