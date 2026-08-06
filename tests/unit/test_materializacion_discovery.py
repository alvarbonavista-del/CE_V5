"""Tests del discovery explicito del catalogo vivo (CE-14, materializacion)."""

from __future__ import annotations

from ce_v5.platform.rules.catalog import DataSourceCatalog
from ce_v5.platform.rules.discovery import discover_declarations

_EXPECTED = {
    "absorption.bid_strength",
    "absorption.ask_strength",
    "climax.top_strength",
    "climax.bottom_strength",
    "void.snap_bullish",
    "void.snap_bearish",
    "notrade.score",
    "notrade.footprint_ineff",
    "notrade.flow_dislocation",
    "notrade.state",
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
    "imbalance.buy_stack",
    "imbalance.sell_stack",
    "pivotphase.phase",
    "pivotphase.confidence",
    "candle.body_pct",
    "candle.upper_shadow_pct",
    "candle.lower_shadow_pct",
    "candle.open",
    "candle.high",
    "candle.low",
    "ema.value",
    "rsi.value",
    "macd.line",
    "macd.signal",
    "macd.histogram",
    "fib.nearest_level",
    "fib.level_pct",
    "fib.direction",
    "fib.levels",
    "divergence.kind",
    "divergence.regular_bull",
    "divergence.regular_bear",
    "divergence.hidden_bull",
    "divergence.hidden_bear",
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


def test_discovery_ya_no_tiene_detectores_diferidos() -> None:
    # P08c-DET-01 cierra la lista: los CUATRO detectores estan cableados. Lo que queda
    # diferido no es una FUENTE sino el bloque L2 de notrade (peso 35 reservado), que
    # espera a que exista l2.* -- y eso no se ve en el catalogo, se ve en el tope 65 del
    # score.
    ids = {d.source_id for d in discover_declarations()}
    for prefix in ("absorption.", "climax.", "void.", "notrade."):
        assert any(i.startswith(prefix) for i in ids)


def test_discovery_incluye_absorption_ya_no_diferida() -> None:
    # P08c-DET-01: dos fuentes DECIMAL, una por lado del veredicto (bid/ask). El
    # triplete (detected, side, strength) no cabe en un ScalarType, asi que se sirve
    # como la FUERZA de cada lado -- 0 cuando no hay absorcion de ese lado.
    ids = {d.source_id for d in discover_declarations()}
    assert {"absorption.bid_strength", "absorption.ask_strength"} <= ids


def test_discovery_incluye_fib_levels_non_servible() -> None:
    # fib.levels (P08b-D1-04, LOTE 5) entra al catalogo vivo como NODO DECLARADO
    # NON_SERVIBLE, calcada de vp.hvn/vp.lvn: se conoce y resuelve, pero sin
    # materializador y sin FibOutput propio -- un VECTOR por barra sigue sin
    # representarlo ningun ScalarType. fib.direction (categorica) SI es servible desde
    # el LOTE 5, porque D1 cerro el carrier para CATEGORICO, no para vectorial.
    ids = {d.source_id for d in discover_declarations()}
    assert "fib.levels" in ids
    assert "fib.direction" in ids


def test_discovery_incluye_candle_high_low() -> None:
    # P08c-DET-01 paso (a): candle.high/candle.low ESPEJO EXACTO de candle.open.
    # Desbloquean climax.*/notrade.* (necesitan high/low ademas del open que ya
    # desbloqueo absorption.*).
    ids = {d.source_id for d in discover_declarations()}
    assert {"candle.high", "candle.low"} <= ids


def test_discovery_incluye_climax_ya_no_diferida() -> None:
    # P08c-DET-01: dos fuentes DECIMAL por lado, como absorption.*. Su consumes NO
    # incluye candle.open a proposito (rev 3 H2: el nucleo no lo lee).
    ids = {d.source_id for d in discover_declarations()}
    assert {"climax.top_strength", "climax.bottom_strength"} <= ids


def test_discovery_incluye_void_ya_no_diferida() -> None:
    # P08c-DET-01: dos indicadoras {0,1} por direccion del snap. Su consumes NO incluye
    # vp.lvn (es NON_SERVIBLE): el nivel se computa dentro del materializador.
    ids = {d.source_id for d in discover_declarations()}
    assert {"void.snap_bullish", "void.snap_bearish"} <= ids


def test_discovery_incluye_notrade_ya_no_diferida() -> None:
    # P08c-DET-01: CUATRO fuentes (tres cifras del score descompuesto + el estado). Es
    # el unico detector con salida STRING, y su consumes SI incluye candle.open (al
    # reves que climax): NoTradeCandle lo usa.
    ids = {d.source_id for d in discover_declarations()}
    assert {
        "notrade.score",
        "notrade.footprint_ineff",
        "notrade.flow_dislocation",
        "notrade.state",
    } <= ids


def test_discovery_incluye_las_cinco_divergence() -> None:
    # LOTE 5 (P08b-D1-05): UN estado (0028) y CINCO fuentes servibles. Las cuatro
    # BOOLEAN son las primeras de ese value_type en el catalogo vivo, y no son una
    # comodidad sobre kind: en una barra donde coincidan dos divergencias, kind colapsa
    # a una por prioridad y solo los flags dicen que paso de verdad.
    ids = {d.source_id for d in discover_declarations()}
    assert {
        "divergence.kind",
        "divergence.regular_bull",
        "divergence.regular_bear",
        "divergence.hidden_bull",
        "divergence.hidden_bear",
    } <= ids


def test_sin_duplicados() -> None:
    all_ids = [d.source_id for d in discover_declarations()]
    assert len(all_ids) == len(set(all_ids))


def test_catalogo_vivo_valida() -> None:
    catalog = DataSourceCatalog()
    for declaration in discover_declarations():
        catalog.register(declaration)
    catalog.validate()


def test_discovery_incluye_imbalance_para_F5() -> None:
    # P08c-CONF-05: las dos fuentes que desbloquean F5, el ultimo factor con peso 0.
    # POINT_LOCAL (no WINDOWED): la pila se mide dentro de la vela con ratio y minimo
    # fijos, asi que no hay ventana que leer -- misma forma que footprint.price_range.
    ids = {d.source_id for d in discover_declarations()}
    assert {"imbalance.buy_stack", "imbalance.sell_stack"} <= ids
