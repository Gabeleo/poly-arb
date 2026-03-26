"""Entry point for ``python -m polyarb``.

Modes:
  (default)        Thin client CLI — connects to running daemon
  --daemon         Start the daemon (REST + WS + scan loop)
  --mock           Legacy sync CLI with MockDataProvider
  --poly           Legacy sync CLI with live Polymarket data
  --kalshi         Legacy sync CLI with live Kalshi data
"""

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="polyarb")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--daemon", action="store_true",
        help="Start the daemon (REST + WS + scan loop)",
    )
    mode.add_argument(
        "--mock", action="store_true",
        help="Legacy sync CLI with mock data",
    )
    mode.add_argument(
        "--poly", action="store_true",
        help="Legacy sync CLI with live Polymarket data",
    )
    mode.add_argument(
        "--kalshi", action="store_true",
        help="Legacy sync CLI with live Kalshi data",
    )

    # Client options
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="Daemon URL for client mode")

    # Daemon options
    parser.add_argument("--host", default="127.0.0.1", help="Bind host for daemon mode")
    parser.add_argument("--port", type=int, default=8080, help="Bind port for daemon mode")
    parser.add_argument(
        "--interval", type=float, default=5.0,
        help="Scan interval in seconds for daemon mode",
    )

    args = parser.parse_args()

    if args.daemon:
        # Forward relevant args to daemon's main via sys.argv
        daemon_argv = ["polyarb.daemon", "--host", args.host, "--port", str(args.port),
                       "--interval", str(args.interval)]
        sys.argv = daemon_argv
        from polyarb.daemon.__main__ import main as daemon_main
        daemon_main()

    elif args.mock:
        from polyarb.cli import PolyarbShell
        shell = PolyarbShell(live=False, kalshi=False)
        shell.cmdloop()

    elif args.poly:
        from polyarb.cli import PolyarbShell
        shell = PolyarbShell(live=True, kalshi=False)
        shell.cmdloop()

    elif args.kalshi:
        from polyarb.cli import PolyarbShell
        shell = PolyarbShell(live=False, kalshi=True)
        shell.cmdloop()

    else:
        # Default: thin client CLI
        from polyarb.client.cli import ClientShell
        shell = ClientShell(daemon_url=args.url)
        shell.cmdloop()


if __name__ == "__main__":
    main()
