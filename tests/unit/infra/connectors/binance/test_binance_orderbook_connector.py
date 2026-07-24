"""Conector de Binance, cara de LIBRO: multiplexado, routing, seed REST y reconexion.

TODO SIN RED. El socket real se valida en caliente (5.18). Aqui se prueba lo que el CI
SI caza: el libro viaja por la MISMA conexion que velas y trades (no hay socket nuevo),
el depthUpdate se enruta a la cola del LIBRO (no a velas ni trades), poll_deltas la
drena, la foto la sirve seed() por REST /api/v3/depth (que BUFFERIZA el WS antes de la
foto, procedimiento oficial I-02), y la reconexion marca TAMBIEN la clave del libro.
"""

from __future__ import annotations

import json
import threading
import time

from ce_v5.infra.connectors.binance.connector import (
    BinanceConfig,
    BinanceSpotConnector,
)
from source.families.market import (
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    RawOrderbookDelta,
)

_OB_KEY = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.ORDERBOOK,  # SIN timeframe (ADR-014).
)


def _con_mapa() -> BinanceSpotConnector:
    # timeout de pre-buffer a 0 en los tests SIN RED: no hay WS que bufferice, asi que
    # seed() procede directo a la foto (el pre-buffer se prueba aparte, con su propio
    # connector y un delta inyectado).
    return BinanceSpotConnector(
        config=BinanceConfig(orderbook_seed_buffer_timeout_s=0.0),
        native_to_canonical={"BTCUSDT": "BTC-USDT"},
    )


def _un_delta() -> RawOrderbookDelta:
    return RawOrderbookDelta(
        exchange="binance",
        market_type="spot",
        symbol="BTC-USDT",
        bids=(),
        asks=(),
        first_update_id=161,
        final_update_id=165,
    )


def _fake_depth() -> dict[str, object]:
    return {
        "lastUpdateId": 160,
        "bids": [["100.50", "2.0"]],
        "asks": [["100.60", "1.5"]],
    }


def _delta_msg() -> str:
    return json.dumps(
        {
            "e": "depthUpdate",
            "E": 1_784_073_600_123,
            "s": "BTCUSDT",
            "U": 161,
            "u": 165,
            "b": [["100.50", "1.0"], ["100.30", "0"]],
            "a": [["100.60", "2.0"]],
        }
    )


def test_el_nombre_de_stream_del_libro() -> None:
    assert (
        _con_mapa()._nombre_de_stream(_OB_KEY) == "btcusdt@depth@100ms"  # noqa: SLF001
    )


def test_el_depthupdate_va_a_la_cola_del_libro_no_a_velas_ni_trades() -> None:
    connector = _con_mapa()
    connector._encolar(_delta_msg())  # noqa: SLF001

    deltas = connector.poll_deltas(100)
    assert len(deltas) == 1
    delta = deltas[0]
    assert delta.exchange == "binance"
    assert delta.symbol == "BTC-USDT"
    assert delta.first_update_id == 161
    assert delta.final_update_id == 165
    assert delta.bids == (("100.50", "1.0"), ("100.30", "0"))
    assert delta.asks == (("100.60", "2.0"),)
    # Nada se colo en las otras dos colas: el multiplexado separa por 'e'.
    assert connector.poll(0) == []
    assert connector.poll_trades(0) == []


def test_poll_deltas_drena_todo_lo_encolado() -> None:
    connector = _con_mapa()
    connector._encolar(_delta_msg())  # noqa: SLF001
    connector._encolar(_delta_msg())  # noqa: SLF001
    assert len(connector.poll_deltas(100)) == 2
    assert connector.poll_deltas(0) == []  # ya vaciada


def test_un_depthupdate_sin_mapa_se_cuenta_como_error() -> None:
    connector = BinanceSpotConnector()  # sin mapa nativo->canonico
    connector._encolar(_delta_msg())  # noqa: SLF001
    assert connector.poll_deltas(0) == []
    assert connector.metrics.translation_errors == 1


def test_seed_pide_la_foto_por_rest_depth() -> None:
    connector = _con_mapa()
    calls: list[str] = []

    def _fake_get_json(path: str) -> object:
        calls.append(path)
        return {
            "lastUpdateId": 160,
            "bids": [["100.50", "2.0"], ["100.40", "1.0"]],
            "asks": [["100.60", "1.5"]],
        }

    connector._get_json = _fake_get_json  # type: ignore[method-assign]  # noqa: SLF001
    seed = connector.seed(_OB_KEY)

    assert len(calls) == 1
    assert calls[0].startswith("/api/v3/depth")
    assert "symbol=BTCUSDT" in calls[0]
    assert seed.base_sequence == 160  # lastUpdateId = secuencia base.
    assert seed.bids == (("100.50", "2.0"), ("100.40", "1.0"))
    assert seed.asks == (("100.60", "1.5"),)


def test_la_reconexion_marca_la_clave_del_libro() -> None:
    connector = _con_mapa()
    connector._deseados[_OB_KEY.as_stream_key()] = _OB_KEY  # noqa: SLF001

    # El lector marca por NOMBRE de stream; _key_for_stream_name lo revierte a la clave.
    nombre = connector._nombre_de_stream(_OB_KEY)  # noqa: SLF001
    assert nombre is not None
    connector._registrar_reconexion((nombre,))  # noqa: SLF001

    assert connector.metrics.reconnections == 1
    assert connector.drain_reconnected() == {_OB_KEY.as_stream_key()}


def test_seed_bufferiza_el_ws_antes_de_pedir_la_foto() -> None:
    # I-02: seed() ESPERA a que el buffer tenga >=1 delta ANTES de pedir la foto REST,
    # para que el buffer ABARQUE lastUpdateId (cierra la ventana perdida). Se simula
    # el WS entregando un delta tras un momento; la foto debe pedirse DESPUES.
    connector = BinanceSpotConnector(
        config=BinanceConfig(orderbook_seed_buffer_timeout_s=5.0),
        native_to_canonical={"BTCUSDT": "BTC-USDT"},
    )
    qsize_al_pedir_foto: list[int] = []

    def _fake_get_json(path: str) -> object:
        qsize_al_pedir_foto.append(connector._cola_orderbook.qsize())  # noqa: SLF001
        return _fake_depth()

    connector._get_json = _fake_get_json  # type: ignore[method-assign]  # noqa: SLF001

    def _entregar_delta_tras_un_momento() -> None:
        time.sleep(0.05)
        connector._cola_orderbook.put(_un_delta())  # noqa: SLF001

    hilo = threading.Thread(target=_entregar_delta_tras_un_momento)
    hilo.start()
    seed = connector.seed(_OB_KEY)
    hilo.join()

    assert seed.base_sequence == 160
    # La foto se pidio DESPUES de que el buffer tuviera el delta (no con buffer vacio).
    assert qsize_al_pedir_foto == [1]
    # El delta bufferizado NO se drena en seed(): el motor lo necesita para encadenar.
    assert connector._cola_orderbook.qsize() == 1  # noqa: SLF001


def test_seed_con_timeout_procede_si_el_buffer_nunca_se_llena() -> None:
    # Stream muerto: el buffer no se llena. seed() NO cuelga: vencido el tope procede
    # con la foto (el fail-safe/resync del motor actua si el primer delta no abarca).
    connector = BinanceSpotConnector(
        config=BinanceConfig(orderbook_seed_buffer_timeout_s=0.2),
        native_to_canonical={"BTCUSDT": "BTC-USDT"},
    )
    qsize_al_pedir_foto: list[int] = []

    def _fake_get_json(path: str) -> object:
        qsize_al_pedir_foto.append(connector._cola_orderbook.qsize())  # noqa: SLF001
        return _fake_depth()

    connector._get_json = _fake_get_json  # type: ignore[method-assign]  # noqa: SLF001

    t0 = time.monotonic()
    seed = connector.seed(_OB_KEY)
    dt = time.monotonic() - t0

    assert seed.base_sequence == 160
    assert qsize_al_pedir_foto == [0]  # procedio con el buffer VACIO (fail-safe).
    assert dt < 1.0  # no colgo: respeto el timeout (~0.2 s), no espera indefinida.
