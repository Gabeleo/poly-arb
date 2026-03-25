import argparse

from polyarb.cli import PolyarbShell


def main() -> None:
    parser = argparse.ArgumentParser(prog="polyarb")
    parser.add_argument("--live", action="store_true", help="Use live Polymarket data")
    args = parser.parse_args()

    shell = PolyarbShell(live=args.live)
    shell.cmdloop()


if __name__ == "__main__":
    main()
