"""Conector de OKX, cara de LIBRO: multiplexado, routing, semilla por WS y reconexion.

TODO SIN RED. A DIFERENCIA de Binance, OKX no pide la foto por REST: la manda por el
MISMO canal 'books' como primer mensaje (action=snapshot). Aqui se prueba lo que el CI
SI caza: el canal del libro viaja por la conexion que YA existe (sin socket nuevo), el
action=snapshot se guarda como semilla y el action=update va a la cola del libro, seed()
sirve la ultima foto (y LANZA si aun no llego), y un re-snapshot marca reconexion.
"""

from __future__ import annotations

import json

import pytest

from ce_v5.infra.connectors.okx.connector import OkxSpotConnector
from ce_v5.infra.connectors.okx.translate import OkxTranslationError
from source.families.market import MarketDataKind, MarketStreamKey, MarketType

_OB_KEY = MarketStreamKey(
    exchange="okx",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.ORDERBOOK,  # SIN timeframe (ADR-014).
)


def _snapshot_msg() -> str:
    return json.dumps(
        {
            "arg": {"channel": "books", "instId": "BTC-USDT"},
            "action": "snapshot",
            "data": [
                {
                    "bids": [["100.50", "2.0", "0", "1"], ["100.40", "1.0", "0", "1"]],
                    "asks": [["100.60", "1.5", "0", "1"]],
                    "ts": "1784073600000",
                    "checksum": 0,
                    "seqId": 123,
                    "prevSeqId": -1,
                }
            ],
        }
    )


def _update_msg() -> str:
    return json.dumps(
        {
            "arg": {"channel": "books", "instId": "BTC-USDT"},
            "action": "update",
            "data": [
                {
                    "bids": [["100.40", "5.0", "0", "1"]],
                    "asks": [["100.60", "0", "0", "0"]],
                    "ts": "1784073600100",
                    "checksum": 0,
                    "seqId": 124,
                    "prevSeqId": 123,
                }
            ],
        }
    )


def test_sub_arg_del_libro() -> None:
    assert OkxSpotConnector()._sub_arg(_OB_KEY) == {  # noqa: SLF001
        "channel": "books",
        "instId": "BTC-USDT",
    }


def test_el_snapshot_es_semilla_y_el_update_va_a_la_cola() -> None:
    connector = OkxSpotConnector()
    connector._encolar(_snapshot_msg())  # noqa: SLF001

    # El snapshot NO se encola como delta: es la foto de partida.
    assert connector.poll_deltas(0) == []
    seed = connector.seed(_OB_KEY)
    assert seed.exchange == "okx"
    assert seed.symbol == "BTC-USDT"
    assert seed.base_sequence == 123
    assert seed.bids == (("100.50", "2.0"), ("100.40", "1.0"))
    assert seed.asks == (("100.60", "1.5"),)

    connector._encolar(_update_msg())  # noqa: SLF001
    deltas = connector.poll_deltas(100)
    assert len(deltas) == 1
    delta = deltas[0]
    assert delta.seq_id == 124
    assert delta.prev_seq_id == 123
    assert delta.bids == (("100.40", "5.0"),)
    assert delta.asks == (("100.60", "0"),)  # tamano 0 = borrar (lo aplica el motor).
    # Nada se colo en velas ni trades: el multiplexado separa por 'channel'.
    assert connector.poll(0) == []
    assert connector.poll_trades(0) == []


def test_seed_sin_foto_todavia_lanza() -> None:
    # Recien suscrito: aun no llego el action=snapshot. seed() LANZA, no inventa libro.
    with pytest.raises(OkxTranslationError):
        OkxSpotConnector().seed(_OB_KEY)


def test_un_re_snapshot_marca_reconexion() -> None:
    connector = OkxSpotConnector()
    connector._encolar(_snapshot_msg())  # noqa: SLF001
    # La PRIMERA foto no es reconexion: es el arranque del stream.
    assert connector.drain_reconnected() == set()

    connector._encolar(_snapshot_msg())  # noqa: SLF001  # re-snapshot = reset.
    assert connector.drain_reconnected() == {_OB_KEY.as_stream_key()}
