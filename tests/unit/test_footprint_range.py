"""Tests de footprint.price_range (P08c P5 T4a): extract POINT_LOCAL determinista.

Factory _footprint clonada de tests/unit/test_volume_profile.py (mismo patron: celdas
ordenadas por precio, totales de barra cuadrados con la suma de las celdas).
"""

from __future__ import annotations

from decimal import Decimal

from ce_v5.platform.rules.footprint_range import price_range
from source.families.footprint import FootprintCell, FootprintClosedPayload
from source.families.market import MarketType, Timeframe
from source.time import MaturityState

_TF = Timeframe.M1
_OPEN = 1_784_073_600_000  # alineado a M1 (divisible por 60_000).


def _footprint(
    levels: list[tuple[str, str, str]],
    *,
    exchange: str = "binance",
    symbol: str = "BTC-USDT",
    open_time: int = _OPEN,
) -> FootprintClosedPayload:
    """Un footprint cerrado valido a partir de (precio, buy, sell) por nivel."""
    cells = tuple(
        FootprintCell(
            price=Decimal(p),
            buy_volume=Decimal(b),
            sell_volume=Decimal(s),
            delta=Decimal(b) - Decimal(s),
        )
        for p, b, s in sorted(levels, key=lambda level: Decimal(level[0]))
    )
    bar_buy = sum((c.buy_volume for c in cells), Decimal(0))
    bar_sell = sum((c.sell_volume for c in cells), Decimal(0))
    return FootprintClosedPayload(
        exchange=exchange,
        market_type=MarketType.SPOT,
        symbol=symbol,
        timeframe=_TF,
        open_time=open_time,
        close_time=open_time + _TF.duration_ms,
        cells=cells,
        bar_buy_volume=bar_buy,
        bar_sell_volume=bar_sell,
        bar_delta=bar_buy - bar_sell,
        trade_count=len(cells),
        is_complete=True,
        maturity_state=MaturityState.CLOSED,
    )


def test_price_range_multiples_celdas() -> None:
    footprint = _footprint([("100", "1", "0"), ("101", "1", "0"), ("103", "1", "0")])
    assert price_range(footprint) == Decimal("3")


def test_price_range_una_sola_celda_es_cero() -> None:
    footprint = _footprint([("100", "1", "0")])
    assert price_range(footprint) == Decimal(0)


def test_price_range_sin_celdas_es_cero() -> None:
    footprint = _footprint([])
    assert price_range(footprint) == Decimal(0)
