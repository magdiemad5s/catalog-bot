"""
main.py — Entry point.
Run with --help to see available CLI flags.
"""
import argparse
import logging
import sys

from bot import LibraryBot
from config import Config


def setup_logging(level: str, log_file: str | None) -> None:
    """Configure console + optional file logging."""
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Always log to stdout
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    # Optionally also log to a file
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="library-bot",
        description="The Library — Discord bot",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity level (default: INFO)",
    )
    parser.add_argument(
        "--log-file",
        default="bot.log",
        metavar="PATH",
        help="Path to write log file (default: bot.log). Pass empty string to disable.",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip syncing slash commands on startup (faster for development).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(
        level=args.log_level,
        log_file=args.log_file or None,
    )

    config = Config.load()
    config.sync_commands = not args.no_sync

    bot = LibraryBot(config)
    bot.run(config.token, log_handler=None)  # logging already configured above


if __name__ == "__main__":
    main()