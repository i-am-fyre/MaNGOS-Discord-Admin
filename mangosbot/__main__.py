"""Entry point: ``python -m mangosbot [--config config.toml] [--check]``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config import Config, ConfigError, load_config


def _setup_logging(config: Config) -> None:
    log_dir = config.bot.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file = logging.handlers.RotatingFileHandler(log_dir / "bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    file.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(config.bot.log_level)
    root.handlers[:] = [console, file]
    if config.bot.log_level != "DEBUG":
        logging.getLogger("discord").setLevel(logging.WARNING)


async def _run(config: Config) -> None:
    from .bot import ServerBot  # imported here so the bot is built inside the running loop

    bot = ServerBot(config)
    await bot.start_services()
    try:
        await bot.start(config.bot.token)
    finally:
        if not bot.is_closed():
            await bot.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mangosbot", description="MaNGOS Discord server bot")
    parser.add_argument("--config", default="config.toml", help="path to the config file (default: config.toml)")
    parser.add_argument("--check", action="store_true", help="validate the config and exit")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    load_dotenv(config_path.parent / ".env", override=False)
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.check:
        realms = ", ".join(f"{r.name} ({r.core})" for r in config.realms)
        print(f"Config OK: {config.server.name}, realms: {realms}")
        return 0

    _setup_logging(config)
    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
