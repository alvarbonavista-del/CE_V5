"""Validacion en caliente REAL de P07b 3b-1: footprint agregado de trades REALES.

Ingiere TRADES REALES de un exchange en market_trade (con la maquinaria de la 3a:
TradeIngestionEngine + PostgresTradeWriter), y luego AGREGA el footprint de una barra
cerrada con la maquinaria de la 3b-1 (FootprintEngine + PostgresFootprintWriter), contra
la base LOCAL de juguete. Demuestra, end-to-end y con dato real, lo que un fake no ve:

- (a) UNA VELA CERRADA -> footprint_closed persistido Y ENCOLADO en la MISMA transaccion
      (ADR-013), con celdas por PRECIO EXACTO cuadradas con los trades reales.
- (b) REPRODUCIBILIDAD BIT A BIT: re-agregar la MISMA ventana produce el MISMO payload,
      byte a byte, y re-emitir la misma vela NO duplica (dedup por idempotency_key).
- (c) UN HUECO que se solapa con la ventana marca is_complete=False (fail-safe),
      leyendo el hueco de market_trade_gap REAL.

SANDBOX LOCAL, MARKET DATA PUBLICA REAL, JAMAS DINERO. Feed sin credenciales, TLS
verificado. "Nunca dinero real" es de ejecucion/ordenes (M5), no de dato publico.

QUE SE PRUEBA AQUI Y NO EN EL CI (regla 5.18): el CI es hermetico y no abre un socket.
La agregacion pura y el writer atomico ya los cubre el CI a fondo (tests en frio); lo
que solo se ve aqui es el ciclo COMPLETO sobre trades REALES de un par liquido: que las
celdas por precio exacto de un minuto de mercado real cuadran, y que el footprint sale
reproducible bit a bit del dato vivo.

is_complete de la barra CAPTURADA es True por definicion del sistema: mide si un HUECO
de reconexion (market_trade_gap) se solapa, no si vimos el minuto entero de reloj. Sin
reconexion no hay hueco; el check (c) provoca uno a proposito para ver el False.

REGLA DURA (leccion del bloqueo de terminal): el connector usa un HILO DE FONDO. Este
arnes lo cierra (shutdown) en un finally PASE LO QUE PASE, y es ACOTADO EN EL TIEMPO:
ventana fija, termina SOLO. Nada de bucle infinito.

Uso: python tools/validate_footprint_live.py
Requiere CE_V5_MIGRATIONS_DATABASE_URL y CE_V5_INGESTION_DATABASE_URL. Si hay Redis,
ademas drena la outbox y confirma el footprint_closed EN EL BUS.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))
sys.path.insert(0, str(REPO_ROOT / "contracts"))

from ce_v5.core.clock import SystemClock  # noqa: E402
from ce_v5.entrypoints.worker_footprint.composition import (  # noqa: E402
    _TradeReaderOnDb,
)
from ce_v5.entrypoints.worker_ingestion.catalog_sync import sync_catalog  # noqa: E402
from ce_v5.infra.bus_redis import (  # noqa: E402
    RedisBusConfig,
    RedisEventBus,
    create_client,
)
from ce_v5.infra.connectors.binance.connector import (  # noqa: E402
    BinanceSpotConnector,
)
from ce_v5.infra.connectors.bybit.connector import BybitSpotConnector  # noqa: E402
from ce_v5.infra.connectors.okx.connector import OkxSpotConnector  # noqa: E402
from ce_v5.infra.db.config import (  # noqa: E402
    INGESTION_DSN_ENV_VAR,
    MIGRATIONS_DSN_ENV_VAR,
    DbConfig,
    IngestionDbConfig,
)
from ce_v5.infra.db.market_footprint import PostgresFootprintWriter  # noqa: E402
from ce_v5.infra.db.market_store import PostgresInstrumentCatalog  # noqa: E402
from ce_v5.infra.db.market_trades import PostgresTradeWriter  # noqa: E402
from ce_v5.infra.db.outbox_publisher import OutboxPublisher, topic_for  # noqa: E402
from ce_v5.infra.db.ports import Database  # noqa: E402
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase  # noqa: E402
from ce_v5.platform.market.footprint_aggregate import (  # noqa: E402
    FootprintStreamIdentity,
    aggregate_footprint,
)
from ce_v5.platform.market.footprint_ingestor import FootprintEngine  # noqa: E402
from ce_v5.platform.market.trade_ingestor import TradeIngestionEngine  # noqa: E402
from source.families.footprint import (  # noqa: E402
    MarketFootprintEventType,
    MarketTrade,
)
from source.families.market import (  # noqa: E402
    CandleClosedPayload,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    Timeframe,
)
from source.time import MaturityState  # noqa: E402

# El footprint es AGNOSTICO del exchange: agrega los trades persistidos, vengan de donde
# vengan. Por eso el exchange es parametrizable: en una maquina con geo-block de Binance
# se valida igual contra OKX o Bybit (sus conectores de trades ya se validaron en vivo).
_EXCHANGE_ENV = "CE_V5_LIVE_FOOTPRINT_EXCHANGE"
_EXCHANGE = os.environ.get(_EXCHANGE_ENV, "binance").strip() or "binance"
_MARKET_TYPE = "spot"
_SYMBOL = "BTC-USDT"
_TF = Timeframe.M1

_CONECTORES = {
    "binance": BinanceSpotConnector,
    "bybit": BybitSpotConnector,
    "okx": OkxSpotConnector,
}

_CLAVE_TRADES = MarketStreamKey(
    exchange=_EXCHANGE,
    market_type=MarketType.SPOT,
    symbol=_SYMBOL,
    data_kind=MarketDataKind.TRADES,  # SIN timeframe: el contrato lo prohibe (ADR-014).
)
_IDENTITY = FootprintStreamIdentity(
    exchange=_EXCHANGE,
    market_type=MarketType.SPOT,
    symbol=_SYMBOL,
    timeframe=_TF,
)
_FOOTPRINT_STREAM_KEY = _IDENTITY.footprint_stream_key()

_VENTANA_ENV = "CE_V5_LIVE_FOOTPRINT_WINDOW_S"
_OBJETIVO_ENV = "CE_V5_LIVE_FOOTPRINT_TARGET"
_DEFAULT_VENTANA_S = 25.0
_DEFAULT_OBJETIVO = 200
_PAUSA_S = 0.5
_METRICS_EVERY_S = 5.0


class _CatalogoEnIngesta:
    """Catalogo por-sesion con el rol de INGESTA (la escritura del catalogo solo la
    permite ese rol, regla 5.20). Satisface CatalogWriterPort (patron de _CatalogOnDb).
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    def upsert(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        native_symbol: str,
        status: str = "active",
    ) -> None:
        with self._database.transaction() as session:
            PostgresInstrumentCatalog(session).upsert(
                exchange, market_type, symbol, native_symbol, status
            )

    def deactivate_missing(
        self, exchange: str, market_type: str, present_symbols: list[str]
    ) -> int:
        with self._database.transaction() as session:
            return PostgresInstrumentCatalog(session).deactivate_missing(
                exchange, market_type, present_symbols
            )


def _solo(*claves: str) -> dict[str, str]:
    """El sub-entorno con SOLO esas variables (guardia 5.20 hecha explicita)."""
    return {clave: os.environ[clave] for clave in claves if clave in os.environ}


def _exigir_env() -> None:
    """Falla RUIDOSO si falta un DSN obligatorio. No se salta (regla 5.18)."""
    faltan = [
        var
        for var in (MIGRATIONS_DSN_ENV_VAR, INGESTION_DSN_ENV_VAR)
        if not os.environ.get(var, "").strip()
    ]
    if faltan:
        print(
            "FALLO: faltan variables obligatorias para la validacion en caliente del "
            f"footprint: {', '.join(faltan)}. El rol de INGESTA es el que escribe "
            "market_footprint (regla 5.20) y el de migraciones el unico que limpia el "
            "sandbox append-only. No se salta, se configura el entorno.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _entero(valor: object) -> int:
    if not isinstance(valor, int):
        msg = f"Se esperaba un entero de la base y llego {type(valor)!r}."
        raise TypeError(msg)
    return valor


# -- Limpieza (rol propietario: el historico es append-only) -------------------


def _limpiar(owner_db: Database) -> None:
    """Borra trades, huecos, footprint y outbox de demo. EXIGE el rol de MIGRACIONES:
    market_trade/gap/footprint son append-only y revocan el DELETE a los roles runtime.
    """
    with owner_db.transaction() as session:
        for tabla in ("market_footprint", "market_trade_gap", "market_trade"):
            session.execute(
                f"DELETE FROM {tabla} "  # noqa: S608 - tabla literal, no hay input.
                "WHERE exchange = %s AND market_type = %s AND symbol = %s",
                (_EXCHANGE, _MARKET_TYPE, _SYMBOL),
            )
        session.execute(
            "DELETE FROM outbox WHERE stream_key IN (%s, %s)",
            (_CLAVE_TRADES.as_stream_key(), _FOOTPRINT_STREAM_KEY),
        )


# -- Ingesta de trades reales --------------------------------------------------


def _ingerir_trades(
    engine: TradeIngestionEngine, ventana_s: float, objetivo: int
) -> None:
    """Drena el socket real hasta agotar la ventana o alcanzar el objetivo de trades."""
    print(f"\n=== FASE 2: streaming REAL de trades (ventana {ventana_s:.0f}s) ===")
    fin = time.monotonic() + ventana_s
    ultimo_print = 0.0
    while time.monotonic() < fin:
        engine.drain_once()
        if engine.metrics.trades_persisted >= objetivo:
            print(f"  objetivo de {objetivo} trades alcanzado; se corta la ventana.")
            break
        ahora = time.monotonic()
        if ahora - ultimo_print >= _METRICS_EVERY_S:
            print(f"  [metricas] persistidos={engine.metrics.trades_persisted}")
            ultimo_print = ahora
        time.sleep(_PAUSA_S)
    print(f"  persistidos en total: {engine.metrics.trades_persisted}")


def _bucket(event_time: int) -> int:
    """El inicio de la barra M1 que contiene event_time: floor(t/tf)*tf (UTC)."""
    dur = _TF.duration_ms
    return (event_time // dur) * dur


def _elegir_ventana(reader_db: Database) -> int | None:
    """Elige el open_time de una barra M1 con trades: el bucket del trade MEDIANO.

    El bucket del mediano evita los extremos (el primer y ultimo minuto, que la ventana
    de ingesta pudo cortar por la mitad) y cae en un minuto con actividad de verdad.
    """
    with reader_db.transaction() as session:
        rows = session.fetchall(
            "SELECT event_time FROM market_trade "
            "WHERE exchange = %s AND market_type = %s AND symbol = %s "
            "ORDER BY event_time",
            (_EXCHANGE, _MARKET_TYPE, _SYMBOL),
        )
    if not rows:
        return None
    tiempos = [_entero(r[0]) for r in rows]
    return _bucket(tiempos[len(tiempos) // 2])


def _candle_de_la_ventana(
    trades: Sequence[MarketTrade], open_time: int
) -> CandleClosedPayload:
    """Una vela CERRADA HONESTA de la ventana, construida de sus trades reales.

    open = precio del primer trade por (event_time, trade_id); close = el del ultimo;
    high/low = extremos de precio; volume = suma de tamanos. No se inventa nada: es la
    vela que esos trades dibujan. Es el disparador del footprint (Opcion A).
    """
    ordenados = sorted(trades, key=lambda t: (t.event_time, t.trade_id))
    precios = [t.price for t in ordenados]
    return CandleClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange=_EXCHANGE,
        market_type=MarketType.SPOT,
        symbol=_SYMBOL,
        timeframe=_TF,
        open_time=open_time,
        close_time=open_time + _TF.duration_ms,
        open=ordenados[0].price,
        high=max(precios),
        low=min(precios),
        close=ordenados[-1].price,
        volume=sum((t.qty for t in ordenados), Decimal(0)),
    )


# -- Lectura del footprint persistido ------------------------------------------


def _footprint_persistido(
    reader_db: Database, open_time: int
) -> tuple[int, str, bool] | None:
    """(trade_count, cells_json, is_complete) del footprint_closed, o None si falta."""
    idem = "|".join(
        [
            MarketFootprintEventType.FOOTPRINT_CLOSED.value,
            _FOOTPRINT_STREAM_KEY,
            str(open_time),
            MaturityState.CLOSED.value,
        ]
    )
    with reader_db.transaction() as session:
        row = session.fetchone(
            "SELECT trade_count, cells, is_complete FROM market_footprint "
            "WHERE idempotency_key = %s",
            (idem,),
        )
    if row is None:
        return None
    cells = row[1] if isinstance(row[1], str) else json.dumps(row[1])
    return _entero(row[0]), cells, bool(row[2])


def _contar(reader_db: Database, sql: str, params: tuple[object, ...]) -> int:
    with reader_db.transaction() as session:
        row = session.fetchone(sql, params)
    return 0 if row is None else _entero(row[0])


# -- Comprobaciones ------------------------------------------------------------


def _check_cierre(
    engine: FootprintEngine,
    reader: _TradeReaderOnDb,
    reader_db: Database,
    open_time: int,
    candle: CandleClosedPayload,
) -> tuple[bool, str]:
    """(a) La vela cierra -> footprint_closed persistido+encolado, cuadrado con trades.

    Devuelve tambien el JSON del payload agregado (para el check de reproducibilidad).
    """
    print("\n=== COMPROBACION (a): vela cerrada -> footprint_closed ===")
    window_end = open_time + _TF.duration_ms
    trades = reader.trades_in_window(
        _EXCHANGE, _MARKET_TYPE, _SYMBOL, open_time, window_end
    )
    gaps = reader.overlapping_gaps(
        _EXCHANGE, _MARKET_TYPE, _SYMBOL, open_time, window_end
    )
    esperado = aggregate_footprint(
        _IDENTITY,
        open_time,
        candle.close_time,
        trades,
        gaps,
        maturity_state=MaturityState.CLOSED,
    )
    print(
        f"  ventana [{open_time}, {window_end}): {len(trades)} trades reales, "
        f"{len(esperado.cells)} celdas por precio exacto, "
        f"delta_barra={esperado.bar_delta}"
    )

    engine.on_candle_closed(candle, event_time=open_time)

    persistido = _footprint_persistido(reader_db, open_time)
    if persistido is None:
        print("  [FALLO] no se persistio el footprint_closed de la ventana.")
        return False, ""
    trade_count, cells_json, is_complete = persistido
    filas_fp = _contar(
        reader_db,
        "SELECT count(*) FROM market_footprint WHERE stream_key = %s",
        (_FOOTPRINT_STREAM_KEY,),
    )
    filas_outbox = _contar(
        reader_db,
        "SELECT count(*) FROM outbox WHERE stream_key = %s AND event_type = %s",
        (_FOOTPRINT_STREAM_KEY, MarketFootprintEventType.FOOTPRINT_CLOSED.value),
    )
    print(
        f"  persistido: trade_count={trade_count} celdas={len(json.loads(cells_json))} "
        f"is_complete={is_complete} | filas footprint={filas_fp} outbox={filas_outbox}"
    )

    celdas_esperadas = json.loads(
        json.dumps(
            [
                {
                    "price": str(c.price),
                    "buy_volume": str(c.buy_volume),
                    "sell_volume": str(c.sell_volume),
                    "delta": str(c.delta),
                }
                for c in esperado.cells
            ]
        )
    )
    ok = (
        trade_count == esperado.trade_count
        and json.loads(cells_json) == celdas_esperadas
        and filas_fp == 1
        and filas_outbox == 1  # ATOMICO: una fila en cada tabla, la misma clave.
    )
    marca = "[OK]" if ok else "[FALLO]"
    print(
        f"  {marca} footprint_closed atomico (historico+outbox) y sus celdas cuadran "
        "con la agregacion independiente de los MISMOS trades reales."
    )
    return ok, esperado.model_dump_json()


def _check_reproducibilidad(
    engine: FootprintEngine,
    reader: _TradeReaderOnDb,
    reader_db: Database,
    open_time: int,
    candle: CandleClosedPayload,
    json_1: str,
) -> bool:
    """(b) Re-agregar la misma ventana da el MISMO payload; re-emitir NO duplica."""
    print("\n=== COMPROBACION (b): reproducibilidad bit a bit ===")
    window_end = open_time + _TF.duration_ms
    trades = reader.trades_in_window(
        _EXCHANGE, _MARKET_TYPE, _SYMBOL, open_time, window_end
    )
    gaps = reader.overlapping_gaps(
        _EXCHANGE, _MARKET_TYPE, _SYMBOL, open_time, window_end
    )
    # Re-leidos y en orden inverso: la agregacion es conmutativa, el orden no cuenta.
    json_2 = aggregate_footprint(
        _IDENTITY,
        open_time,
        candle.close_time,
        list(reversed(list(trades))),
        gaps,
        maturity_state=MaturityState.CLOSED,
    ).model_dump_json()
    identico = json_1 == json_2
    print(
        f"  {'[OK]' if identico else '[FALLO]'} mismo payload byte a byte re-agregando."
    )

    dup_antes = engine.metrics.duplicates_skipped
    filas_antes = _contar(
        reader_db,
        "SELECT count(*) FROM market_footprint WHERE stream_key = %s",
        (_FOOTPRINT_STREAM_KEY,),
    )
    engine.on_candle_closed(candle, event_time=open_time)
    filas_despues = _contar(
        reader_db,
        "SELECT count(*) FROM market_footprint WHERE stream_key = %s",
        (_FOOTPRINT_STREAM_KEY,),
    )
    dedup = engine.metrics.duplicates_skipped - dup_antes
    sin_duplicar = filas_despues == filas_antes and dedup == 1
    print(
        f"  {'[OK]' if sin_duplicar else '[FALLO]'} re-emitir la misma vela NO duplico "
        f"(filas {filas_antes}->{filas_despues}, dedup=+{dedup})."
    )
    return identico and sin_duplicar


def _check_hueco(
    reader: _TradeReaderOnDb,
    ingestion_db: Database,
    open_time: int,
    candle: CandleClosedPayload,
) -> bool:
    """(c) Un hueco que se solapa con la ventana marca is_complete=False (fail-safe)."""
    print("\n=== COMPROBACION (c): hueco solapado -> is_complete=False ===")
    window_end = open_time + _TF.duration_ms

    # Un hueco REAL en market_trade_gap, escrito por el rol de INGESTA, DENTRO de la
    # ventana. No se falsea dato de trades: solo se declara "aqui falto informacion".
    hueco_from = open_time + 10
    hueco_to = open_time + 20
    escrito = PostgresTradeWriter(ingestion_db).record_gap(
        _EXCHANGE, _MARKET_TYPE, _SYMBOL, hueco_from, hueco_to
    )
    print(f"  hueco declarado [{hueco_from}, {hueco_to}] (record_gap -> {escrito})")

    trades = reader.trades_in_window(
        _EXCHANGE, _MARKET_TYPE, _SYMBOL, open_time, window_end
    )
    gaps = reader.overlapping_gaps(
        _EXCHANGE, _MARKET_TYPE, _SYMBOL, open_time, window_end
    )
    payload = aggregate_footprint(
        _IDENTITY,
        open_time,
        candle.close_time,
        trades,
        gaps,
        maturity_state=MaturityState.CLOSED,
    )
    ok = len(gaps) >= 1 and payload.is_complete is False
    marca = "[OK]" if ok else "[FALLO]"
    print(f"  huecos que solapan: {len(gaps)}; is_complete={payload.is_complete}")
    print(
        f"  {marca} el hueco solapado marca la barra INCOMPLETA (fail-safe): le "
        "faltan trades y sus celdas no son la verdad completa del mercado."
    )
    return ok


def _check_bus(ingestion_db: Database) -> bool | None:
    """OPCIONAL: si hay Redis, drena la outbox y confirma el footprint EN EL BUS."""
    url = os.environ.get("CE_V5_REDIS_URL", "").strip()
    if not url:
        print("\n=== BUS: omitido (sin CE_V5_REDIS_URL) ===")
        return None
    print("\n=== COMPROBACION (bus): el footprint_closed sale al bus ===")
    import uuid

    config = RedisBusConfig(url=url, namespace="live-fp-" + uuid.uuid4().hex)
    client = create_client(config)
    try:
        bus = RedisEventBus(client, config)
        publisher = OutboxPublisher(db=ingestion_db, bus=bus)
        publicados = publisher.drain_once()
        topic = topic_for(MarketFootprintEventType.FOOTPRINT_CLOSED.value)
        bus.ensure_group(topic, "g1")
        recibidos = bus.poll(topic, "g1", "c1", max_messages=10, block_ms=0)
        cerrados = [
            r
            for r in recibidos
            if json.loads(r.message.envelope)["event_type"]
            == MarketFootprintEventType.FOOTPRINT_CLOSED.value
        ]
        ok = publicados >= 1 and len(cerrados) >= 1
        if cerrados:
            env = json.loads(cerrados[0].message.envelope)
            print(
                f"  publicado en el topic '{topic}': event_time={env['event_time']} "
                f"open_time={env['payload']['open_time']} "
                f"celdas={len(env['payload']['cells'])}"
            )
        print(
            f"  {'[OK]' if ok else '[FALLO]'} el publisher (INGESTA) valido el sobre "
            f"contra CA-06 y saco {publicados} evento(s) al bus."
        )
        return ok
    finally:
        for key in client.scan_iter(match=f"{config.namespace}:*"):
            client.delete(key)
        client.close()


# -- Orquestacion --------------------------------------------------------------


def main() -> None:
    _exigir_env()
    ventana_s = float(os.environ.get(_VENTANA_ENV, str(_DEFAULT_VENTANA_S)))
    objetivo = int(os.environ.get(_OBJETIVO_ENV, str(_DEFAULT_OBJETIVO)))

    migrations_db = PsycopgDatabase(DbConfig.migrations_from_env())
    ingestion_db = PsycopgDatabase(
        DbConfig(dsn=IngestionDbConfig.from_env(_solo(INGESTION_DSN_ENV_VAR)).dsn)
    )

    factory = _CONECTORES.get(_EXCHANGE)
    if factory is None:
        print(
            f"FALLO: exchange {_EXCHANGE!r} no soportado. Usa uno de "
            f"{sorted(_CONECTORES)} via {_EXCHANGE_ENV}.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    connector = factory()
    trade_engine = TradeIngestionEngine(
        source=connector, writer=PostgresTradeWriter(ingestion_db)
    )
    reader = _TradeReaderOnDb(ingestion_db)
    footprint_engine = FootprintEngine(
        reader=reader,
        writer=PostgresFootprintWriter(ingestion_db),
        clock=SystemClock(),
        component_source="validate_footprint_live",
    )

    checks: dict[str, bool] = {}
    try:
        print(
            f"Validacion en caliente REAL del footprint (P07b 3b-1). Exchange="
            f"{_EXCHANGE}, sandbox local, feed publico, sin credenciales."
        )
        # Estado limpio: el historico es append-only, se limpia con el rol propietario.
        _limpiar(migrations_db)

        print("\n=== FASE 1: catalogo y apertura del stream de trades ===")
        # sync_catalog registra el simbolo (BTC-USDT -> native) que el conector necesita
        # para suscribirse: el mismo paso que hace el worker de ingesta real.
        resultado = sync_catalog(connector, _CatalogoEnIngesta(ingestion_db))
        print(
            f"  catalogo: {resultado.active} activos, "
            f"{resultado.deactivated} delistados, "
            f"{resultado.not_representable} no representables"
        )
        connector.open(_CLAVE_TRADES)
        if _CLAVE_TRADES.as_stream_key() not in connector.active():
            print(
                f"FALLO: no se pudo abrir el stream {_CLAVE_TRADES.as_stream_key()}.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print(f"  stream abierto: {_CLAVE_TRADES.as_stream_key()}")

        _ingerir_trades(trade_engine, ventana_s, objetivo)
        if trade_engine.metrics.trades_persisted == 0:
            print(
                "FALLO: no se persistio NI UN trade real. El feed no esta vivo "
                "(revisa red/geo).",
                file=sys.stderr,
            )
            raise SystemExit(1)

        open_time = _elegir_ventana(ingestion_db)
        if open_time is None:
            print("FALLO: sin trades persistidos, no hay ventana que agregar.")
            raise SystemExit(1)
        window_end = open_time + _TF.duration_ms
        trades = reader.trades_in_window(
            _EXCHANGE, _MARKET_TYPE, _SYMBOL, open_time, window_end
        )
        if not trades:
            print("FALLO: la ventana elegida no tiene trades.")
            raise SystemExit(1)
        candle = _candle_de_la_ventana(trades, open_time)
        print(
            f"\n=== FASE 3: barra elegida [{open_time}, {window_end}) con "
            f"{len(trades)} trades ==="
        )

        ok_a, json_1 = _check_cierre(
            footprint_engine, reader, ingestion_db, open_time, candle
        )
        checks["a) cierre -> footprint_closed atomico"] = ok_a
        if ok_a:
            checks["b) reproducibilidad bit a bit"] = _check_reproducibilidad(
                footprint_engine, reader, ingestion_db, open_time, candle, json_1
            )
        checks["c) hueco solapado -> is_complete=False"] = _check_hueco(
            reader, ingestion_db, open_time, candle
        )
        bus = _check_bus(ingestion_db)
        if bus is not None:
            checks["bus) footprint_closed publicable"] = bus
    finally:
        connector.shutdown()
        print("\nCONECTOR DETENIDO (hilo de fondo parado).")
        try:
            _limpiar(migrations_db)
            print("LIMPIEZA OK (trades, huecos, footprint y outbox de demo borrados).")
        except Exception as exc:  # noqa: BLE001 - la limpieza no tapa el veredicto.
            print(f"AVISO: no se pudo limpiar: {type(exc).__name__}: {exc}")
        finally:
            migrations_db.close()
            ingestion_db.close()
            print("CONEXIONES CERRADAS")

    print("\n=== VEREDICTO ===")
    for nombre, ok in checks.items():
        print(f"  {'[OK]   ' if ok else '[FALLO]'} {nombre}")

    if not (checks and all(checks.values())):
        print(
            "\nVALIDACION EN CALIENTE 3b-1 (footprint): FALLIDA. Una validacion que "
            "miente es peor que ninguna.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(
        "\nVALIDACION EN CALIENTE 3b-1 (footprint): OK. Trades reales -> "
        "footprint_closed atomico, celdas por precio exacto, reproducible bit a bit, "
        "is_complete honesto."
    )


if __name__ == "__main__":
    main()
