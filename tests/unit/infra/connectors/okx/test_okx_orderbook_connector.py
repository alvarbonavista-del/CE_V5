"""Conector de OKX, cara de LIBRO: 2a conexion a /public, routing, semilla, reconexion.

TODO SIN RED. OKX movio 'candle' a /business pero dejo 'books' SOLO en /public: el libro
NO se puede multiplexar sobre la conexion de velas/trades (Tanda V, 60018). Por eso el
connector abre una 2a conexion DEDICADA a /public para books (Tanda VI). Aqui se prueba
lo que el CI SI caza sin abrir socket: el REPARTO por carril (libro -> /public;
velas/trades -> /business), el routing del mensaje de books a la cola, la semilla WS
(snapshot), y que un re-snapshot o una reconexion del libro marca su clave (re-siembra).
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from ce_v5.infra.connectors.okx.connector import (
    _WS_BASE,
    _WS_BASE_PUBLIC,
    OkxSpotConnector,
)
from ce_v5.infra.connectors.okx.translate import OkxTranslationError
from source.families.market import (
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    Timeframe,
)

_OB_KEY = MarketStreamKey(
    exchange="okx",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.ORDERBOOK,  # SIN timeframe (ADR-014).
)
_CANDLE_KEY = MarketStreamKey(
    exchange="okx",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.CANDLES,
    timeframe=Timeframe.M1,
)
_TRADE_KEY = MarketStreamKey(
    exchange="okx",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.TRADES,
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


# -- Tanda VI: 2 carriles (/business velas+trades, /public books) -----------


def test_el_libro_es_del_carril_public_y_no_del_business() -> None:
    connector = OkxSpotConnector()
    assert connector._es_books(_OB_KEY) is True  # noqa: SLF001
    assert connector._es_business(_OB_KEY) is False  # noqa: SLF001


def test_velas_y_trades_son_del_carril_business_y_no_del_libro() -> None:
    connector = OkxSpotConnector()
    for key in (_CANDLE_KEY, _TRADE_KEY):
        assert connector._es_business(key) is True  # noqa: SLF001
        assert connector._es_books(key) is False  # noqa: SLF001


def test_replanificar_reparte_cada_clase_a_su_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Sin red: se intercepta el arranque de lectores y se comprueba el REPARTO. El libro
    # va a /public; velas y trades siguen en /business, intactos.
    connector = OkxSpotConnector()
    llamadas: list[tuple[str, list[str]]] = []

    def _fake_arrancar(
        plan: Mapping[int, list[str]],
        endpoint: str,
        lectores: dict[int, object],
        conexiones: dict[int, object],
    ) -> None:
        claves = sorted(c for nombres in plan.values() for c in nombres)
        llamadas.append((endpoint, claves))

    monkeypatch.setattr(connector, "_arrancar_lectores", _fake_arrancar)  # noqa: SLF001
    connector._deseados = {  # noqa: SLF001
        k.as_stream_key(): k for k in (_CANDLE_KEY, _TRADE_KEY, _OB_KEY)
    }
    connector._replanificar()  # noqa: SLF001

    por_endpoint = dict(llamadas)
    assert por_endpoint[_WS_BASE] == sorted(
        [_CANDLE_KEY.as_stream_key(), _TRADE_KEY.as_stream_key()]
    )
    assert por_endpoint[_WS_BASE_PUBLIC] == [_OB_KEY.as_stream_key()]


def test_los_endpoints_de_cada_carril_son_distintos() -> None:
    assert _WS_BASE.endswith("/business")
    assert _WS_BASE_PUBLIC.endswith("/public")


def test_la_reconexion_del_carril_del_libro_marca_su_clave() -> None:
    # El lector de /public, al reconectar, llama _registrar_reconexion(sus claves): el
    # libro queda marcado para que el motor re-siembre (drain_reconnected). Mismo camino
    # que velas/trades en /business, pero por el socket del libro.
    connector = OkxSpotConnector()
    connector._registrar_reconexion((_OB_KEY,))  # noqa: SLF001
    assert connector.metrics.reconnections == 1
    assert connector.drain_reconnected() == {_OB_KEY.as_stream_key()}
