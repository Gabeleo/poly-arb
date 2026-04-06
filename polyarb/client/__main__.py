"""Entry point for ``python -m polyarb.client``."""

import argparse
import os

from polyarb.client.cli import ClientShell


def main() -> None:
    parser = argparse.ArgumentParser(prog="polyarb.client")
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--api-key", default=os.environ.get("POLYARB_API_KEY", ""))
    args = parser.parse_args()
    shell = ClientShell(daemon_url=args.url, api_key=args.api_key)
    shell.cmdloop()


if __name__ == "__main__":
    main()
