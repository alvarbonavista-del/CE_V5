"""Cobertura del backfill de trades de OKX (_coverage_okx). PURA, SIN RED.

La DECISION -- si el relleno REST alcanzo lo que ya teniamos -- es donde vive el error
de logica; el IO que trae el relleno se valida en caliente (regla 5.18). IGUAL EN FORMA
a la de Binance porque el tradeId de OKX es, como el de Binance, un entero monotono y
contiguo por instrumento (verificado en el sondeo en vivo).

POR QUE IMPORTA TANTO: si sale mal por el lado optimista, un hueco real se declara
cubierto, nadie lo apunta, y las barras de footprint a las que les faltan trades se
publican como completas. Una mentira sobre el mercado que ya no se puede detectar.
"""

from __future__ import annotations

from ce_v5.infra.connectors.okx.connector import _coverage_okx
from source.families.market import LastSeenTrade, RawTrade

_EVENT_TIME = 1_700_000_000_000


def _trade(trade_id: str, event_time_ms: int) -> RawTrade:
    return RawTrade(
        exchange="okx",
        market_type="spot",
        symbol="BTC-USDT",
        trade_id=trade_id,
        price="66000.00",
        qty="0.01",
        aggressor_side="buy",
        event_time_ms=event_time_ms,
        source_sequence=int(trade_id) if trade_id.isdigit() else None,
    )


class TestPrimeraConexion:
    def test_sin_nada_persistido_no_hay_hueco(self) -> None:
        covered, desde, hasta = _coverage_okx(
            LastSeenTrade(trade_id=None, event_time_ms=None),
            [_trade("500", _EVENT_TIME)],
        )
        assert covered is True
        assert (desde, hasta) == (None, None)


class TestHuecoCubierto:
    def test_el_relleno_empalma_justo(self) -> None:
        covered, desde, hasta = _coverage_okx(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME),
            [_trade("101", _EVENT_TIME + 1), _trade("102", _EVENT_TIME + 2)],
        )
        assert covered is True
        assert (desde, hasta) == (None, None)

    def test_el_relleno_solapa(self) -> None:
        # Reconexion corta: el REST devuelve trades que YA teniamos. El solape es la
        # PRUEBA de continuidad; el dedup por identidad natural lo absorbe.
        covered, desde, hasta = _coverage_okx(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME),
            [_trade("98", _EVENT_TIME - 2), _trade("101", _EVENT_TIME + 1)],
        )
        assert covered is True
        assert (desde, hasta) == (None, None)

    def test_el_orden_del_lote_no_importa(self) -> None:
        # Se busca el MINIMO por id; no se asume que el REST venga ordenado.
        covered, _, _ = _coverage_okx(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME),
            [_trade("105", _EVENT_TIME + 5), _trade("101", _EVENT_TIME + 1)],
        )
        assert covered is True


class TestHuecoNoCubierto:
    def test_el_relleno_no_llega(self) -> None:
        # El corte duro mas que lo que el relleno alcanzo: entre 100 y 900 falta dato.
        covered, desde, hasta = _coverage_okx(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME),
            [_trade("900", _EVENT_TIME + 900), _trade("901", _EVENT_TIME + 901)],
        )
        assert covered is False
        assert desde == _EVENT_TIME
        assert hasta == _EVENT_TIME + 900


class TestFailSafe:
    def test_relleno_vacio_declara_hueco_con_extremo_desconocido(self) -> None:
        covered, desde, hasta = _coverage_okx(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME), []
        )
        assert covered is False
        assert desde == _EVENT_TIME
        assert hasta is None

    def test_id_no_numerico_declara_hueco(self) -> None:
        covered, desde, hasta = _coverage_okx(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME),
            [_trade("no-es-id", _EVENT_TIME + 50)],
        )
        assert covered is False
        assert desde == _EVENT_TIME
        assert hasta == _EVENT_TIME + 50

    def test_last_seen_no_numerico_declara_hueco(self) -> None:
        covered, _, _ = _coverage_okx(
            LastSeenTrade(trade_id="basura", event_time_ms=_EVENT_TIME),
            [_trade("101", _EVENT_TIME + 1)],
        )
        assert covered is False
