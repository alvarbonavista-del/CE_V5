"""Cobertura del backfill de trades de Binance. PURA, SIN RED.

Aqui se prueba la DECISION -- si el relleno REST alcanzo lo que ya teniamos --, que es
donde vive el error de logica. El IO que trae ese relleno se valida EN CALIENTE (regla
5.18): el CI es hermetico y no abre un socket ni llama al REST.

POR QUE ESTA DECISION IMPORTA TANTO: si sale mal por el lado optimista, un hueco real se
declara cubierto, nadie lo apunta, y las barras de footprint a las que les faltan trades
se publican como completas. Esa es una mentira sobre el mercado que ya no se puede
detectar despues, porque el endpoint publico no devuelve tan atras.
"""

from __future__ import annotations

from ce_v5.infra.connectors.binance.connector import _coverage_binance
from source.families.market import LastSeenTrade, RawTrade

_EVENT_TIME = 1_784_073_600_000


def _trade(trade_id: str, event_time_ms: int) -> RawTrade:
    return RawTrade(
        exchange="binance",
        market_type="spot",
        symbol="BTC-USDT",
        trade_id=trade_id,
        price="66000.00",
        qty="0.01",
        aggressor_side="buy",
        event_time_ms=event_time_ms,
        # RawTrade es el dato CRUDO del borde y no valida nada: un id no numerico llega
        # tal cual y source_sequence se queda a None, que es justo el caso que la
        # cobertura tiene que resolver por el lado seguro.
        source_sequence=int(trade_id) if trade_id.isdigit() else None,
    )


class TestPrimeraConexion:
    def test_sin_nada_persistido_no_hay_hueco(self) -> None:
        # No se puede haber perdido lo que nunca se tuvo. Declarar hueco aqui marcaria
        # como incompletas las primeras barras de CADA arranque limpio.
        covered, desde, hasta = _coverage_binance(
            LastSeenTrade(trade_id=None, event_time_ms=None),
            [_trade("500", _EVENT_TIME)],
        )

        assert covered is True
        assert (desde, hasta) == (None, None)


class TestHuecoCubierto:
    def test_el_relleno_empalma_justo_con_lo_que_teniamos(self) -> None:
        # El id mas antiguo del relleno es el SIGUIENTE al ultimo persistido: la serie
        # quedo contigua, no falta ni un trade.
        covered, desde, hasta = _coverage_binance(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME),
            [_trade("101", _EVENT_TIME + 1), _trade("102", _EVENT_TIME + 2)],
        )

        assert covered is True
        assert (desde, hasta) == (None, None)

    def test_el_relleno_solapa_con_lo_que_teniamos(self) -> None:
        # EL CASO NORMAL de una reconexion corta: el REST devuelve trades que YA
        # teniamos. El solape es la PRUEBA de continuidad; el dedup por PK lo absorbe.
        covered, desde, hasta = _coverage_binance(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME),
            [_trade("98", _EVENT_TIME - 2), _trade("101", _EVENT_TIME + 1)],
        )

        assert covered is True
        assert (desde, hasta) == (None, None)

    def test_el_orden_del_lote_no_importa(self) -> None:
        # Se busca el MINIMO por id, no se asume que el REST venga ordenado. Fiarse del
        # orden de un tercero es fiarse de algo que nadie nos garantiza por contrato.
        covered, _, _ = _coverage_binance(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME),
            [_trade("105", _EVENT_TIME + 5), _trade("101", _EVENT_TIME + 1)],
        )

        assert covered is True


class TestHuecoNoCubierto:
    def test_el_relleno_no_llega_hasta_lo_que_teniamos(self) -> None:
        # El corte duro mas que la ventana del REST: entre el trade 100 y el 900 hay
        # trades que el endpoint publico ya no devuelve. Hueco REAL.
        covered, desde, hasta = _coverage_binance(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME),
            [_trade("900", _EVENT_TIME + 900), _trade("901", _EVENT_TIME + 901)],
        )

        assert covered is False
        # El hueco va desde lo ultimo que SI teniamos hasta lo mas antiguo que el
        # relleno alcanzo: justo el tramo que falta.
        assert desde == _EVENT_TIME
        assert hasta == _EVENT_TIME + 900


class TestFailSafe:
    def test_un_relleno_vacio_declara_hueco_con_extremo_desconocido(self) -> None:
        # El REST no devolvio nada con lo que acotar el hueco. Se declara con el extremo
        # superior a None en vez de inventarle un limite: un limite inventado es peor
        # que un limite ausente, porque parece dato.
        covered, desde, hasta = _coverage_binance(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME), []
        )

        assert covered is False
        assert desde == _EVENT_TIME
        assert hasta is None

    def test_un_id_no_numerico_declara_hueco(self) -> None:
        # Si los ids no son enteros, el razonamiento por contiguidad no vale. NO se
        # improvisa otro criterio: se declara hueco y se acota por event_time.
        covered, desde, hasta = _coverage_binance(
            LastSeenTrade(trade_id="100", event_time_ms=_EVENT_TIME),
            [_trade("no-es-un-id", _EVENT_TIME + 50)],
        )

        assert covered is False
        assert desde == _EVENT_TIME
        assert hasta == _EVENT_TIME + 50

    def test_un_last_seen_no_numerico_declara_hueco(self) -> None:
        covered, _, _ = _coverage_binance(
            LastSeenTrade(trade_id="basura", event_time_ms=_EVENT_TIME),
            [_trade("101", _EVENT_TIME + 1)],
        )

        assert covered is False
