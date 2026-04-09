"""Entry point: python -m polyarb.recorder"""

import argparse
import asyncio
import logging

from polyarb.recorder.recorder import (
    DEFAULT_DB_PATH,
    DEFAULT_FETCH_LIMIT,
    DEFAULT_INTERVAL,
    run_recorder,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="polyarb.recorder",
        description="Record market snapshots from Polymarket and Kalshi to SQLite.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        help=f"seconds between scans (default: {DEFAULT_INTERVAL})",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_FETCH_LIMIT,
        help=f"max markets to fetch per platform (default: {DEFAULT_FETCH_LIMIT})",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    asyncio.run(
        run_recorder(
            interval=args.interval,
            db_path=args.db,
            fetch_limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()
