"""Entry point for ``python -m polyarb.client``."""

import argparse

from polyarb.client.cli import ClientShell


def main() -> None:
    parser = argparse.ArgumentParser(prog="polyarb.client")
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    shell = ClientShell(daemon_url=args.url)
    shell.cmdloop()


if __name__ == "__main__":
    main()
