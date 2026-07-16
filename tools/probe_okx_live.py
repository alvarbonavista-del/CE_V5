"""Sonda de conectividad y streaming de OKX, previa al arnes con base (T-03).

Confirma que ESTA maquina ALCANZA el feed publico de OKX (REST y WebSocket) ANTES de
montar el arnes completo, que necesita Docker + los DSN. NO escribe en la base. Tres
comprobaciones:

  1. GET /api/v5/public/instruments?instType=SPOT (catalogo REST).
  2. GET /api/v5/market/candles (fetch_recent BTC-USDT 1m: bootstrap REST).
  3. WebSocket /ws/v5/business: abre el stream de BTC-USDT 1m y drena unos segundos
     para confirmar que llegan velas REALES (handshake, User-Agent, suscripcion,
     ping/pong y traduccion: todo el camino de red del connector, sin base).

Si algo falla (timeout, 4xx/451 geo-block, DNS), CAPTURA el error y termina != 0. OKX
restringe algunas jurisdicciones: un 451/403 REST es geo-block, no un bug.

REGLA DURA: el connector usa hilos daemon. La fase 3 los para con shutdown() en un
finally y es ACOTADA en el tiempo (_WS_PROBE_S). Nada de bucle infinito.

Uso: python tools/probe_okx_live.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import NoReturn
from urllib.error import HTTPError, URLError

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))
sys.path.insert(0, str(REPO_ROOT / "contracts"))

from ce_v5.infra.connectors.okx.connector import OkxSpotConnector  # noqa: E402
from source.families.market import (  # noqa: E402
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    Timeframe,
)

_BTC_1M = MarketStreamKey(
    exchange="okx",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.CANDLES,
    timeframe=Timeframe.M1,
)
_WS_PROBE_S = 20.0


def _fallar_red(contexto: str, exc: Exception) -> NoReturn:
    """Imprime un diagnostico CLARO segun el tipo de fallo y termina con codigo != 0."""
    if isinstance(exc, HTTPError):
        pista = " (posible geo-block de OKX)" if exc.code in (403, 451) else ""
        detalle = f"HTTP {exc.code} {exc.reason}{pista}"
    elif isinstance(exc, URLError):
        detalle = f"{type(exc).__name__}: {exc.reason} (DNS, conexion o timeout)"
    elif isinstance(exc, json.JSONDecodeError):
        detalle = "la respuesta no era JSON (pagina de error de un proxy o geo-block?)"
    else:
        detalle = f"{type(exc).__name__}: {exc}"
    print(f"FALLO alcanzando OKX en {contexto}: {detalle}.", file=sys.stderr)
    print(
        "No se reintenta a ciegas ni se usa otra via: si OKX no es alcanzable desde "
        "esta maquina, se dice y punto. Revisa red/DNS/geo antes de la validacion.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _rest_instruments(connector: OkxSpotConnector) -> None:
    print("[1/3] GET /api/v5/public/instruments?instType=SPOT ...")
    try:
        instrumentos = connector.list_instruments("spot")
    except (OSError, json.JSONDecodeError) as exc:
        _fallar_red("instruments (GET /api/v5/public/instruments)", exc)
    if not instrumentos:
        print("FALLO: instruments respondio SIN instrumentos.", file=sys.stderr)
        raise SystemExit(1)
    print(f"  instrumentos publicos: {len(instrumentos)}")
    print("  3 ejemplos (exchange / symbol canonico / native):")
    for inst in instrumentos[:3]:
        print(f"    - {inst.exchange} / {inst.symbol} / {inst.native_symbol}")


def _rest_candles(connector: OkxSpotConnector) -> None:
    print("\n[2/3] GET /api/v5/market/candles (fetch_recent BTC-USDT 1m, limit=3)...")
    try:
        velas = connector.fetch_recent(_BTC_1M, limit=3)
    except (OSError, json.JSONDecodeError) as exc:
        _fallar_red("candles (GET /api/v5/market/candles)", exc)
    if not velas:
        print("FALLO: candles respondio sin velas cerradas.", file=sys.stderr)
        raise SystemExit(1)
    print(f"  velas crudas cerradas recibidas: {len(velas)}")
    for vela in velas:
        print(
            f"    - open_time={vela.open_time_ms} close={vela.close} "
            f"is_closed={vela.is_closed}"
        )


def _ws_streaming(connector: OkxSpotConnector) -> None:
    print(f"\n[3/3] WS /ws/v5/business: streaming BTC-USDT 1m ({_WS_PROBE_S:.0f}s)...")
    connector.open(_BTC_1M)
    vistas = 0
    cerradas = 0
    ultimo = "-"
    fin = time.monotonic() + _WS_PROBE_S
    try:
        while time.monotonic() < fin:
            for vela in connector.poll(1000):
                vistas += 1
                ultimo = vela.close
                if vela.is_closed:
                    cerradas += 1
    finally:
        connector.shutdown()
    if vistas == 0:
        print(
            "FALLO: no llego NI UNA vela por WebSocket. Revisa el handshake WS "
            "(User-Agent/Cloudflare) o geo.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(f"  velas por WS: {vistas} (cerradas: {cerradas}); ultimo precio: {ultimo}")


def main() -> None:
    connector = OkxSpotConnector()
    print("Sonda de OKX (REST + WebSocket; NO toca la base).\n")
    _rest_instruments(connector)
    _rest_candles(connector)
    _ws_streaming(connector)
    print("\nSONDA OK: OKX alcanzable por REST y WebSocket. Listo para el arnes.")


if __name__ == "__main__":
    main()
