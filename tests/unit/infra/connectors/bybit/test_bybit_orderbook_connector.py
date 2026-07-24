"""Conector de Bybit, cara de LIBRO: multiplexado por topic, semilla por WS, reconexion.

TODO SIN RED. Como OKX, Bybit manda la foto por el MISMO stream ('orderbook.*') como
type=snapshot; el delta es type=delta. Aqui se prueba lo que el CI SI caza: el libro se
enruta por PREFIJO de topic (y 'data' es un OBJETO, no una lista, a diferencia de
trades), el snapshot se guarda como semilla y el delta va a la cola, seed() sirve la
ultima foto (y LANZA si no llego), y un type=snapshot (reset) marca reconexion.
"""

from __future__ import annotations

import json

import pytest

from ce_v5.infra.connectors.bybit.connector import BybitSpotConnector
from ce_v5.infra.connectors.bybit.translate import BybitTranslationError
from source.families.market import MarketDataKind, MarketStreamKey, MarketType

_OB_KEY = MarketStreamKey(
    exchange="bybit",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.ORDERBOOK,  # SIN timeframe (ADR-014).
)


def _con_mapa() -> BybitSpotConnector:
    return BybitSpotConnector(native_to_canonical={"BTCUSDT": "BTC-USDT"})


def _snapshot_msg() -> str:
    return json.dumps(
        {
            "topic": "orderbook.200.BTCUSDT",
            "type": "snapshot",
            "ts": 1_784_073_600_000,
            "data": {
                "s": "BTCUSDT",
                "b": [["100.50", "2.0"], ["100.40", "1.0"]],
                "a": [["100.60", "1.5"]],
                "u": 50,
                "seq": 7000,
            },
        }
    )


def _delta_msg() -> str:
    return json.dumps(
        {
            "topic": "orderbook.200.BTCUSDT",
            "type": "delta",
            "ts": 1_784_073_600_100,
            "data": {
                "s": "BTCUSDT",
                "b": [["100.50", "0"]],
                "a": [],
                "u": 51,
                "seq": 7001,
            },
        }
    )


def test_topics_de_incluye_el_libro() -> None:
    assert _con_mapa()._topics_de((_OB_KEY,)) == ["orderbook.200.BTCUSDT"]  # noqa: SLF001


def test_el_snapshot_es_semilla_y_el_delta_va_a_la_cola() -> None:
    connector = _con_mapa()
    connector._encolar(_snapshot_msg())  # noqa: SLF001

    # El snapshot NO se encola como delta: es la foto de partida.
    assert connector.poll_deltas(0) == []
    seed = connector.seed(_OB_KEY)
    assert seed.exchange == "bybit"
    assert seed.symbol == "BTC-USDT"
    assert seed.base_sequence == 50  # Bybit publica el seq del snapshot en 'u'.
    assert seed.bids == (("100.50", "2.0"), ("100.40", "1.0"))
    assert seed.asks == (("100.60", "1.5"),)

    connector._encolar(_delta_msg())  # noqa: SLF001
    deltas = connector.poll_deltas(100)
    assert len(deltas) == 1
    delta = deltas[0]
    assert delta.update_id == 51
    assert delta.seq == 7001
    assert delta.bids == (("100.50", "0"),)  # tamano 0 = borrar (lo aplica el motor).
    assert delta.asks == ()
    # Nada se colo en velas ni trades: el multiplexado separa por prefijo de topic.
    assert connector.poll(0) == []
    assert connector.poll_trades(0) == []


def test_un_delta_de_topic_desconocido_se_cuenta_como_error() -> None:
    connector = BybitSpotConnector()  # sin mapa: BTCUSDT no resuelve a canonico.
    connector._encolar(_snapshot_msg())  # noqa: SLF001
    assert connector.poll_deltas(0) == []
    assert connector.metrics.translation_errors == 1


def test_seed_sin_foto_todavia_lanza() -> None:
    # Recien suscrito: aun no llego el type=snapshot. seed() LANZA, no inventa libro.
    with pytest.raises(BybitTranslationError):
        _con_mapa().seed(_OB_KEY)


def test_un_re_snapshot_marca_reconexion() -> None:
    connector = _con_mapa()
    connector._encolar(_snapshot_msg())  # noqa: SLF001
    # La PRIMERA foto no es reconexion: es el arranque del stream.
    assert connector.drain_reconnected() == set()

    connector._encolar(_snapshot_msg())  # noqa: SLF001  # reset = re-snapshot.
    assert connector.drain_reconnected() == {_OB_KEY.as_stream_key()}
