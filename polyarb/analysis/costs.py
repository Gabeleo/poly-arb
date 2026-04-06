"""Cross-platform arbitrage cost model.

Computes net profit per contract after fees and spread for buying
complementary sides across Polymarket and Kalshi.

Two arb directions exist for every matched pair:
  A) Buy YES on Poly + Buy NO on Kalshi  → one pays $1
  B) Buy NO on Poly + Buy YES on Kalshi  → one pays $1

Net profit = $1.00 - execution cost - fees.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeeParams:
    """Configurable fee parameters for both platforms.

    Polymarket: taker fee = fee_rate × p × (1 − p) per share.
    Rate varies by category (crypto 7.2%, sports 3%, politics 4%,
    economics 5%, geopolitics 0%).  No fee on sells.

    Kalshi: entry fee per contract, capped at fee_cap.
    Typical formula: min(0.07 × min(p, 1−p), fee_cap).
    """

    poly_fee_rate: float = 0.05
    kalshi_fee_cap: float = 0.02


@dataclass(frozen=True)
class ArbResult:
    """Cost breakdown for a single arb direction."""

    direction: str  # "poly_yes_kalshi_no" or "poly_no_kalshi_yes"
    poly_ask: float
    kalshi_ask: float
    gross_cost: float  # poly_ask + kalshi_ask
    poly_fee: float
    kalshi_fee: float
    net_profit: float  # 1.0 - gross_cost - fees


def poly_taker_fee(price: float, fee_rate: float) -> float:
    """Polymarket taker fee for buying at a given price.

    Formula: fee_rate × p × (1 − p).  Peaks at p=0.50.
    Only charged on buys; sells are fee-free.
    """
    return fee_rate * price * (1.0 - price)


def kalshi_entry_fee(price: float, fee_cap: float) -> float:
    """Kalshi entry fee per contract.

    Proportional to the cheaper side of the contract, capped.
    """
    return min(0.07 * min(price, 1.0 - price), fee_cap)


def compute_arb(
    poly_yes_ask: float,
    poly_no_ask: float,
    kalshi_yes_ask: float,
    kalshi_no_ask: float,
    fees: FeeParams = FeeParams(),
) -> ArbResult | None:
    """Compute the best arb direction and return its cost breakdown.

    Returns the profitable direction, or the less-negative one if neither
    is profitable.  Returns None only if inputs are invalid.
    """
    dir_a = _eval_direction(
        "poly_yes_kalshi_no",
        poly_ask=poly_yes_ask,
        kalshi_ask=kalshi_no_ask,
        fees=fees,
    )
    dir_b = _eval_direction(
        "poly_no_kalshi_yes",
        poly_ask=poly_no_ask,
        kalshi_ask=kalshi_yes_ask,
        fees=fees,
    )
    # Return whichever direction is more profitable (or less negative)
    return dir_a if dir_a.net_profit >= dir_b.net_profit else dir_b


def _eval_direction(
    direction: str,
    poly_ask: float,
    kalshi_ask: float,
    fees: FeeParams,
) -> ArbResult:
    gross_cost = poly_ask + kalshi_ask
    pf = poly_taker_fee(poly_ask, fees.poly_fee_rate)
    kf = kalshi_entry_fee(kalshi_ask, fees.kalshi_fee_cap)
    net_profit = 1.0 - gross_cost - pf - kf
    return ArbResult(
        direction=direction,
        poly_ask=poly_ask,
        kalshi_ask=kalshi_ask,
        gross_cost=round(gross_cost, 6),
        poly_fee=round(pf, 6),
        kalshi_fee=round(kf, 6),
        net_profit=round(net_profit, 6),
    )


def is_profitable(result: ArbResult) -> bool:
    """True if the arb has positive net profit after all fees."""
    return result.net_profit > 0
