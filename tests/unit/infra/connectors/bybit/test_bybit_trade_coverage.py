"""Cobertura del backfill de trades de Bybit (_coverage_bybit). PURA, SIN RED.

La DECISION -- si el relleno REST alcanzo lo que ya teniamos -- es donde vive el error
de logica; el IO se valida en caliente (5.18). IGUAL EN FORMA a Binance/OKX porque el
tradeId de Bybit es un entero monotono y contiguo, y el id del WS ('i') y del REST
('execId') son el MISMO espacio. En Bybit el fail-safe es FRECUENTE (60 y no pagina).
"""

from __future__ import annotations

from ce_v5.infra.connectors.bybit.connector import _coverage_bybit
from source.families.market import LastSeenTrade, RawTrade

_EVENT_TIME = 1_784_793_000_000


def _trade(trade_id: str, event_time_ms: int) -> RawTrade:
    return RawTrade(
        exchange="bybit",
        market_type="spot",
        symbol="BTC-USDT",
        trade_id=trade_id,
        price="65000.00",
        qty="0.01",
        aggressor_side="buy",
        event_time_ms=event_time_ms,
        source_sequence=int(trade_id) if trade_id.isdigit() else None,
    )


class TestPrimeraConexion:
    def test_sin_nada_persistido_no_hay_hueco(self) -> None:
        covered, desde, hasta = _coverage_bybit(
            LastSeenTrade(trade_id=None, event_time_ms=None),
            [_trade("500", _EVENT_TIME)],
        )
        assert covered is True
        assert (desde, hasta) == (None, None)


class TestHuecoCubierto:
    def test_el_relleno_empalma_justo(self) -> None:
        covered, desde, hasta = _coverage_bybit(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME),
            [_trade("101", _EVENT_TIME + 1), _trade("102", _EVENT_TIME + 2)],
        )
        assert covered is True
        assert (desde, hasta) == (None, None)

    def test_el_relleno_solapa(self) -> None:
        # El WS dejo la ultima en 100 y el REST (execId, mismo espacio) trae 98..101:
        # hay solape, la serie es contigua, no falta nada.
        covered, _, _ = _coverage_bybit(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME),
            [_trade("98", _EVENT_TIME - 2), _trade("101", _EVENT_TIME + 1)],
        )
        assert covered is True

    def test_el_orden_del_lote_no_importa(self) -> None:
        # recent-trade viene newest-first; se busca el MINIMO por id, no se asume orden.
        covered, _, _ = _coverage_bybit(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME),
            [_trade("105", _EVENT_TIME + 5), _trade("101", _EVENT_TIME + 1)],
        )
        assert covered is True


class TestHuecoNoCubierto:
    def test_el_corte_excede_la_ventana_de_60(self) -> None:
        # EL CAMINO COMUN en Bybit: el corte duro mas que los ~60 de recent-trade
        # da, asi que entre 100 y 900 falta dato que el REST no alcanza. Hueco REAL.
        covered, desde, hasta = _coverage_bybit(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME),
            [_trade("900", _EVENT_TIME + 900), _trade("901", _EVENT_TIME + 901)],
        )
        assert covered is False
        assert desde == _EVENT_TIME
        assert hasta == _EVENT_TIME + 900


class TestFailSafe:
    def test_relleno_vacio_declara_hueco_con_extremo_desconocido(self) -> None:
        covered, desde, hasta = _coverage_bybit(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME), []
        )
        assert covered is False
        assert desde == _EVENT_TIME
        assert hasta is None

    def test_id_no_numerico_declara_hueco(self) -> None:
        covered, desde, hasta = _coverage_bybit(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME),
            [_trade("no-es-id", _EVENT_TIME + 50)],
        )
        assert covered is False
        assert desde == _EVENT_TIME
        assert hasta == _EVENT_TIME + 50

    def test_last_seen_no_numerico_declara_hueco(self) -> None:
        covered, _, _ = _coverage_bybit(
            LastSeenTrade(trade_id="basura", event_time_ms=_EVENT_TIME),
            [_trade("101", _EVENT_TIME + 1)],
        )
        assert covered is False
