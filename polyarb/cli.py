from __future__ import annotations

import cmd
import os
import shlex
import shutil
from datetime import datetime, timezone

from polyarb.colors import BOLD as B, CYAN, DIM, GREEN, RED, RESET as R, YELLOW
from polyarb.config import Config
from polyarb.data.base import group_events
from polyarb.data.kalshi import KalshiDataProvider
from polyarb.data.live import LiveDataProvider
from polyarb.data.mock import MockDataProvider
from polyarb.engine.multi import detect_multi
from polyarb.engine.single import detect_single
from polyarb.execution.executor import MockExecutor
from polyarb.execution.orders import build_order_set
from polyarb.matching.matcher import MatchedPair, find_matches
from polyarb.models import Market, Opportunity, OrderSet


def _link(url: str, text: str) -> str:
    """OSC 8 terminal hyperlink — clickable in modern terminals."""
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def _cols() -> int:
    return shutil.get_terminal_size((100, 24)).columns


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


class PolyarbShell(cmd.Cmd):
    prompt = f"{B}polyarb> {R}"

    def __init__(self, live: bool = False, kalshi: bool = False) -> None:
        super().__init__()
        self.config = Config()
        self.executor = MockExecutor()
        self.live = live

        if kalshi:
            self.provider = KalshiDataProvider(limit=100)
            self._source = "Kalshi"
        elif live:
            self.provider = LiveDataProvider(limit=10)
            self._source = "Polymarket"
        else:
            self.provider = MockDataProvider(drift=True)
            self._source = "mock"

        self.intro = (
            f"\n{B}{CYAN}  polyarb{R} — {self._source} Arbitrage CLI\n"
            f"  Type {B}help{R} for commands, {B}quit{R} to exit.\n"
        )

        self._markets: list[Market] = []
        self._opps: list[tuple[Opportunity, OrderSet]] = []

    # ── Data ──────────────────────────────────────────────────

    def do_fetch(self, arg: str) -> None:
        """Fetch markets from Polymarket.\n\nRun 'fetch' with no arguments for usage."""
        if not arg.strip():
            self._print_fetch_usage()
            return

        try:
            tokens = shlex.split(arg)
        except ValueError as e:
            print(f"{RED}Parse error: {e}{R}")
            return

        market_query = None
        expiration_hours = None
        pagination = 5

        i = 0
        while i < len(tokens):
            flag = tokens[i]
            if flag in ("--market", "-m") and i + 1 < len(tokens):
                market_query = tokens[i + 1]
                i += 2
            elif flag in ("--expiration", "-e") and i + 1 < len(tokens):
                try:
                    expiration_hours = float(tokens[i + 1])
                except ValueError:
                    print(f"{RED}--expiration requires a number (hours){R}")
                    return
                i += 2
            elif flag in ("--pagination", "-p") and i + 1 < len(tokens):
                try:
                    pagination = min(max(1, int(tokens[i + 1])), 500)
                except ValueError:
                    print(f"{RED}--pagination requires an integer (1-500){R}")
                    return
                i += 2
            else:
                print(f"{RED}Unknown option: {flag}{R}")
                self._print_fetch_usage()
                return

        if market_query is None and expiration_hours is None:
            print(f"{RED}Specify --market <name> or --expiration <hours>{R}")
            self._print_fetch_usage()
            return

        print(f"{DIM}Fetching from {self._source}...{R}")

        try:
            if market_query is not None:
                self._markets = self.provider.search_markets(market_query, limit=pagination)
            else:
                self._markets = self.provider.get_expiring_within(expiration_hours, limit=pagination)
        except Exception as e:
            print(f"{RED}Fetch failed: {e}{R}")
            return

        if not self._markets:
            print(f"{YELLOW}No markets found.{R}")
            return

        print(f"{GREEN}Found {len(self._markets)} markets.{R}\n")
        self.do_markets("")

    def _print_fetch_usage(self) -> None:
        print(f"""
{B}fetch{R} — Retrieve markets from {self._source}

{B}Usage:{R}
  fetch --market <name>              Search by name, top 5 by 24h volume
  fetch --expiration <hours>         Markets expiring within <hours> hours

{B}Options:{R}
  --pagination, -p <N>               Result count (default: 5, max: 500)

{B}Aliases:{R}  -m (--market)  -e (--expiration)  -p (--pagination)

{B}Examples:{R}
  fetch --market bitcoin
  fetch -m election -p 20
  fetch --expiration 48
  fetch -e 24 -p 10
""")

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
        for i, m in enumerate(self._markets[:n], 1):
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
        cid = m.condition_id
        cid_display = f"{cid[:24]}..." if len(cid) > 24 else cid
        print(f"  Condition ID : {DIM}{cid_display}{R}")
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
        events = group_events(self._markets)
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

    def do_connect(self, arg: str) -> None:
        """Connect to Kalshi for live order execution.

        Usage: connect [--live]

        Reads credentials from environment variables:
          KALSHI_API_KEY   — API key ID
          KALSHI_KEY_FILE  — path to RSA private key PEM file

        Default connects to DEMO. Use 'connect --live' for production.
        """
        try:
            from polyarb.execution.kalshi import KalshiAuth, KalshiClient, KalshiExecutor
        except ImportError:
            print(f"{RED}cryptography package required. Install with: pip install cryptography{R}")
            return

        api_key = os.environ.get("KALSHI_API_KEY", "")
        key_file = os.environ.get("KALSHI_KEY_FILE", "")

        if not api_key or not key_file:
            print(f"{RED}Set environment variables first:{R}")
            print(f"  export KALSHI_API_KEY=your_key_id")
            print(f"  export KALSHI_KEY_FILE=/path/to/private_key.pem")
            return

        is_live = arg.strip() == "--live"
        env = "PRODUCTION" if is_live else "DEMO"

        if is_live:
            print(f"{RED}{B}  WARNING: Connecting to Kalshi PRODUCTION. Real money!{R}")

        try:
            auth = KalshiAuth(api_key, key_file)
            client = KalshiClient(auth, demo=not is_live)
            balance = client.get_balance()
            self.executor = KalshiExecutor(client)
            print(f"{GREEN}  Connected to Kalshi ({env}). Balance: ${balance:,.2f}{R}")
        except Exception as e:
            print(f"{RED}  Connection failed: {e}{R}")

    def do_balance(self, arg: str) -> None:
        """Show Kalshi account balance (requires connect first)."""
        try:
            from polyarb.execution.kalshi import KalshiExecutor
        except ImportError:
            print(f"{YELLOW}Not available (cryptography not installed).{R}")
            return

        if not isinstance(self.executor, KalshiExecutor):
            print(f"{YELLOW}Not connected. Run {B}connect{R}{YELLOW} first.{R}")
            return

        try:
            balance = self.executor.client.get_balance()
            env = "DEMO" if self.executor.client.demo else "LIVE"
            print(f"\n{B}Kalshi Balance ({env}):{R} ${balance:,.2f}\n")
        except Exception as e:
            print(f"{RED}Failed: {e}{R}")

    def do_positions(self, arg: str) -> None:
        """Show Kalshi positions (requires connect first)."""
        try:
            from polyarb.execution.kalshi import KalshiExecutor
        except ImportError:
            print(f"{YELLOW}Not available (cryptography not installed).{R}")
            return

        if not isinstance(self.executor, KalshiExecutor):
            print(f"{YELLOW}Not connected. Run {B}connect{R}{YELLOW} first.{R}")
            return

        try:
            positions = self.executor.client.get_positions(ticker=arg.strip())
            if not positions:
                print(f"{YELLOW}No open positions.{R}")
                return
            print(f"\n{B}Kalshi Positions:{R}\n")
            print(f"{'Ticker':<30}  {'Position':>10}  {'Exposure':>12}  {'P&L':>10}")
            print("─" * 70)
            for p in positions:
                pos = p.get("position_fp", "0")
                exposure = p.get("market_exposure_dollars", "0")
                pnl = p.get("realized_pnl_dollars", "0")
                ticker = p.get("ticker", "?")
                color = GREEN if float(pnl) >= 0 else RED
                print(f"{color}{ticker:<30}  {pos:>10}  ${float(exposure):>11.2f}  ${float(pnl):>9.2f}{R}")
            print()
        except Exception as e:
            print(f"{RED}Failed: {e}{R}")

    def do_execute(self, arg: str) -> None:
        """Execute an opportunity. Paper-trade unless connected via 'connect'."""
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
        """Show trading portfolio and P&L."""
        trades = self.executor.trades
        if not trades:
            print(f"{YELLOW}No trades yet.{R}")
            return
        print(f"\n{B}Trading Portfolio{R}\n")
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

    # ── Cross-platform matching ────────────────────────────────

    def do_cross(self, arg: str) -> None:
        """Find cross-platform matches between Polymarket and Kalshi.

        Usage: cross [min_confidence]   (default 0.5, range 0.0-1.0)
        """
        min_conf = float(arg) if arg.strip() else 0.5

        print(f"{DIM}Fetching from Polymarket...{R}")
        try:
            poly_markets = LiveDataProvider(limit=100).get_active_markets()
        except Exception as e:
            print(f"{RED}Polymarket fetch failed: {e}{R}")
            return

        print(f"{DIM}Fetching from Kalshi...{R}")
        try:
            kalshi_markets = KalshiDataProvider(limit=200).get_active_markets()
        except Exception as e:
            print(f"{RED}Kalshi fetch failed: {e}{R}")
            return

        print(
            f"{DIM}Matching {len(poly_markets)} × {len(kalshi_markets)} markets...{R}"
        )
        matches = find_matches(poly_markets, kalshi_markets, min_confidence=min_conf)

        if not matches:
            print(f"{YELLOW}No cross-platform matches above {min_conf:.0%} confidence.{R}")
            return

        self._cross_matches = matches
        self._show_cross()

    def _show_cross(self) -> None:
        matches = self._cross_matches
        w = _cols()
        qw = max(20, (w - 45) // 2)
        print(f"\n{B}{GREEN}{len(matches)} cross-platform matches:{R}\n")
        print(
            f"{B}{'#':>3}  {'Conf':>5}  {'Spread':>7}  "
            f"{'Polymarket':<{qw}}  {'Kalshi':<{qw}}{R}"
        )
        print("─" * min(w, 120))

        for i, pair in enumerate(matches, 1):
            spread = pair.yes_spread
            color = GREEN if abs(spread) >= 0.02 else ""
            pm_q = _trunc(pair.poly_market.question, qw - 8)
            km_q = _trunc(pair.kalshi_market.question, qw - 8)
            pm_y = pair.poly_market.yes_token.midpoint
            km_y = pair.kalshi_market.yes_token.midpoint
            sign = "+" if spread > 0 else ""
            print(
                f"{color}{i:>3}  {pair.confidence:>5.0%}  "
                f"{sign}{spread:>6.3f}  "
                f"{pm_q} ({pm_y:.3f})  "
                f"{km_q} ({km_y:.3f}){R}"
            )

        print(
            f"\n  Spread = Kalshi YES − Poly YES "
            f"(positive = cheaper on Polymarket)\n"
        )

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
