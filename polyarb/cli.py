from __future__ import annotations

import cmd
import shutil
from datetime import datetime, timezone

from polyarb.config import Config
from polyarb.data.live import LiveDataProvider
from polyarb.data.mock import MockDataProvider
from polyarb.engine.multi import detect_multi
from polyarb.engine.single import detect_single
from polyarb.execution.executor import MockExecutor
from polyarb.execution.orders import build_order_set
from polyarb.models import Market, Opportunity, OrderSet

# ANSI
B = "\033[1m"
R = "\033[0m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
DIM = "\033[2m"


def _link(url: str, text: str) -> str:
    """OSC 8 terminal hyperlink — clickable in modern terminals."""
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def _cols() -> int:
    return shutil.get_terminal_size((100, 24)).columns


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


class PolyarbShell(cmd.Cmd):
    intro = (
        f"\n{B}{CYAN}  polyarb{R} — Polymarket Arbitrage CLI\n"
        f"  Type {B}help{R} for commands, {B}quit{R} to exit.\n"
    )
    prompt = f"{B}polyarb> {R}"

    def __init__(self, live: bool = False) -> None:
        super().__init__()
        self.config = Config()
        self.executor = MockExecutor()
        self.live = live

        if live:
            self.provider = LiveDataProvider(limit=10)
        else:
            self.provider = MockDataProvider(drift=True)

        self._markets: list[Market] = []
        self._opps: list[tuple[Opportunity, OrderSet]] = []

    # ── Data ──────────────────────────────────────────────────

    def do_fetch(self, arg: str) -> None:
        """Fetch top 10 markets by volume from Polymarket (or mock data)."""
        src = "Polymarket" if self.live else "mock provider"
        print(f"{DIM}Fetching from {src}...{R}")
        try:
            self._markets = self.provider.get_active_markets()
        except Exception as e:
            print(f"{RED}Fetch failed: {e}{R}")
            return
        print(f"{GREEN}Loaded {len(self._markets)} markets (by volume, increasing).{R}")

    # ── Views ─────────────────────────────────────────────────

    def do_markets(self, arg: str) -> None:
        """List fetched markets. Usage: markets [count]"""
        if not self._markets:
            print(f"{YELLOW}No markets loaded. Run {B}fetch{R}{YELLOW} first.{R}")
            return
        n = _parse_int(arg, len(self._markets))
        now = datetime.now(timezone.utc)
        w = _cols()
        qw = max(30, w - 58)
        print(f"\n{B}{'#':>4}  {'Volume':>14}  {'Expires':>8}  {'YES':>6} {'NO':>6}  {'Question':<{qw}}{R}")
        print("─" * min(w, 100))
        for i, m in enumerate(self._markets[-n:], 1):
            vol = f"${m.volume:,.0f}"
            if m.end_date and m.end_date > now:
                days = (m.end_date - now).total_seconds() / 86400
                exp = f"{days:.1f}d"
            else:
                exp = "—"
            q = _trunc(m.question, qw)
            print(f"{i:>4}  {vol:>14}  {exp:>8}  {m.yes_token.midpoint:>6.3f} {m.no_token.midpoint:>6.3f}  {q}")
        print()

    def do_expiring(self, arg: str) -> None:
        """Show markets expiring within N days. Usage: expiring [days=7]"""
        if not self._markets:
            print(f"{YELLOW}No markets loaded. Run {B}fetch{R}{YELLOW} first.{R}")
            return
        days = _parse_int(arg, 7)
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        cutoff = now + timedelta(days=days)
        expiring = [
            m for m in self._markets
            if m.end_date is not None and now < m.end_date <= cutoff
        ]
        if not expiring:
            print(f"{YELLOW}No markets expiring within {days} days.{R}")
            return

        w = _cols()
        qw = max(30, w - 58)
        print(f"\n{B}{GREEN}{len(expiring)} markets expiring within {days} days:{R}\n")
        print(f"{B}{'#':>4}  {'Volume':>14}  {'Expires':>8}  {'YES':>6} {'NO':>6}  {'Question':<{qw}}{R}")
        print("─" * min(w, 100))
        for i, m in enumerate(expiring, 1):
            dl = (m.end_date - now).total_seconds() / 86400
            vol = f"${m.volume:,.0f}"
            q = _trunc(m.question, qw)
            print(f"{i:>4}  {vol:>14}  {dl:>7.1f}d  {m.yes_token.midpoint:>6.3f} {m.no_token.midpoint:>6.3f}  {q}")

        total = sum(m.volume for m in expiring)
        print(f"\n{CYAN}Total volume: ${total:,.0f}{R}\n")

    def do_detail(self, arg: str) -> None:
        """Show full details for a market. Usage: detail <#>"""
        idx = _parse_int(arg, 0)
        if idx < 1 or idx > len(self._markets):
            print(f"{YELLOW}Usage: detail <#> (1-{len(self._markets)}){R}")
            return
        m = self._markets[idx - 1]
        now = datetime.now(timezone.utc)
        title = _link(m.url, m.question) if m.url else m.question
        print(f"\n{B}{CYAN}{title}{R}")
        if m.url:
            print(f"  URL          : {DIM}{m.url}{R}")
        print(f"  Condition ID : {DIM}{m.condition_id[:24]}...{R}")
        print(f"  Event        : {m.event_slug or '—'}")
        print(f"  NegRisk      : {m.neg_risk}")
        print(f"  Volume       : ${m.volume:,.2f}")
        if m.end_date:
            dl = (m.end_date - now).total_seconds() / 86400
            print(f"  End date     : {m.end_date:%Y-%m-%d %H:%M UTC} ({dl:.1f}d)")
        else:
            print(f"  End date     : —")
        print(f"  YES          : mid={m.yes_token.midpoint:.4f}  bid={m.yes_token.best_bid:.4f}  ask={m.yes_token.best_ask:.4f}")
        print(f"  NO           : mid={m.no_token.midpoint:.4f}  bid={m.no_token.best_bid:.4f}  ask={m.no_token.best_ask:.4f}")
        print(f"  YES+NO       : {m.yes_no_sum:.4f}  (spread={m.spread:.4f})")
        print()

    # ── Arb Detection ─────────────────────────────────────────

    def do_scan(self, arg: str) -> None:
        """Scan loaded markets for arbitrage opportunities."""
        if not self._markets:
            print(f"{YELLOW}No markets loaded. Run {B}fetch{R}{YELLOW} first.{R}")
            return
        events = self.provider.get_events()
        single = detect_single(self._markets, self.config)
        multi = detect_multi(events, self.config)
        all_opps = single + multi

        if not all_opps:
            print(f"{YELLOW}No arbitrage opportunities found.{R}")
            self._opps = []
            return

        self._opps = []
        for opp in all_opps:
            os = build_order_set(opp, self.config)
            self._opps.append((opp, os))

        self._opps.sort(key=lambda x: x[1].expected_profit, reverse=True)
        self._show_opps()

    def _show_opps(self) -> None:
        print(f"\n{B}{GREEN}{len(self._opps)} arbitrage opportunities found:{R}\n")
        w = _cols()
        qw = max(20, w - 65)
        print(f"{B}{'#':>4}  {'Type':<20}  {'Profit':>10}  {'Cost':>10}  {'Market':<{qw}}{R}")
        print("─" * min(w, 100))
        for i, (opp, os) in enumerate(self._opps, 1):
            arb_label = opp.arb_type.value.replace("_", " ").title()
            color = GREEN if "Underprice" in arb_label else RED
            mkt = opp.markets[0].question if len(opp.markets) == 1 else (opp.event.title if opp.event else "Multi")
            mkt = _trunc(mkt, qw)
            print(
                f"{color}{i:>4}  {arb_label:<20}  "
                f"${os.expected_profit:>9.4f}  ${os.total_cost:>9.4f}  {mkt}{R}"
            )
        print(f"\n  Use {B}opp <#>{R} for details, {B}execute <#>{R} to paper trade.\n")

    def do_opp(self, arg: str) -> None:
        """Show details of an arbitrage opportunity. Usage: opp <#>"""
        if not self._opps:
            print(f"{YELLOW}No opportunities. Run {B}scan{R}{YELLOW} first.{R}")
            return
        idx = _parse_int(arg, 0)
        if idx < 1 or idx > len(self._opps):
            print(f"{YELLOW}Usage: opp <#> (1-{len(self._opps)}){R}")
            return
        opp, os = self._opps[idx - 1]
        color = GREEN if "UNDER" in opp.arb_type.value else RED
        print(f"\n{B}{color}Opportunity #{idx}{R}")
        print(f"  {opp.summary()}\n")
        print(f"  {B}Markets:{R}")
        for m in opp.markets:
            label = _link(m.url, m.question) if m.url else m.question
            print(f"    {CYAN}{label}{R}")
        print(f"\n  {CYAN}{os.describe()}{R}\n")

    # ── Execution ─────────────────────────────────────────────

    def do_execute(self, arg: str) -> None:
        """Paper-trade an opportunity. Usage: execute <#>"""
        if not self._opps:
            print(f"{YELLOW}No opportunities. Run {B}scan{R}{YELLOW} first.{R}")
            return
        idx = _parse_int(arg, 0)
        if idx < 1 or idx > len(self._opps):
            print(f"{YELLOW}Usage: execute <#> (1-{len(self._opps)}){R}")
            return
        _, os = self._opps[idx - 1]
        self.executor.execute(os)

    def do_portfolio(self, arg: str) -> None:
        """Show paper trading portfolio and P&L."""
        trades = self.executor.trades
        if not trades:
            print(f"{YELLOW}No trades yet.{R}")
            return
        print(f"\n{B}Paper Trading Portfolio{R}\n")
        print(f"{'#':>4}  {'Type':<20}  {'Profit':>10}  Market")
        print("─" * 80)
        for i, os in enumerate(trades, 1):
            opp = os.opportunity
            arb = opp.arb_type.value.replace("_", " ").title()
            mkt = opp.markets[0].question if len(opp.markets) == 1 else (opp.event.title if opp.event else "Multi")
            mkt = _trunc(mkt, 40)
            color = GREEN if os.expected_profit > 0 else RED
            print(f"{color}{i:>4}  {arb:<20}  ${os.expected_profit:>9.4f}  {mkt}{R}")
        print(f"\n{B}Total P&L: {GREEN}${self.executor.total_profit:,.4f}{R}\n")

    # ── Config ────────────────────────────────────────────────

    def do_config(self, arg: str) -> None:
        """View or set config. Usage: config [key=value]"""
        if not arg:
            print(f"\n{B}Config{R}")
            for k, v in self.config.__dict__.items():
                print(f"  {k:<16} = {v}")
            print()
            return
        try:
            key, val = arg.split("=", 1)
            key = key.strip()
            val = val.strip()
            if not hasattr(self.config, key):
                print(f"{RED}Unknown config key: {key}{R}")
                return
            cur = getattr(self.config, key)
            setattr(self.config, key, type(cur)(val))
            print(f"{GREEN}{key} = {getattr(self.config, key)}{R}")
        except ValueError:
            print(f"{YELLOW}Usage: config key=value{R}")

    # ── Misc ──────────────────────────────────────────────────

    def do_quit(self, arg: str) -> bool:
        """Exit polyarb."""
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
