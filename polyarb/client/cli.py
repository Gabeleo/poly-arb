"""Thin CLI client that talks to the polyarb daemon via REST + WS."""

from __future__ import annotations

import cmd
import shutil

from polyarb.client.api import DaemonClient
from polyarb.client.ws_listener import start_ws_listener
from polyarb.colors import BOLD as B, CYAN, DIM, GREEN, RED, RESET as R, YELLOW


def _cols() -> int:
    return shutil.get_terminal_size((100, 24)).columns


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "\u2026"


class ClientShell(cmd.Cmd):
    prompt = f"{B}polyarb> {R}"

    def __init__(self, daemon_url: str = "http://127.0.0.1:8080") -> None:
        super().__init__()
        self.daemon_url = daemon_url
        self.client = DaemonClient(base_url=daemon_url)

        ws_url = daemon_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = ws_url.rstrip("/") + "/ws"
        self._ws_thread = start_ws_listener(ws_url, self._on_push)

        self.intro = (
            f"\n{B}{CYAN}  polyarb client{R} \u2014 connected to {daemon_url}\n"
            f"  Type {B}help{R} for commands, {B}quit{R} to exit.\n"
        )

    # ── WS push handler ───────────────────────────────────

    def _on_push(self, data: dict) -> None:
        """Handle real-time push messages from the daemon."""
        msg_type = data.get("type", "")

        if msg_type == "new_matches":
            count = len(data.get("data", []))
            print(f"\n{CYAN}[ws]{R} {GREEN}{count} new cross-platform match(es) detected{R}")
            print(f"     Run {B}cross{R} to view.\n{self.prompt}", end="", flush=True)

        elif msg_type == "new_opportunities":
            items = data.get("data", [])
            profitable = [
                o for o in items
                if o.get("expected_profit_per_share", 0) > 0
            ]
            if profitable:
                print(
                    f"\n{CYAN}[ws]{R} {GREEN}{len(profitable)} new profitable "
                    f"opportunity(ies){R}"
                )
                print(
                    f"     Run {B}opp{R} to view.\n{self.prompt}",
                    end="",
                    flush=True,
                )

    # ── Commands ──────────────────────────────────────────

    def do_status(self, arg: str) -> None:
        """Show daemon status."""
        data = self.client.get_status()
        if data is None:
            print(f"{RED}Could not reach daemon.{R}")
            return
        print(f"\n{B}Daemon Status{R}")
        print(f"  Uptime       : {data.get('uptime_seconds', 0):.1f}s")
        print(f"  Scans        : {data.get('scan_count', 0)}")
        print(f"  Matches      : {data.get('match_count', 0)}")
        print(f"  Opportunities: {data.get('opportunity_count', 0)}")
        print(f"  WS clients   : {data.get('connected_clients', 0)}")
        print()

    def do_cross(self, arg: str) -> None:
        """List cross-platform matches from the daemon."""
        matches = self.client.get_matches()
        if matches is None:
            print(f"{RED}Could not reach daemon.{R}")
            return
        if not matches:
            print(f"{YELLOW}No cross-platform matches found.{R}")
            return

        w = _cols()
        qw = max(15, (w - 60) // 2)

        print(f"\n{B}{GREEN}{len(matches)} cross-platform matches:{R}\n")
        print(
            f"{B}{'#':>3}  {'Conf':>5}  {'Arb':>8}  {'Kalshi leg':<14}  "
            f"{'Polymarket':<{qw}}  {'Kalshi':<{qw}}{R}"
        )
        print("\u2500" * min(w, 120))

        for i, m in enumerate(matches, 1):
            best = m.get("best_arb", {})
            profit = best.get("profit", 0)
            kalshi_desc = best.get("kalshi_desc", "")
            conf = m.get("confidence", 0)
            color = GREEN if profit > 0 else ""
            short_side = kalshi_desc.replace("BUY ", "").replace(" on Kalshi", "")
            pm_q = _trunc(m.get("poly_market", {}).get("question", "?"), qw)
            km_q = _trunc(m.get("kalshi_market", {}).get("question", "?"), qw)
            sign = "+" if profit > 0 else ""
            print(
                f"{color}{i:>3}  {conf:>5.0%}  "
                f"{sign}${profit:>6.4f}  {short_side:<14}  "
                f"{pm_q}  {km_q}{R}"
            )

        print(f"\n  Use {B}opp <#>{R} for details, {B}execute <#>{R} to trade.\n")

    def do_opp(self, arg: str) -> None:
        """Show detail for a cross-platform match. Usage: opp <#>"""
        idx = _parse_int(arg, 0)
        if idx < 1:
            print(f"{YELLOW}Usage: opp <#>{R}")
            return

        data = self.client.get_match(idx)
        if data is None:
            print(f"{YELLOW}Match #{idx} not found.{R}")
            return

        best = data.get("best_arb", {})
        profit = best.get("profit", 0)
        kalshi_desc = best.get("kalshi_desc", "")
        poly_desc = best.get("poly_desc", "")
        conf = data.get("confidence", 0)
        color = GREEN if profit > 0 else YELLOW

        print(f"\n{B}{color}Cross-Platform Match #{idx}{R}  ({conf:.0%} confidence)\n")

        pm = data.get("poly_market", {})
        print(f"  {B}Polymarket:{R} {pm.get('question', '?')}")
        yt = pm.get("yes_token", {})
        nt = pm.get("no_token", {})
        print(f"    YES  mid={yt.get('midpoint', 0):.4f}  bid={yt.get('best_bid', 0):.4f}  ask={yt.get('best_ask', 0):.4f}")
        print(f"    NO   mid={nt.get('midpoint', 0):.4f}  bid={nt.get('best_bid', 0):.4f}  ask={nt.get('best_ask', 0):.4f}")

        km = data.get("kalshi_market", {})
        print(f"\n  {B}Kalshi:{R} {km.get('question', '?')}")
        yt = km.get("yes_token", {})
        nt = km.get("no_token", {})
        print(f"    YES  mid={yt.get('midpoint', 0):.4f}  bid={yt.get('best_bid', 0):.4f}  ask={yt.get('best_ask', 0):.4f}")
        print(f"    NO   mid={nt.get('midpoint', 0):.4f}  bid={nt.get('best_bid', 0):.4f}  ask={nt.get('best_ask', 0):.4f}")

        print(f"\n  {B}Arb (at ask prices):{R}")
        print(f"    {kalshi_desc}  +  {poly_desc}")
        print(f"    Profit/share: {color}${profit:.4f}{R}")
        print()

    def do_execute(self, arg: str) -> None:
        """Execute the Kalshi leg of a match. Usage: execute <#>"""
        idx = _parse_int(arg, 0)
        if idx < 1:
            print(f"{YELLOW}Usage: execute <#>{R}")
            return

        result = self.client.execute(idx)
        if "error" in result:
            print(f"{RED}Error: {result['error']}{R}")
        else:
            order = result.get("order", {})
            mid = result.get("match_id", idx)
            status = order.get("status", "unknown")
            print(f"{GREEN}Order placed for match #{mid}: {status}{R}")

    def do_config(self, arg: str) -> None:
        """View or set config. Usage: config [key=value]"""
        if not arg or "=" not in arg:
            data = self.client.get_config()
            if data is None:
                print(f"{RED}Could not reach daemon.{R}")
                return
            print(f"\n{B}Config{R}")
            for k, v in data.items():
                print(f"  {k:<16} = {v}")
            print()
        else:
            try:
                key, val = arg.split("=", 1)
                key = key.strip()
                val = val.strip()
                # Try to coerce to number
                try:
                    parsed = int(val)
                except ValueError:
                    try:
                        parsed = float(val)
                    except ValueError:
                        parsed = val
                result = self.client.set_config({key: parsed})
                if "error" in result:
                    print(f"{RED}{result['error']}{R}")
                else:
                    print(f"{GREEN}{key} = {result.get(key, parsed)}{R}")
            except ValueError:
                print(f"{YELLOW}Usage: config key=value{R}")

    def do_quit(self, arg: str) -> bool:
        """Exit polyarb client."""
        self.client.close()
        print(f"{DIM}Goodbye.{R}")
        return True

    do_exit = do_quit
    do_q = do_quit
    do_EOF = do_quit

    def emptyline(self) -> None:
        pass

    def default(self, line: str) -> None:
        print(f"{YELLOW}Unknown command: {line!r}. Type {B}help{R}{YELLOW} for commands.{R}")


def _parse_int(s: str, default: int) -> int:
    s = s.strip()
    if not s:
        return default
    try:
        return int(s)
    except ValueError:
        return default
