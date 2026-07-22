"""Validacion en caliente REAL de P07b 3a-ii: trades de Binance -> hechos persistidos.

Conecta el WebSocket REAL de Binance, recibe TRADES REALES y los convierte en filas de
market_trade contra la base LOCAL de juguete, con la maquinaria de la 3a-i
(TradeIngestionEngine + PostgresTradeWriter) y el conector de la 3a-ii (multiplexado de
trades sobre la MISMA conexion). Demuestra lo que un fake NO puede: el flujo vivo de un
par liquido y el comportamiento del dedup cuando el socket se cae de verdad.

SANDBOX LOCAL, MARKET DATA PUBLICA REAL, JAMAS DINERO. La regla "nunca dinero real" es
de ejecucion/ordenes (M5, P10b); esto es dato de mercado publico, feed sin credenciales
y con TLS verificado.

QUE SE PRUEBA AQUI Y NO EN EL CI (regla 5.18): el CI es hermetico y no abre un socket,
asi que el multiplexado (velas y trades por la misma conexion, enrutados por el campo
'e'), la reconexion real y el bootstrap REST de trades SOLO se pueden validar aqui. Lo
que el CI si cubre a fondo es la traduccion pura, que es donde vive el error de logica.

SIN REDIS, Y NO ES UN OLVIDO: los trades NO se publican al bus (I-02, un par liquido
produce miles por minuto). Por eso este arnes no necesita RedisEventBus ni el DSN de la
aplicacion: solo el rol de INGESTA (que escribe) y el de migraciones (que lee y limpia).

REGLA DURA (leccion del bloqueo de terminal): el connector real usa un HILO DE FONDO.
Este arnes cierra el connector (shutdown) en un finally, PASE LO QUE PASE, y es ACOTADO
EN EL TIEMPO: ventanas fijas y termina SOLO. Nada de bucle infinito. Los lectores son
daemon, asi que no pueden colgar el proceso al salir.

Uso: python tools/validate_binance_trades_live.py
Requiere CE_V5_MIGRATIONS_DATABASE_URL y CE_V5_INGESTION_DATABASE_URL.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from decimal import Decimal
from pathlib import Path
from typing import NoReturn
from urllib.error import HTTPError, URLError

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))
sys.path.insert(0, str(REPO_ROOT / "contracts"))

from ce_v5.entrypoints.worker_ingestion.catalog_sync import sync_catalog  # noqa: E402
from ce_v5.infra.connectors.binance.connector import (  # noqa: E402
    _REST_TRADES_MAX,
    BinanceSpotConnector,
)
from ce_v5.infra.db.config import (  # noqa: E402
    INGESTION_DSN_ENV_VAR,
    MIGRATIONS_DSN_ENV_VAR,
    DbConfig,
    IngestionDbConfig,
)
from ce_v5.infra.db.market_store import PostgresInstrumentCatalog  # noqa: E402
from ce_v5.infra.db.market_trades import PostgresTradeWriter  # noqa: E402
from ce_v5.infra.db.ports import Database  # noqa: E402
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase  # noqa: E402
from ce_v5.platform.market.trade_ingestor import (  # noqa: E402
    TradeIngestionEngine,
)
from ce_v5.platform.market.trade_normalize import trade_from_raw  # noqa: E402
from source.families.market import (  # noqa: E402
    LastSeenTrade,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    RawTrade,
    TradeBackfillResult,
)

# PAR LIQUIDO A PROPOSITO: la comprobacion del lado agresor exige que en pocos segundos
# haya trades de LOS DOS lados. En un par ilnquido el reparto podria salir a un solo
# lado por falta de actividad y no distinguirlamos "mercado tranquilo" de "bug".
_CLAVE = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.TRADES,  # SIN timeframe: el contrato lo prohibe (ADR-014).
)
_CLAVE_STR = _CLAVE.as_stream_key()
_EXCHANGE = "binance"
_MARKET_TYPE = "spot"
_SYMBOL = "BTC-USDT"

_VENTANA_ENV = "CE_V5_LIVE_TRADES_WINDOW_S"
_OBJETIVO_ENV = "CE_V5_LIVE_TRADES_TARGET"
_DEFAULT_VENTANA_S = 20.0
_DEFAULT_OBJETIVO = 200
# Ventana de drenaje tras forzar la reconexion: da tiempo a que el lector reconecte con
# backoff, marque el stream y el motor dispare su bootstrap en un drain_once.
_RECONNECT_DRAIN_S = 15.0
# Tope de espera a que el lector complete una reconexion (backoff + handshake TLS).
_RECONNECT_WAIT_S = 30.0
_PAUSA_S = 0.5
_METRICS_EVERY_S = 5.0


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
            "FALLO: faltan variables obligatorias para la validacion en caliente de "
            f"trades: {', '.join(faltan)}. El rol de INGESTA es el unico que escribe "
            "market_trade (regla 5.20) y el de migraciones es el unico que puede "
            "limpiar el historico. No se salta, se configura el entorno.",
            file=sys.stderr,
        )
        raise SystemExit(2)


# -- Sonda previa --------------------------------------------------------------


def _fallar_red(contexto: str, exc: Exception) -> NoReturn:
    """Diagnostico CLARO segun el tipo de fallo, y fuera. NO se cuelga ni reintenta."""
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
        "NO es un fallo del codigo de la 3a-ii: es alcanzabilidad de red. Si Binance "
        "no se alcanza desde esta maquina, se dice y se para.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _rest_backfill(connector: BinanceSpotConnector) -> Sequence[RawTrade]:
    """El relleno REST desde cero (sin last_seen). Es el MISMO camino que usa el motor.

    Se pide con LastSeenTrade vacio a proposito: sin nada persistido no hay hueco que
    calcular, asi que devuelve la ventana REST tal cual, que es justo lo que hace falta
    para sondear y para anclar el check del hueco.
    """
    return connector.backfill_after_reconnect(
        _CLAVE, LastSeenTrade(trade_id=None, event_time_ms=None)
    ).raw_trades


def _sonda(connector: BinanceSpotConnector) -> Sequence[RawTrade]:
    """ANTES de abrir el socket: confirma que el REST responde. Si no, aborta.

    Abrir el WebSocket sin esto significaria quedarse esperando mensajes que nunca
    llegan y no saber si es que el mercado esta parado o que la red no da.

    Devuelve la ventana REST, que ademas sirve de ANCLA para el check del hueco: su
    trade mas antiguo es el punto que el mercado tendra que dejar atras.
    """
    print("=== FASE 0: sonda REST previa (no abre WebSocket) ===")
    print(f"  GET /api/v3/trades?symbol=BTCUSDT&limit={_REST_TRADES_MAX} ...")
    try:
        ventana = _rest_backfill(connector)
    except (OSError, json.JSONDecodeError) as exc:
        _fallar_red("trades (GET /api/v3/trades)", exc)

    if not ventana:
        print(
            "FALLO: /api/v3/trades respondio pero SIN trades para BTC-USDT. Inesperado "
            "en un par tan liquido: revisar antes de abrir el socket.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    trade = ventana[-1]
    print(
        f"  [OK] Binance alcanzable. Ventana REST: {len(ventana)} trades. "
        f"Ultimo: id={trade.trade_id} price={trade.price} qty={trade.qty} "
        f"lado={trade.aggressor_side} event_time={trade.event_time_ms}"
    )
    return ventana


# -- Fuentes para el motor -----------------------------------------------------


class _ObservingSource:
    """Decorador TRANSPARENTE sobre el connector real: delega TODO y ademas GRABA los
    RawTrade que salen por poll_trades.

    Los graba para la fase de REPRODUCIBILIDAD: hay que poder re-ingerir EXACTAMENTE el
    mismo conjunto que ya entro. No cambia el comportamiento del motor, que sigue
    hablando con el conector real.
    """

    def __init__(self, inner: BinanceSpotConnector) -> None:
        self._inner = inner
        self.vistos: list[RawTrade] = []
        self.ultimo: RawTrade | None = None

    def open(self, key: MarketStreamKey) -> None:
        self._inner.open(key)

    def close(self, key: MarketStreamKey) -> None:
        self._inner.close(key)

    def active(self) -> AbstractSet[str]:
        return set(self._inner.active())

    def poll_trades(self, timeout_ms: int) -> Sequence[RawTrade]:
        lote = self._inner.poll_trades(timeout_ms)
        if lote:
            self.vistos.extend(lote)
            self.ultimo = lote[-1]
        return lote

    def backfill_after_reconnect(
        self, key: MarketStreamKey, last_seen: LastSeenTrade
    ) -> TradeBackfillResult:
        return self._inner.backfill_after_reconnect(key, last_seen)

    def drain_reconnected(self) -> AbstractSet[str]:
        return set(self._inner.drain_reconnected())


class _ReplaySource:
    """Feed que RE-EMITE un conjunto fijo de RawTrade y luego se calla. SIN RED.

    Es la fase de reproducibilidad: los MISMOS trades por el MISMO camino de
    normalizacion y dedup. Si el resultado persistido cambiase al repetir la entrada, la
    ingesta no seria reproducible y el footprint que se agregue encima tampoco.
    """

    def __init__(self, clave: MarketStreamKey, trades: Sequence[RawTrade]) -> None:
        self._clave = clave
        self._pendientes: list[RawTrade] = list(trades)

    @property
    def pendientes(self) -> int:
        return len(self._pendientes)

    def open(self, key: MarketStreamKey) -> None:
        return None

    def close(self, key: MarketStreamKey) -> None:
        return None

    def active(self) -> AbstractSet[str]:
        return {self._clave.as_stream_key()}

    def poll_trades(self, timeout_ms: int) -> Sequence[RawTrade]:
        lote = self._pendientes[:200]
        del self._pendientes[:200]
        return lote

    def backfill_after_reconnect(
        self, key: MarketStreamKey, last_seen: LastSeenTrade
    ) -> TradeBackfillResult:
        # El segundo pase NO rellena nada: se re-ingiere EXACTAMENTE lo ya recogido.
        del key, last_seen
        return TradeBackfillResult(
            raw_trades=(),
            covered=True,
            gap_from_event_time_ms=None,
            gap_to_event_time_ms=None,
        )

    def drain_reconnected(self) -> AbstractSet[str]:
        return set()


# -- Catalogo con el rol de INGESTA (mismo patron que _CatalogOnDb) ------------


class _CatalogoEnIngesta:
    """Catalogo real por-sesion con el rol de INGESTA (la escritura del catalogo solo la
    permite ese rol, regla 5.20). Satisface CatalogWriterPort.
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


# -- Consultas y limpieza ------------------------------------------------------


def _entero(valor: object) -> int:
    if not isinstance(valor, int):
        msg = f"Se esperaba un entero de la base y llego {type(valor)!r}."
        raise TypeError(msg)
    return valor


def _filas_y_claves(reader_db: Database) -> tuple[int, int]:
    """(filas, claves naturales DISTINTAS) de market_trade para este stream.

    La prueba FUERTE de no duplicacion: la identidad natural del trade es
    (exchange, market_type, symbol, trade_id). Si filas == claves distintas, no hay ni
    un solo trade repetido en el historico.
    """
    with reader_db.transaction() as session:
        row = session.fetchone(
            "SELECT count(*), "
            "count(DISTINCT (exchange, market_type, symbol, trade_id)) "
            "FROM market_trade "
            "WHERE exchange = %s AND market_type = %s AND symbol = %s",
            (_EXCHANGE, _MARKET_TYPE, _SYMBOL),
        )
    if row is None:
        return 0, 0
    return _entero(row[0]), _entero(row[1])


def _reparto_por_lado(reader_db: Database) -> dict[str, int]:
    with reader_db.transaction() as session:
        rows = session.fetchall(
            "SELECT aggressor_side, count(*) FROM market_trade "
            "WHERE exchange = %s AND market_type = %s AND symbol = %s "
            "GROUP BY aggressor_side ORDER BY aggressor_side",
            (_EXCHANGE, _MARKET_TYPE, _SYMBOL),
        )
    return {str(r[0]): _entero(r[1]) for r in rows}


def _muestra(reader_db: Database, limite: int) -> list[tuple[str, Decimal, str, int]]:
    with reader_db.transaction() as session:
        rows = session.fetchall(
            "SELECT trade_id, price, aggressor_side, event_time FROM market_trade "
            "WHERE exchange = %s AND market_type = %s AND symbol = %s "
            "ORDER BY event_time DESC, trade_id DESC LIMIT %s",
            (_EXCHANGE, _MARKET_TYPE, _SYMBOL, limite),
        )
    return [(str(r[0]), Decimal(str(r[1])), str(r[2]), _entero(r[3])) for r in rows]


def _contar_huecos(reader_db: Database) -> int:
    with reader_db.transaction() as session:
        row = session.fetchone(
            "SELECT count(*) FROM market_trade_gap "
            "WHERE exchange = %s AND market_type = %s AND symbol = %s",
            (_EXCHANGE, _MARKET_TYPE, _SYMBOL),
        )
    return 0 if row is None else _entero(row[0])


def _huecos(reader_db: Database) -> list[tuple[int | None, int | None]]:
    with reader_db.transaction() as session:
        rows = session.fetchall(
            "SELECT gap_from_event_time_ms, gap_to_event_time_ms FROM market_trade_gap "
            "WHERE exchange = %s AND market_type = %s AND symbol = %s "
            "ORDER BY gap_from_event_time_ms",
            (_EXCHANGE, _MARKET_TYPE, _SYMBOL),
        )
    return [
        (
            None if r[0] is None else _entero(r[0]),
            None if r[1] is None else _entero(r[1]),
        )
        for r in rows
    ]


def _limpiar_huecos(owner_db: Database) -> None:
    """Borra los huecos de demo. EXIGE el rol de MIGRACIONES (owner): market_trade_gap
    es append-only y la 0018 revoca el DELETE a los tres roles de runtime.
    """
    with owner_db.transaction() as session:
        session.execute(
            "DELETE FROM market_trade_gap "
            "WHERE exchange = %s AND market_type = %s AND symbol = %s",
            (_EXCHANGE, _MARKET_TYPE, _SYMBOL),
        )


def _dejar_solo_el_ancla(
    owner_db: Database, ingestion_db: Database, ancla: RawTrade
) -> None:
    """Deja el historico con UNA sola fila: el trade ancla.

    SIMULA UN PROCESO QUE ESTUVO CAIDO MUCHO RATO, que es el caso que el check (e)
    tiene que provocar y que no se puede provocar de otra forma en un arnes de minuto y
    medio: para que el socket generase un hueco real de mas de mil trades habria que
    tenerlo caido varios minutos.

    NO se falsea ningun dato: el ancla es un trade REAL de Binance, traido por REST al
    empezar. Lo unico que se manipula es CUANTO recuerda la base, que es exactamente lo
    que distingue a un proceso recien reiniciado de uno que venia corriendo. A partir de
    ahi, todo lo que se prueba (last_seen, el relleno REST, el calculo de cobertura y el
    registro del hueco) es el camino de produccion sin tocar.

    El ancla se PERSISTE primero, por la frontera de confianza de siempre: viene del
    REST del arranque y para entonces el socket aun no la habia traido, asi que no esta
    en la tabla. Sin este paso el borrado dejaria el historico VACIO, last_seen no
    devolveria nada y el conector concluiria "primera conexion, no hay hueco".
    """
    PostgresTradeWriter(ingestion_db).persist(trade_from_raw(ancla, _CLAVE))
    with owner_db.transaction() as session:
        session.execute(
            "DELETE FROM market_trade "
            "WHERE exchange = %s AND market_type = %s AND symbol = %s "
            "AND trade_id <> %s",
            (_EXCHANGE, _MARKET_TYPE, _SYMBOL, ancla.trade_id),
        )


def _limpiar_trades(owner_db: Database) -> None:
    """Borra los trades de demo de este stream. EXIGE el rol de MIGRACIONES (owner):
    market_trade es append-only y la migracion 0017 REVOCA el DELETE a los tres roles de
    runtime (app, ingesta, operador). Solo el propietario de la tabla puede limpiar el
    sandbox, y eso es exactamente lo que se quiere.
    """
    with owner_db.transaction() as session:
        session.execute(
            "DELETE FROM market_trade "
            "WHERE exchange = %s AND market_type = %s AND symbol = %s",
            (_EXCHANGE, _MARKET_TYPE, _SYMBOL),
        )


# -- Drenaje -------------------------------------------------------------------


def _imprimir_metricas(
    engine: TradeIngestionEngine, observing: _ObservingSource
) -> None:
    m = engine.metrics
    ultimo = observing.ultimo
    precio = "-" if ultimo is None else ultimo.price
    print(
        f"  [metricas] persistidos={m.trades_persisted} "
        f"duplicados={m.duplicates_skipped} bootstrap={m.bootstrap_trades} "
        f"errores_bootstrap={m.bootstrap_errors} "
        f"sin_suscripcion={m.unsubscribed_dropped} "
        f"rechazos={sum(m.rejected.values())} "
        f"degradados={sorted(m.degraded_streams)} ultimo_precio={precio}"
    )


def _drenar_durante(
    engine: TradeIngestionEngine,
    observing: _ObservingSource,
    segundos: float,
    etiqueta: str,
    objetivo: int | None = None,
) -> None:
    """Drena hasta agotar la ventana o (si se da) alcanzar el objetivo de trades."""
    print(f"\n=== {etiqueta} (ventana {segundos:.0f}s) ===")
    fin = time.monotonic() + segundos
    ultimo_print = 0.0
    while time.monotonic() < fin:
        engine.drain_once()
        if objetivo is not None and engine.metrics.trades_persisted >= objetivo:
            print(f"  objetivo de {objetivo} trades alcanzado; se corta la ventana.")
            break
        ahora = time.monotonic()
        if ahora - ultimo_print >= _METRICS_EVERY_S:
            _imprimir_metricas(engine, observing)
            ultimo_print = ahora
        time.sleep(_PAUSA_S)
    _imprimir_metricas(engine, observing)


# -- Comprobaciones ------------------------------------------------------------


def _check_agresor(reader_db: Database) -> bool:
    """(a) El lado agresor es EXACTO: tiene que haber de los dos lados."""
    print("\n=== COMPROBACION (a): lado AGRESOR exacto ===")
    reparto = _reparto_por_lado(reader_db)
    total = sum(reparto.values())
    print(f"  reparto persistido: {reparto} (total {total})")
    for lado, n in sorted(reparto.items()):
        pct = 0.0 if total == 0 else 100.0 * n / total
        print(f"    - {lado}: {n} ({pct:.1f}%)")

    ok = reparto.get("buy", 0) > 0 and reparto.get("sell", 0) > 0
    marca = "[OK]" if ok else "[FALLO]"
    print(
        f"  {marca} hay trades de los DOS lados. Un par liquido con un solo lado "
        "delataria un bug de clasificacion del flag 'm' de Binance."
        if ok
        else f"  {marca} falta un lado en el reparto: el flag 'm' NO se esta "
        "traduciendo bien (o el mercado no dio actividad de los dos lados)."
    )
    return ok


def _check_dedup(reader_db: Database, etiqueta: str) -> tuple[bool, int, int]:
    """(b) filas == claves naturales distintas: cero duplicados."""
    filas, claves = _filas_y_claves(reader_db)
    ok = filas == claves
    marca = "[OK]" if ok else "[FALLO]"
    print(
        f"  {marca} {etiqueta}: filas={filas} == claves distintas={claves} "
        "(identidad natural exchange/market_type/symbol/trade_id)"
    )
    return ok, filas, claves


def _check_reconexion(
    connector: BinanceSpotConnector,
    engine: TradeIngestionEngine,
    observing: _ObservingSource,
    reader_db: Database,
) -> bool:
    """(c) Reconexion real: se rellena el hueco y el solape se DEDUPA."""
    print("\n=== COMPROBACION (c): reconexion SIN perder ni duplicar ===")

    filas_antes, _ = _filas_y_claves(reader_db)
    reconn_antes = connector.metrics.reconnections
    boot_antes = engine.metrics.bootstrap_trades
    dup_antes = engine.metrics.duplicates_skipped
    print(f"  filas en market_trade ANTES: {filas_antes}")

    cerradas = connector.force_reconnect_all()
    print(f"  force_reconnect_all: cerro {cerradas} conexion(es)")

    # El arnes NO reinyecta nada: el conector marca el stream reconectado
    # (drain_reconnected) y el MOTOR dispara su backfill solo, por el mismo
    # camino de normalizacion + dedup que los trades del socket.
    _drenar_durante(engine, observing, _RECONNECT_DRAIN_S, "drenaje tras la reconexion")

    filas_despues, _ = _filas_y_claves(reader_db)
    reconn = connector.metrics.reconnections - reconn_antes
    boot = engine.metrics.bootstrap_trades - boot_antes
    dup = engine.metrics.duplicates_skipped - dup_antes
    print(f"  reconnections={reconn} bootstrap_trades={boot} duplicates_skipped={dup}")
    print(
        f"  filas en market_trade ANTES={filas_antes} DESPUES={filas_despues} "
        f"(relleno del hueco: +{filas_despues - filas_antes} trades nuevos)"
    )

    sin_dup, _, _ = _check_dedup(reader_db, "tras la reconexion")
    ok = reconn >= 1 and boot > 0 and dup > 0 and sin_dup
    marca = "[OK]" if ok else "[FALLO]"
    print(
        f"  {marca} hubo reconexion real ({reconn}); el motor rebootstrapeo SOLO "
        f"({boot} trades por REST); el solape ya persistido se dedupo ({dup}); el "
        "historico no tiene ni un trade repetido"
    )
    return ok


def _reconectar_y_esperar(connector: BinanceSpotConnector) -> bool:
    """Fuerza una reconexion y ESPERA a que el lector la complete de verdad.

    Hasta que no reconecta, el stream no queda marcado y el motor no dispararia ningun
    backfill: drenar antes solo persistiria trades vivos y falsearia el punto de
    partida. Acotado en el tiempo, como todo en este arnes.
    """
    antes = connector.metrics.reconnections
    cerradas = connector.force_reconnect_all()
    print(f"  force_reconnect_all: cerro {cerradas} conexion(es); esperando reconexion")
    fin = time.monotonic() + _RECONNECT_WAIT_S
    while connector.metrics.reconnections == antes and time.monotonic() < fin:
        time.sleep(_PAUSA_S)
    if connector.metrics.reconnections == antes:
        print(
            f"  [FALLO] el lector no reconecto en {_RECONNECT_WAIT_S:.0f}s: sin "
            "reconexion no hay backfill que comprobar.",
            file=sys.stderr,
        )
        return False
    print(f"  reconexion completada (reconnections={connector.metrics.reconnections})")
    return True


def _check_hueco(
    connector: BinanceSpotConnector,
    engine: TradeIngestionEngine,
    observing: _ObservingSource,
    ingestion_db: Database,
    owner_db: Database,
    ancla: RawTrade,
) -> bool:
    """(e) Un hueco que el REST NO puede cubrir se REGISTRA; uno cubierto, NO.

    Es la prueba del modelo honesto entero: el backfill esta ACOTADO por el techo del
    endpoint (1000 trades), asi que un corte mas largo que eso deja dato perdido para
    siempre, y lo unico honesto es dejar constancia de donde falta.
    """
    print("\n=== COMPROBACION (e): hueco no cubierto -> se REGISTRA ===")

    # (e1) HUECO REAL. Se deja la base como la tendria un proceso que acaba de arrancar
    # tras un corte largo: su ultimo trade conocido es el ancla, de hace mas de mil
    # trades. El relleno REST no llega hasta el, y eso es exactamente un hueco.
    ventana_actual = _rest_backfill(connector)
    mas_antiguo = min(int(t.trade_id) for t in ventana_actual)
    distancia = mas_antiguo - int(ancla.trade_id)
    print(
        f"  ancla (REST del arranque): id={ancla.trade_id} "
        f"event_time={ancla.event_time_ms}"
    )
    print(
        f"  ventana REST actual: {len(ventana_actual)} trades, id mas antiguo="
        f"{mas_antiguo} -> el mercado ha dejado atras el ancla en {distancia} trades"
    )
    if distancia <= 1:
        print(
            "  [FALLO] el mercado no se ha movido lo bastante para que el ancla quede "
            "fuera de la ventana REST: sin hueco que provocar, no se prueba nada.",
            file=sys.stderr,
        )
        return False

    _limpiar_huecos(owner_db)
    huecos_antes = _contar_huecos(ingestion_db)
    gaps_antes = engine.metrics.uncovered_gaps

    if not _reconectar_y_esperar(connector):
        return False

    # EL ESTADO SE PREPARA AQUI, DESPUES DE LA RECONEXION Y ANTES DEL PRIMER DRENAJE, y
    # el orden no es un capricho del arnes: es la limitacion que el propio motor
    # documenta. Si se preparase antes, los ciclos de drenaje que corren mientras el
    # lector reconecta habrian persistido trades recientes del socket, last_seen dejaria
    # de ser el ancla y el conector concluiria que no falta nada. Preparado aqui, el
    # siguiente drain_once hace su backfill ANTES de tocar el poll, que es exactamente
    # el orden que el motor garantiza.
    _dejar_solo_el_ancla(owner_db, ingestion_db, ancla)
    engine.drain_once()
    _imprimir_metricas(engine, observing)

    huecos = _huecos(ingestion_db)
    nuevos = engine.metrics.uncovered_gaps - gaps_antes
    print(f"  huecos en market_trade_gap: ANTES={huecos_antes} DESPUES={len(huecos)}")
    for desde, hasta in huecos:
        print(f"    - [{desde}, {hasta}]")
    e1 = len(huecos) == 1 and nuevos == 1
    print(
        f"  {'[OK]' if e1 else '[FALLO]'} (e1) el hueco no cubierto quedo REGISTRADO "
        f"(filas={len(huecos)}, uncovered_gaps=+{nuevos})"
    )

    # (e2) RECONEXION CORTA. Ahora la base ya tiene los trades recientes que acaba de
    # persistir el relleno, asi que el siguiente relleno SI solapa con lo persistido: no
    # hay hueco y NO debe apuntarse ninguna fila nueva.
    huecos_antes_e2 = len(huecos)
    gaps_antes_e2 = engine.metrics.uncovered_gaps
    print("\n  (e2) reconexion CORTA, con el historico ya al dia:")
    if not _reconectar_y_esperar(connector):
        return False
    engine.drain_once()
    _imprimir_metricas(engine, observing)
    huecos_despues_e2 = _contar_huecos(ingestion_db)
    nuevos_e2 = engine.metrics.uncovered_gaps - gaps_antes_e2
    e2 = huecos_despues_e2 == huecos_antes_e2 and nuevos_e2 == 0
    print(
        f"  {'[OK]' if e2 else '[FALLO]'} (e2) la reconexion CUBIERTA no apunto ningun "
        f"hueco (filas {huecos_antes_e2} -> {huecos_despues_e2}, "
        f"uncovered_gaps=+{nuevos_e2})"
    )
    return e1 and e2


def _check_reproducibilidad(
    observing: _ObservingSource, ingestion_db: Database, reader_db: Database
) -> bool:
    """(d) Mismos trades -> mismo resultado persistido. Cero filas nuevas."""
    print("\n=== COMPROBACION (d): reproducibilidad (segundo pase) ===")
    recogidos = list(observing.vistos)
    if not recogidos:
        print("  [FALLO] no se recogio ni un RawTrade: no hay nada que re-ingerir.")
        return False

    filas_antes, claves_antes = _filas_y_claves(reader_db)
    replay = _ReplaySource(_CLAVE, recogidos)
    engine2 = TradeIngestionEngine(
        source=replay, writer=PostgresTradeWriter(ingestion_db)
    )
    print(f"  re-ingiriendo los MISMOS {len(recogidos)} RawTrade del socket...")
    while replay.pendientes:
        engine2.drain_once()
    engine2.drain_once()

    filas_despues, claves_despues = _filas_y_claves(reader_db)
    m2 = engine2.metrics
    nuevas = filas_despues - filas_antes
    print(
        f"  segundo pase: persistidos={m2.trades_persisted} "
        f"duplicados={m2.duplicates_skipped} "
        f"rechazos={sum(m2.rejected.values())}"
    )
    print(f"  filas ANTES={filas_antes} DESPUES={filas_despues} (nuevas: {nuevas})")

    ok = nuevas == 0 and filas_despues == claves_despues and m2.duplicates_skipped > 0
    marca = "[OK]" if ok else "[FALLO]"
    print(
        f"  {marca} re-ingerir el MISMO conjunto no anadio ni una fila: la ingesta es "
        "reproducible por la identidad natural del trade (el dedup lo decide la base "
        "con ON CONFLICT, no una consulta previa)"
    )
    return ok


# -- Orquestacion --------------------------------------------------------------


def main() -> None:
    _exigir_env()
    ventana_s = float(os.environ.get(_VENTANA_ENV, str(_DEFAULT_VENTANA_S)))
    objetivo = int(os.environ.get(_OBJETIVO_ENV, str(_DEFAULT_OBJETIVO)))

    migrations_db = PsycopgDatabase(DbConfig.migrations_from_env())
    ingestion_db = PsycopgDatabase(
        DbConfig(dsn=IngestionDbConfig.from_env(_solo(INGESTION_DSN_ENV_VAR)).dsn)
    )

    connector = BinanceSpotConnector()
    observing = _ObservingSource(connector)
    engine = TradeIngestionEngine(
        source=observing,
        writer=PostgresTradeWriter(ingestion_db),
    )

    stream_ok = False
    checks: dict[str, bool] = {}
    try:
        print(
            "Validacion en caliente REAL de trades de Binance (P07b 3a-ii). "
            "Sandbox local, feed publico, sin credenciales."
        )
        print(
            f"  parametros: ventana={ventana_s:.0f}s objetivo={objetivo} trades "
            f"techo REST del relleno={_REST_TRADES_MAX}\n"
        )

        # FASE 0: sonda REST. Si Binance no responde, se para AQUI, sin abrir socket.
        # Su trade mas antiguo queda de ANCLA para el check (e) del hueco.
        ancla = _sonda(connector)[0]

        # Estado limpio: el historico es append-only, asi que se limpia el stream de
        # demo con el rol propietario antes de medir nada.
        _limpiar_trades(migrations_db)
        _limpiar_huecos(migrations_db)

        # FASE 1: catalogo + stream de TRADES.
        print("\n=== FASE 1: catalogo y apertura del stream de trades ===")
        catalogo = _CatalogoEnIngesta(ingestion_db)
        resultado = sync_catalog(connector, catalogo)
        print(
            f"  catalogo sincronizado: {resultado.active} activos, "
            f"{resultado.deactivated} delistados, "
            f"{resultado.not_representable} no representables"
        )

        observing.open(_CLAVE)
        stream_ok = _CLAVE_STR in observing.active()
        if not stream_ok:
            print(f"FALLO: no se pudo abrir el stream {_CLAVE_STR}.", file=sys.stderr)
        else:
            print(f"  stream abierto: {_CLAVE_STR} (btcusdt@trade)")
            _drenar_durante(
                engine,
                observing,
                ventana_s,
                "FASE 2: streaming REAL de trades",
                objetivo=objetivo,
            )

            if engine.metrics.trades_persisted == 0:
                print(
                    "FALLO: no se persistio NI UN trade real en toda la ventana. El "
                    "feed no esta vivo (revisa red/geo).",
                    file=sys.stderr,
                )
                stream_ok = False
            else:
                print(
                    f"  trades vistos por el socket: {len(observing.vistos)}; "
                    f"persistidos: {engine.metrics.trades_persisted}"
                )
                print("  ultimos 3 trades del historico (id, price, lado, event_time):")
                for tid, price, lado, ts in _muestra(migrations_db, 3):
                    print(f"    - id={tid} price={price} lado={lado} event_time={ts}")

                checks["a) agresor exacto"] = _check_agresor(migrations_db)
                print("\n=== COMPROBACION (b): dedup por identidad natural ===")
                checks["b) dedup"] = _check_dedup(migrations_db, "tras el streaming")[0]
                checks["c) reconexion"] = _check_reconexion(
                    connector, engine, observing, migrations_db
                )
                checks["d) reproducibilidad"] = _check_reproducibilidad(
                    observing, ingestion_db, migrations_db
                )
                # (e) VA LA ULTIMA a proposito: manipula lo que la base recuerda para
                # simular un proceso recien reiniciado, asi que dejaria el historico en
                # un estado que invalidaria las medidas de (a)-(d).
                checks["e) hueco no cubierto"] = _check_hueco(
                    connector,
                    engine,
                    observing,
                    migrations_db,
                    migrations_db,
                    ancla,
                )
    finally:
        # REGLA DURA: parar el hilo de fondo del connector SIEMPRE. Los lectores son
        # daemon: shutdown() les senala el fin y no pueden colgar el proceso al salir.
        connector.shutdown()
        print("\nCONECTOR DETENIDO (hilo de fondo parado).")
        try:
            _limpiar_trades(migrations_db)
            _limpiar_huecos(migrations_db)
            print("LIMPIEZA OK (trades y huecos de demo borrados con el rol owner).")
        except Exception as exc:  # noqa: BLE001 - la limpieza no puede tapar el veredicto.
            print(
                f"AVISO: no se pudo limpiar market_trade: {type(exc).__name__}: {exc}"
            )
        finally:
            migrations_db.close()
            ingestion_db.close()
            print("CONEXIONES CERRADAS")

    print("\n=== VEREDICTO ===")
    for nombre, ok in checks.items():
        print(f"  {'[OK]   ' if ok else '[FALLO]'} {nombre}")

    if not (stream_ok and checks and all(checks.values())):
        print(
            "\nVALIDACION EN CALIENTE 3a-ii (trades): FALLIDA. Una validacion que "
            "miente es peor que ninguna.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(
        "\nVALIDACION EN CALIENTE 3a-ii (trades): OK. Trades reales de Binance -> "
        "hechos persistidos, con lado agresor exacto, dedup, reconexion y "
        "reproducibilidad."
    )


if __name__ == "__main__":
    main()
