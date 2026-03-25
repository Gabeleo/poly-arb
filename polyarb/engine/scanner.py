from __future__ import annotations

import sys
import time

from polyarb.alerts.console import ConsoleAlerter
from polyarb.config import Config
from polyarb.data.base import DataProvider, group_events
from polyarb.engine.multi import detect_multi
from polyarb.engine.single import detect_single
from polyarb.execution.executor import Executor
from polyarb.execution.orders import build_order_set
from polyarb.models import Opportunity, OrderSet


class Scanner:
    def __init__(
        self,
        provider: DataProvider,
        executor: Executor,
        config: Config | None = None,
    ) -> None:
        self.provider = provider
        self.executor = executor
        self.config = config or Config()
        self.alerter = ConsoleAlerter()
        self._seen: dict[str, float] = {}
        self._active: dict[int, OrderSet] = {}

    def _expire_seen(self) -> None:
        cutoff = time.monotonic() - self.config.dedup_window
        self._seen = {k: t for k, t in self._seen.items() if t > cutoff}

    def _scan_once(self) -> list[tuple[Opportunity, OrderSet]]:
        self._expire_seen()
        markets = self.provider.get_active_markets()
        events = group_events(markets)

        opps = detect_single(markets, self.config) + detect_multi(events, self.config)

        results = []
        now = time.monotonic()
        for opp in opps:
            if opp.key in self._seen:
                continue
            self._seen[opp.key] = now
            order_set = build_order_set(opp, self.config)
            results.append((opp, order_set))
        return results

    def _display(self, results: list[tuple[Opportunity, OrderSet]]) -> None:
        self._active.clear()
        for i, (opp, order_set) in enumerate(results, 1):
            self._active[i] = order_set
            self.alerter.alert(i, opp, order_set)

        if results:
            self.alerter.info(
                f"\nType a number (1-{len(results)}) to execute, or wait for next scan..."
            )

    def _check_stdin(self) -> str | None:
        if sys.platform == "win32":
            import msvcrt
            if msvcrt.kbhit():
                return sys.stdin.readline().strip()
            time.sleep(0.5)
            return None
        import select as _select
        ready, _, _ = _select.select([sys.stdin], [], [], 0.5)
        if ready:
            return sys.stdin.readline().strip()
        return None

    def _handle_input(self, line: str) -> None:
        try:
            idx = int(line)
        except ValueError:
            if line.lower() in ("q", "quit", "exit"):
                raise KeyboardInterrupt
            self.alerter.info(f"Invalid input: {line!r}")
            return

        order_set = self._active.get(idx)
        if order_set is None:
            self.alerter.info(f"No active opportunity #{idx}")
            return

        self.executor.execute(order_set)
        del self._active[idx]

    def run(self) -> None:
        self.alerter.info("Polyarb scanner started. Press Ctrl+C to quit.\n")
        try:
            while True:
                results = self._scan_once()
                if results:
                    self._display(results)
                else:
                    self.alerter.info("No arbitrage opportunities found. Waiting...")

                deadline = time.monotonic() + self.config.scan_interval
                while time.monotonic() < deadline:
                    line = self._check_stdin()
                    if line is not None:
                        self._handle_input(line)

        except KeyboardInterrupt:
            self.alerter.info("\nScanner stopped.")
