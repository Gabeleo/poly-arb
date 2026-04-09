"""Pure-function fee calculations for Kalshi and Polymarket."""

from __future__ import annotations

import math


def kalshi_taker_fee(
    contracts: float,
    price: float,
    rate: float = 0.07,
    multiplier: float = 1.0,
) -> float:
    """Kalshi per-order taker fee, ceiled to the nearest cent.

    fee = ceil(rate * multiplier * contracts * price * (1 - price) * 100) / 100
    """
    return math.ceil(rate * multiplier * contracts * price * (1 - price) * 100) / 100


def poly_taker_fee(
    shares: float,
    price: float,
    fee_rate: float = 0.04,
) -> float:
    """Polymarket per-order taker fee, rounded to 4 decimal places.

    fee = round(shares * fee_rate * price * (1 - price), 4)
    """
    return round(shares * fee_rate * price * (1 - price), 4)


def net_profit_single(
    yes_price: float,
    no_price: float,
    arb_type: str,
    size: float,
    platform: str,
    kalshi_rate: float = 0.07,
    poly_rate: float = 0.04,
) -> float:
    """Net profit after fees for a single-platform arb."""
    if "UNDERPRICE" in arb_type:
        gross = (1.0 - yes_price - no_price) * size
    else:
        gross = (yes_price + no_price - 1.0) * size

    if platform == "kalshi":
        fee = kalshi_taker_fee(size, yes_price, kalshi_rate) + kalshi_taker_fee(
            size, no_price, kalshi_rate
        )
    else:
        fee = poly_taker_fee(size, yes_price, poly_rate) + poly_taker_fee(size, no_price, poly_rate)

    return gross - fee


def net_profit_cross(
    kalshi_price: float,
    poly_price: float,
    size: float,
    kalshi_rate: float = 0.07,
    poly_rate: float = 0.04,
) -> float:
    """Net profit after fees for a cross-platform arb."""
    gross = (1.0 - kalshi_price - poly_price) * size
    fee = kalshi_taker_fee(size, kalshi_price, kalshi_rate) + poly_taker_fee(
        size, poly_price, poly_rate
    )
    return gross - fee
