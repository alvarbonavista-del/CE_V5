"""Tests del discovery explicito del catalogo vivo (CE-14, materializacion)."""

from __future__ import annotations

from ce_v5.platform.rules.catalog import DataSourceCatalog
from ce_v5.platform.rules.discovery import discover_declarations

_EXPECTED = {
    "market.close",
    "market.footprint",
    "vp.poc",
    "vp.vah",
    "vp.val",
    "vp.hvn",
    "vp.lvn",
    "orderflow.delta",
    "orderflow.delta_momentum",
    "cvd.value",
    "footprint.price_range",
    "pivotphase.phase",
    "pivotphase.confidence",
    "candle.body_pct",
    "candle.upper_shadow_pct",
    "candle.lower_shadow_pct",
    "ema.value",
    "rsi.value",
    "macd.line",
    "macd.signal",
    "macd.histogram",
    "fib.nearest_level",
    "fib.level_pct",
    "volume.ratio_vs_avg",
    "vwap.value",
    "vwap.distance_pct",
    "swing.high",
    "swing.low",
}


def test_discovery_incluye_el_dag_base() -> None:
    ids = {d.source_id for d in discover_declarations()}
    assert ids == _EXPECTED


def test_discovery_incluye_market_close_aditividad() -> None:
    ids = {d.source_id for d in discover_declarations()}
    assert "market.close" in ids


def test_discovery_no_incluye_diferidas() -> None:
    ids = {d.source_id for d in discover_declarations()}
    for prefix in ("absorption.", "climax.", "void.", "notrade."):
        assert not any(i.startswith(prefix) for i in ids)


def test_discovery_no_incluye_las_fib_no_escalares() -> None:
    # LOTE 5 gate D1: fib.levels es una LISTA de 17 niveles y fib.direction un
    # CATEGORICO; el marco de fuentes de v5.0 sirve escalares. Se quedan fuera del
    # catalogo hasta que ese gate las resuelva -- no por un if del validador, sino
    # porque fib.declarations() no las publica (aditividad).
    ids = {d.source_id for d in discover_declarations()}
    assert "fib.levels" not in ids
    assert "fib.direction" not in ids


def test_sin_duplicados() -> None:
    all_ids = [d.source_id for d in discover_declarations()]
    assert len(all_ids) == len(set(all_ids))


def test_catalogo_vivo_valida() -> None:
    catalog = DataSourceCatalog()
    for declaration in discover_declarations():
        catalog.register(declaration)
    catalog.validate()
