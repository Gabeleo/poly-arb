import argparse

from polyarb.cli import PolyarbShell


def main() -> None:
    parser = argparse.ArgumentParser(prog="polyarb")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--poly", action="store_true", help="Use live Polymarket data")
    source.add_argument("--kalshi", action="store_true", help="Use live Kalshi data")
    args = parser.parse_args()

    shell = PolyarbShell(live=args.poly, kalshi=args.kalshi)
    shell.cmdloop()


if __name__ == "__main__":
    main()
