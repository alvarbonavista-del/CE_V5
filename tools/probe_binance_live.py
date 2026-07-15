"""Sonda de conectividad previa a la validacion en caliente B12b.

Confirma que ESTA maquina ALCANZA el feed publico de Binance ANTES de montar el arnes de
streaming. Es una sonda REST de solo LECTURA: NO abre el WebSocket y NO escribe en la
base. Dos comprobaciones:

  1. GET /api/v3/exchangeInfo (list_instruments): cuantos instrumentos publicos hay y 3
     ejemplos (exchange / symbol canonico / native).
  2. GET /api/v3/klines (fetch_recent de BTC-USDT 1m, limit=3): las 3 velas crudas del
     bootstrap REST, para confirmar que el rellenado tras reconexion funcionaria.

Si algo falla (timeout, 4xx/451 geo-block, DNS), CAPTURA el error, imprime un mensaje
CLARO con su tipo y termina con codigo != 0. NO reintenta a ciegas ni usa otra via de
red: si Binance no es alcanzable desde aqui, se dice y punto.

Uso: python tools/probe_binance_live.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import NoReturn
from urllib.error import HTTPError, URLError

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))
sys.path.insert(0, str(REPO_ROOT / "contracts"))

from ce_v5.infra.connectors.binance.connector import BinanceSpotConnector  # noqa: E402
from source.families.market import (  # noqa: E402
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    Timeframe,
)

_BTC_1M = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.CANDLES,
    timeframe=Timeframe.M1,
)


def _fallar_red(contexto: str, exc: Exception) -> NoReturn:
    """Imprime un diagnostico CLARO segun el tipo de fallo y termina con codigo != 0."""
    if isinstance(exc, HTTPError):
        pista = " (posible geo-block de Binance)" if exc.code in (403, 451) else ""
        detalle = f"HTTP {exc.code} {exc.reason}{pista}"
    elif isinstance(exc, URLError):
        detalle = f"{type(exc).__name__}: {exc.reason} (DNS, conexion o timeout)"
    elif isinstance(exc, json.JSONDecodeError):
        detalle = "la respuesta no era JSON (pagina de error de un proxy o geo-block?)"
    else:
        detalle = f"{type(exc).__name__}: {exc}"
    print(f"FALLO alcanzando Binance en {contexto}: {detalle}.", file=sys.stderr)
    print(
        "No se reintenta a ciegas ni se usa otra via: si Binance no es alcanzable "
        "desde esta maquina, se dice y punto. Revisa red/DNS/geo antes de B12b.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def main() -> None:
    connector = BinanceSpotConnector()
    print("Sonda REST de Binance (solo lectura; NO abre WebSocket ni toca la base).\n")

    # 1) exchangeInfo. OSError cubre URLError/HTTPError/timeout/SSL; el JSON invalido va
    #    aparte. NO se abre ningun socket de streaming: esto es solo REST de lectura.
    print("[1/2] GET /api/v3/exchangeInfo (list_instruments spot)...")
    try:
        instrumentos = connector.list_instruments("spot")
    except (OSError, json.JSONDecodeError) as exc:
        _fallar_red("exchangeInfo (GET /api/v3/exchangeInfo)", exc)

    if not instrumentos:
        print(
            "FALLO: exchangeInfo respondio pero SIN instrumentos. No es un fallo de "
            "red, pero un catalogo vacio hace que el connector descarte todo: revisar "
            "antes de B12b.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(f"  instrumentos publicos: {len(instrumentos)}")
    print("  3 ejemplos (exchange / symbol canonico / native):")
    for inst in instrumentos[:3]:
        print(f"    - {inst.exchange} / {inst.symbol} / {inst.native_symbol}")

    # 2) klines: el bootstrap REST real (el que rellena el hueco tras una reconexion).
    print("\n[2/2] GET /api/v3/klines (fetch_recent BTC-USDT 1m, limit=3)...")
    try:
        velas = connector.fetch_recent(_BTC_1M, limit=3)
    except (OSError, json.JSONDecodeError) as exc:
        _fallar_red("klines (GET /api/v3/klines)", exc)

    if not velas:
        print(
            "FALLO: klines respondio pero sin velas para BTC-USDT 1m. Inesperado en un "
            "par tan liquido: revisar antes de B12b.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(f"  velas crudas recibidas: {len(velas)}")
    for vela in velas:
        print(
            f"    - open_time={vela.open_time_ms} close={vela.close} "
            f"is_closed={vela.is_closed}"
        )

    print(
        "\nSONDA OK: Binance es alcanzable desde esta maquina (REST). Listo para B12b."
    )


if __name__ == "__main__":
    main()
