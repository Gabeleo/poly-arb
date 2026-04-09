"""Kelly Criterion position sizing for binary prediction market arbitrage.

Computes optimal position sizes based on edge (fee-adjusted profit)
and available bankroll. Uses a configurable fraction of Kelly to
reduce variance.
"""

from __future__ import annotations


def kelly_fraction_raw(
    net_profit_per_contract: float,
    cost_per_contract: float,
) -> float:
    """Raw Kelly fraction f* = edge / odds.

    For binary arbs: f* = net_profit / cost.
    Returns 0.0 if inputs are invalid or no edge exists.
    """
    if net_profit_per_contract <= 0 or cost_per_contract <= 0:
        return 0.0
    return net_profit_per_contract / cost_per_contract


def kelly_size(
    net_profit_per_contract: float,
    cost_per_contract: float,
    bankroll: float,
    fraction: float = 0.5,
    max_position: float | None = None,
    min_position: float = 1.0,
) -> float:
    """Compute Kelly-optimal position size in contracts.

    Parameters
    ----------
    net_profit_per_contract:
        Fee-adjusted profit per contract (from ArbResult.net_profit).
        Must be > 0 for a non-trivial result.
    cost_per_contract:
        Total cost per contract (ArbResult.gross_cost + fees).
        This is the amount at risk per contract.
    bankroll:
        Current total available capital across both platforms.
    fraction:
        Kelly fraction (0.0-1.0). Default 0.5 = half Kelly.
        0.0 disables Kelly, falling back to min_position.
    max_position:
        Hard cap on position size in contracts. If None, defaults
        to bankroll / cost_per_contract (can't spend more than you have).
    min_position:
        Minimum position size. Trades below this are not worth the
        execution overhead. Default 1.0 contract.

    Returns
    -------
    Position size in contracts (float). The caller should floor() this
    to the nearest integer for Kalshi (integer contracts) and use
    as-is for Polymarket (fractional shares).

    Returns 0.0 if:
    - net_profit_per_contract <= 0 (no edge)
    - cost_per_contract <= 0 (invalid)
    - bankroll <= 0 (nothing to trade)
    - Kelly-computed size < min_position (not worth it)
    """
    if net_profit_per_contract <= 0 or cost_per_contract <= 0 or bankroll <= 0:
        return 0.0

    f_raw = kelly_fraction_raw(net_profit_per_contract, cost_per_contract)
    position = fraction * f_raw * bankroll / cost_per_contract

    # Apply max cap
    bankroll_cap = bankroll / cost_per_contract
    effective_max = min(max_position, bankroll_cap) if max_position is not None else bankroll_cap
    position = min(position, effective_max)

    # Apply min floor
    if position < min_position:
        return 0.0

    return position
