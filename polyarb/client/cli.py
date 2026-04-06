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


def _link(url: str, text: str) -> str:
    """OSC 8 terminal hyperlink — clickable in modern terminals."""
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def _market_url(market: dict) -> str:
    """Reconstruct a market URL from serialized market data."""
    slug = market.get("event_slug", "")
    if not slug:
        return ""
    if market.get("platform") == "kalshi":
        return f"https://kalshi.com/events/{slug}"
    return f"https://polymarket.com/event/{slug}"


class ClientShell(cmd.Cmd):
    prompt = f"{B}polyarb> {R}"

    def __init__(self, daemon_url: str = "http://127.0.0.1:8080", api_key: str = "") -> None:
        super().__init__()
        self.daemon_url = daemon_url
        self.client = DaemonClient(base_url=daemon_url, api_key=api_key or None)
        self._scan_results: list[tuple[str, dict]] = []

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
            print(f"     Run {B}scan{R} to view.\n{self.prompt}", end="", flush=True)

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
                    f"     Run {B}scan{R} to view.\n{self.prompt}",
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

    def do_scan(self, arg: str) -> None:
        """Show daemon findings. Usage: scan [--cross | --single]"""
        flag = arg.strip().lower()
        show_cross = flag != "--single"
        show_single = flag != "--cross"

        if flag and flag not in ("--cross", "--single"):
            print(f"{YELLOW}Usage: scan [--cross | --single]{R}")
            return

        matches = self.client.get_matches() if show_cross else []
        opps = self.client.get_opportunities() if show_single else []

        if matches is None or opps is None:
            print(f"{RED}Could not reach daemon.{R}")
            return

        # Sort matches by best_arb profit descending
        sorted_matches = sorted(
            matches,
            key=lambda m: m.get("best_arb", {}).get("profit", 0),
            reverse=True,
        )
        # Sort opps by expected_profit_per_share descending
        sorted_opps = sorted(
            opps,
            key=lambda o: o.get("expected_profit_per_share", 0),
            reverse=True,
        )

        # Build combined list: matches first, then opps
        self._scan_results = []
        for m in sorted_matches:
            self._scan_results.append(("match", m))
        for o in sorted_opps:
            self._scan_results.append(("opp", o))

        if not self._scan_results:
            print(f"{YELLOW}No matches or opportunities found.{R}")
            return

        w = _cols()
        dw = max(20, w - 40)

        print(f"\n{B}{GREEN}{len(self._scan_results)} result(s):{R}\n")
        print(f"{B}{'#':>3}  {'Type':<14}  {'Profit':>9}  {'Description':<{dw}}{R}")
        print("\u2500" * min(w, 120))

        for i, (kind, data) in enumerate(self._scan_results, 1):
            if kind == "match":
                best = data.get("best_arb", {})
                profit = best.get("profit", 0)
                type_label = "CROSS"
                pm_q = data.get("poly_market", {}).get("question", "?")
                km_q = data.get("kalshi_market", {}).get("question", "?")
                desc = _trunc(f"{pm_q} \u2194 {km_q}", dw)
            else:
                profit = data.get("expected_profit_per_share", 0)
                type_label = data.get("arb_type", "SINGLE").upper()
                desc = _trunc(data.get("market", {}).get("question", "?"), dw)

            if profit > 0:
                color = GREEN
            elif profit == 0:
                color = YELLOW
            else:
                color = RED
            sign = "+" if profit > 0 else ""
            print(
                f"{color}{i:>3}  {type_label:<14}  "
                f"{sign}${profit:>7.4f}  {desc}{R}"
            )

        print(f"\n  Use {B}detail <#>{R} for details, {B}execute <#>{R} to trade.\n")

    def do_detail(self, arg: str) -> None:
        """Show detail for a scan result. Usage: detail <#>"""
        idx = _parse_int(arg, 0)
        if idx < 1:
            print(f"{YELLOW}Usage: detail <#>{R}")
            return
        if not self._scan_results or idx > len(self._scan_results):
            print(f"{YELLOW}No scan result #{idx}. Run {B}scan{R}{YELLOW} first.{R}")
            return

        kind, data = self._scan_results[idx - 1]

        if kind == "match":
            self._detail_match(idx, data)
        else:
            self._detail_opp(idx, data)

    def _detail_match(self, idx: int, data: dict) -> None:
        """Render cross-platform match detail."""
        best = data.get("best_arb", {})
        profit = best.get("profit", 0)
        kalshi_desc = best.get("kalshi_desc", "")
        poly_desc = best.get("poly_desc", "")
        conf = data.get("confidence", 0)
        color = GREEN if profit > 0 else YELLOW

        print(f"\n{B}{color}Cross-Platform Match #{idx}{R}  ({conf:.0%} confidence)\n")

        pm = data.get("poly_market", {})
        pm_url = _market_url(pm)
        pm_label = _link(pm_url, pm.get("question", "?")) if pm_url else pm.get("question", "?")
        print(f"  {B}Polymarket:{R} {pm_label}")
        if pm_url:
            print(f"    {DIM}{pm_url}{R}")
        yt = pm.get("yes_token", {})
        nt = pm.get("no_token", {})
        print(f"    YES  mid={yt.get('midpoint', 0):.4f}  bid={yt.get('best_bid', 0):.4f}  ask={yt.get('best_ask', 0):.4f}")
        print(f"    NO   mid={nt.get('midpoint', 0):.4f}  bid={nt.get('best_bid', 0):.4f}  ask={nt.get('best_ask', 0):.4f}")

        km = data.get("kalshi_market", {})
        km_url = _market_url(km)
        km_label = _link(km_url, km.get("question", "?")) if km_url else km.get("question", "?")
        print(f"\n  {B}Kalshi:{R} {km_label}")
        if km_url:
            print(f"    {DIM}{km_url}{R}")
        yt = km.get("yes_token", {})
        nt = km.get("no_token", {})
        print(f"    YES  mid={yt.get('midpoint', 0):.4f}  bid={yt.get('best_bid', 0):.4f}  ask={yt.get('best_ask', 0):.4f}")
        print(f"    NO   mid={nt.get('midpoint', 0):.4f}  bid={nt.get('best_bid', 0):.4f}  ask={nt.get('best_ask', 0):.4f}")

        print(f"\n  {B}Arb (at ask prices):{R}")
        print(f"    {kalshi_desc}  +  {poly_desc}")
        print(f"    Profit/share: {color}${profit:.4f}{R}")
        print()

    def _detail_opp(self, idx: int, data: dict) -> None:
        """Render single-platform opportunity detail."""
        profit = data.get("expected_profit_per_share", 0)
        arb_type = data.get("arb_type", "SINGLE").upper()
        color = GREEN if profit > 0 else YELLOW

        print(f"\n{B}{color}Single-Platform Opportunity #{idx}{R}  (type: {arb_type})\n")

        markets = data.get("markets", [])
        mkt = markets[0] if markets else {}
        mkt_url = _market_url(mkt)
        mkt_label = _link(mkt_url, mkt.get("question", "?")) if mkt_url else mkt.get("question", "?")
        print(f"  {B}Market:{R} {mkt_label}")
        if mkt_url:
            print(f"    {DIM}{mkt_url}{R}")

        yes_price = mkt.get("yes_price", data.get("yes_price", 0))
        no_price = mkt.get("no_price", data.get("no_price", 0))
        print(f"    YES  price={yes_price:.4f}")
        print(f"    NO   price={no_price:.4f}")

        print(f"\n  {B}Arb type:{R}  {arb_type}")
        print(f"  {B}Profit/share:{R} {color}${profit:.4f}{R}")
        print()

    def do_execute(self, arg: str) -> None:
        """Execute a trade from scan results. Usage: execute <#>"""
        idx = _parse_int(arg, 0)
        if idx < 1:
            print(f"{YELLOW}Usage: execute <#>{R}")
            return
        if not self._scan_results or idx > len(self._scan_results):
            print(f"{YELLOW}No scan result #{idx}. Run {B}scan{R}{YELLOW} first.{R}")
            return

        kind, data = self._scan_results[idx - 1]

        if kind == "opp":
            print(f"{YELLOW}Single-platform execution is paper-trade only "
                  f"(not yet supported via daemon).{R}")
            return

        # For matches: compute the 1-based index within just the matches
        match_index = sum(
            1 for k, _ in self._scan_results[:idx - 1] if k == "match"
        ) + 1

        result = self.client.execute(match_index)
        if "error" in result:
            print(f"{RED}Error: {result['error']}{R}")
        else:
            order = result.get("order", {})
            mid = result.get("match_id", match_index)
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
