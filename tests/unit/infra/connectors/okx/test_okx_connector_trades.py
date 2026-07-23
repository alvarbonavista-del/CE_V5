"""Conector de OKX, cara de TRADES: paginacion del backfill, enrutado y reconexion.

TODO SIN RED. El socket real se valida en caliente (regla 5.18). Aqui se prueba la
logica que el CI SI puede cazar:
- el bucle de backfill PAGINA con &after hacia atras hasta cubrir o hasta el tope de
  esfuerzo, y NO asume haber recibido mas de lo que cada pagina trajo (cap de 300);
- el enrutado por canal: 'trades-all' va a la cola de trades y las velas a la suya;
- la reconexion marca TAMBIEN la clave de trades (conexion multiplexada).

El REST se sustituye por un fake determinista que sirve un historico contiguo de ids; el
_get_json real (IO) es lo unico que no se ejerce, a proposito.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable

from ce_v5.infra.connectors.okx.connector import (
    _BACKFILL_MAX_PAGES,
    OkxConfig,
    OkxSpotConnector,
)
from source.families.market import (
    LastSeenTrade,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    Timeframe,
)

_EVENT_BASE = 1_700_000_000_000

_TRADES_KEY = MarketStreamKey(
    exchange="okx",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.TRADES,  # SIN timeframe: el contrato lo prohibe (ADR-014).
)
_CANDLE_KEY = MarketStreamKey(
    exchange="okx",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.CANDLES,
    timeframe=Timeframe.M1,
)


def _sin_pausa() -> OkxSpotConnector:
    # pausa=0: en frio no hay rate limit de OKX que respetar.
    return OkxSpotConnector(OkxConfig(backfill_page_pause_s=0.0))


def _rest_historico(
    newest_id: int, page_size: int, floor_id: int, calls: list[int | None]
) -> Callable[[str], object]:
    """Fake de _get_json: un historico CONTIGUO descendente servido por paginas.

    Reproduce el comportamiento real de OKX: newest-first, y &after=<id> devuelve los
    ANTERIORES a ese id. page_size modela el tamano de pagina que OKX entrega (el cap de
    300 en produccion). El connector SIEMPRE pide limit=300; el fake lo verifica.
    """

    def _get_json(path: str) -> object:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
        # El connector pide EXACTAMENTE 300 (el cap silencioso de OKX): nunca mas.
        assert qs["limit"] == ["300"]
        assert qs["type"] == ["1"]
        after = int(qs["after"][0]) if "after" in qs else None
        calls.append(after)
        top = newest_id if after is None else after - 1
        ids = [i for i in range(top, top - page_size, -1) if i >= floor_id]
        data = [
            {
                "instId": "BTC-USDT",
                "tradeId": str(i),
                "px": "66000.0",
                "sz": "0.01",
                "side": "buy" if i % 2 == 0 else "sell",
                "ts": str(_EVENT_BASE + i),
            }
            for i in ids
        ]
        return {"code": "0", "data": data}

    return _get_json


def test_una_pagina_basta_cuando_el_hueco_es_pequeno() -> None:
    connector = _sin_pausa()
    calls: list[int | None] = []
    connector._get_json = _rest_historico(1000, 300, 1, calls)  # type: ignore[assignment]  # noqa: SLF001

    # last_seen=800: el hueco (800..1000) cabe en una pagina de 300.
    resultado = connector.backfill_after_reconnect(
        _TRADES_KEY, LastSeenTrade(trade_id="800", event_time_ms=_EVENT_BASE + 800)
    )

    assert calls == [None]  # una sola peticion
    assert resultado.covered is True
    assert resultado.gap_from_event_time_ms is None


def test_pagina_hacia_atras_hasta_cubrir_el_hueco_grande() -> None:
    # PASO 6: el hueco es MAYOR que una pagina (cap de 300). El connector NO da el hueco
    # por cubierto tras la primera pagina: pagina con &after hasta empalmar.
    connector = _sin_pausa()
    calls: list[int | None] = []
    connector._get_json = _rest_historico(1000, 300, 1, calls)  # type: ignore[assignment]  # noqa: SLF001

    resultado = connector.backfill_after_reconnect(
        _TRADES_KEY, LastSeenTrade(trade_id="150", event_time_ms=_EVENT_BASE + 150)
    )

    # Tres paginas: after avanza por el id mas antiguo REALMENTE devuelto (701, 401), no
    # por un numero asumido. Prueba que respeta el cap y no inventa cobertura.
    assert calls == [None, 701, 401]
    assert resultado.covered is True
    assert resultado.gap_from_event_time_ms is None
    # Se acumularon los trades de las tres paginas (con solape del ultimo tramo).
    assert len(resultado.raw_trades) == 900


def test_primera_conexion_no_pagina_y_no_hay_hueco() -> None:
    connector = _sin_pausa()
    calls: list[int | None] = []
    connector._get_json = _rest_historico(1000, 300, 1, calls)  # type: ignore[assignment]  # noqa: SLF001

    resultado = connector.backfill_after_reconnect(
        _TRADES_KEY, LastSeenTrade(trade_id=None, event_time_ms=None)
    )

    # Sin nada persistido no hay hueco que perseguir: una sola pagina y a otra cosa.
    assert calls == [None]
    assert resultado.covered is True


def test_el_tope_de_esfuerzo_declara_hueco_fail_safe() -> None:
    # Un corte tan largo que el relleno no alcanza lo que teniamos ni en el tope de
    # paginas: se RINDE y declara el hueco (fail-safe), no lo da por cubierto.
    connector = _sin_pausa()
    calls: list[int | None] = []
    # Historico enorme y paginas pequenas: nunca se alcanza el target dentro del tope.
    connector._get_json = _rest_historico(1_000_000, 5, 1, calls)  # type: ignore[assignment]  # noqa: SLF001

    resultado = connector.backfill_after_reconnect(
        _TRADES_KEY, LastSeenTrade(trade_id="10", event_time_ms=_EVENT_BASE + 10)
    )

    assert len(calls) == _BACKFILL_MAX_PAGES  # se agoto el esfuerzo
    assert resultado.covered is False
    # El hueco queda acotado: desde lo ultimo que teniamos hasta lo mas antiguo visto.
    assert resultado.gap_from_event_time_ms == _EVENT_BASE + 10
    assert resultado.gap_to_event_time_ms is not None


def test_sub_arg_de_trades_usa_el_canal_trades_all() -> None:
    connector = _sin_pausa()
    assert connector._sub_arg(_TRADES_KEY) == {  # noqa: SLF001
        "channel": "trades-all",
        "instId": "BTC-USDT",
    }
    # Regresion: las velas siguen usando su canal candle<bar>.
    assert connector._sub_arg(_CANDLE_KEY) == {  # noqa: SLF001
        "channel": "candle1m",
        "instId": "BTC-USDT",
    }


def test_la_reconexion_marca_tambien_la_clave_de_trades() -> None:
    connector = _sin_pausa()
    # Poblar _deseados A MANO: open() arrancaria un hilo lector (red).
    connector._deseados[_TRADES_KEY.as_stream_key()] = _TRADES_KEY  # noqa: SLF001
    connector._deseados[_CANDLE_KEY.as_stream_key()] = _CANDLE_KEY  # noqa: SLF001

    connector._registrar_reconexion((_TRADES_KEY, _CANDLE_KEY))  # noqa: SLF001

    assert connector.metrics.reconnections == 1
    # Una conexion multiplexada que reconecta deja hueco de velas Y de trades: se marcan
    # las dos, y cada motor filtra de drain_reconnected lo que le toca.
    assert connector.drain_reconnected() == {
        _TRADES_KEY.as_stream_key(),
        _CANDLE_KEY.as_stream_key(),
    }


def test_encolar_enruta_trades_a_su_cola_y_velas_a_la_suya() -> None:
    connector = _sin_pausa()

    mensaje_trade = json.dumps(
        {
            "arg": {"channel": "trades-all", "instId": "BTC-USDT"},
            "data": [
                {
                    "instId": "BTC-USDT",
                    "tradeId": "555",
                    "px": "66000.0",
                    "sz": "0.02",
                    "side": "sell",
                    "ts": str(_EVENT_BASE + 555),
                }
            ],
        }
    )
    connector._encolar(mensaje_trade)  # noqa: SLF001

    trades = connector.poll_trades(100)
    assert [t.trade_id for t in trades] == ["555"]
    assert trades[0].aggressor_side == "sell"
    # No se colo nada en la cola de velas.
    assert connector.poll(0) == []


def test_encolar_velas_sigue_yendo_a_su_cola() -> None:
    connector = _sin_pausa()
    # [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    row = [
        "1700000040000",
        "66000",
        "66100",
        "65900",
        "66050",
        "12",
        "1",
        "1",
        "1",
    ]
    mensaje_vela = json.dumps(
        {"arg": {"channel": "candle1m", "instId": "BTC-USDT"}, "data": [row]}
    )
    connector._encolar(mensaje_vela)  # noqa: SLF001

    velas = connector.poll(100)
    assert [v.symbol for v in velas] == ["BTC-USDT"]
    assert connector.poll_trades(0) == []
