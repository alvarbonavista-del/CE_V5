"""Validacion en caliente REAL de P07b 3a-ii-OKX: trades de OKX -> hechos persistidos.

Hermana de validate_binance_trades_live.py, para OKX. Conecta el WebSocket REAL de OKX
(canal 'trades-all' en el endpoint business), recibe TRADES REALES individuales y los
convierte en filas de market_trade contra la base LOCAL de juguete, con la maquinaria de
la 3a-i (TradeIngestionEngine + PostgresTradeWriter) y el conector de la 3a-ii-OKX.

Graduada del scratch tools/probe_okx_trades_live.py (el sondeo previo, condicion de
Central): aquel confirmo los tres puntos del diseno; esta prueba el CAMINO ENTERO.

SANDBOX LOCAL, MARKET DATA PUBLICA REAL, JAMAS DINERO. La regla "nunca dinero real" es
de ejecucion/ordenes (M5, P10b); esto es dato publico, feed sin credenciales, TLS OK.

QUE SE PRUEBA AQUI Y NO EN EL CI (regla 5.18): el CI es hermetico y no abre un socket,
asi que el multiplexado (velas y trades por la misma conexion, enrutados por 'channel'),
la reconexion real y -- lo propio de OKX -- el relleno REST PAGINADO por history-trades
solo se validan aqui. Lo que el CI cubre a fondo es la traduccion y la cobertura puras.

LO PROPIO DE OKX FRENTE A BINANCE:
- Canal 'trades-all' (individuales), no 'trades' (agregado), en /ws/v5/business.
- Lado agresor = campo 'side' directo ('buy'|'sell').
- Relleno PAGINADO: history-trades type=1 con &after hacia atras TAPA el hueco entero
  (Binance solo alcanza su ventana). El cap de pagina de OKX es 300 (silencioso).

SIN REDIS (I-02): los trades NO se publican. Solo el rol de INGESTA escribe market_trade
(5.20) y el de MIGRACIONES limpia el sandbox (append-only, 0017/0018 revocan el DELETE).

REGLA DURA (leccion del bloqueo de terminal): el connector usa un HILO DE FONDO. Este
arnes lo cierra (shutdown) en un finally PASE LO QUE PASE, y es ACOTADO EN EL TIEMPO:
ventanas fijas y termina SOLO. Los lectores son daemon: no cuelgan el proceso al salir.

Uso: python tools/validate_okx_trades_live.py
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
from ce_v5.infra.connectors.okx.connector import (  # noqa: E402
    _BACKFILL_MAX_PAGES,
    _REST_TRADES_PAGE,
    OkxSpotConnector,
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
from ce_v5.platform.market.trade_ingestor import TradeIngestionEngine  # noqa: E402
from source.families.market import (  # noqa: E402
    LastSeenTrade,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    RawTrade,
    TradeBackfillResult,
)

# PAR LIQUIDO A PROPOSITO: la comprobacion del lado agresor exige que en la ventana haya
# trades de LOS DOS lados. En un par ilnquido el reparto podria salir a un solo lado por
# falta de actividad y no distinguirlamos "mercado tranquilo" de "bug".
_CLAVE = MarketStreamKey(
    exchange="okx",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.TRADES,  # SIN timeframe: el contrato lo prohibe (ADR-014).
)
_CLAVE_STR = _CLAVE.as_stream_key()
_EXCHANGE = "okx"
_MARKET_TYPE = "spot"
_SYMBOL = "BTC-USDT"

_VENTANA_ENV = "CE_V5_LIVE_TRADES_WINDOW_S"
_OBJETIVO_ENV = "CE_V5_LIVE_TRADES_TARGET"
# Umbrales HOLGADOS: BTC-USDT spot en OKX tiene BAJA CADENCIA (el sondeo vio ~1/s).
# Ventana amplia y objetivo modesto para no confundir "mercado tranquilo" con "bug".
_DEFAULT_VENTANA_S = 45.0
_DEFAULT_OBJETIVO = 60
# Ventana de drenaje tras forzar la reconexion: da tiempo a reconectar con backoff,
# marcar el stream y que el motor dispare su backfill en un drain_once.
_RECONNECT_DRAIN_S = 20.0
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
        "NO es un fallo del codigo de la 3a-ii-OKX: es alcanzabilidad de red. Si OKX "
        "no se alcanza desde esta maquina, se dice y se para.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _rest_backfill(
    connector: OkxSpotConnector, last_seen: LastSeenTrade
) -> TradeBackfillResult:
    """El relleno REST por el MISMO camino del motor (history-trades paginado)."""
    return connector.backfill_after_reconnect(_CLAVE, last_seen)


def _sonda(connector: OkxSpotConnector) -> Sequence[RawTrade]:
    """ANTES de abrir el socket: confirma que el REST responde. Si no, aborta.

    Abrir el WebSocket sin esto significaria esperar mensajes que quiza no llegan y no
    saber si es que el mercado esta parado o que la red no da. Devuelve la primera
    pagina REST (primera conexion), que ademas sirve de ANCLA para el check del hueco.
    """
    print("=== FASE 0: sonda REST previa (no abre WebSocket) ===")
    print(
        f"  GET /api/v5/market/history-trades?instId=BTC-USDT&type=1&limit="
        f"{_REST_TRADES_PAGE} ..."
    )
    try:
        # Primera conexion: sin nada persistido, una pagina; no hay hueco que cubrir.
        ventana = _rest_backfill(
            connector, LastSeenTrade(trade_id=None, event_time_ms=None)
        ).raw_trades
    except (OSError, json.JSONDecodeError) as exc:
        _fallar_red("history-trades (GET /api/v5/market/history-trades)", exc)

    if not ventana:
        print(
            "FALLO: history-trades respondio pero SIN trades para BTC-USDT. Inesperado "
            "en un par tan liquido: revisar antes de abrir el socket.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    trade = ventana[0]
    print(
        f"  [OK] OKX alcanzable. Pagina REST: {len(ventana)} trades. "
        f"Ejemplo: id={trade.trade_id} price={trade.price} qty={trade.qty} "
        f"lado={trade.aggressor_side} event_time={trade.event_time_ms}"
    )
    return ventana


# -- Fuentes para el motor -----------------------------------------------------


class _ObservingSource:
    """Decorador TRANSPARENTE sobre el connector real: delega TODO y ademas GRABA los
    RawTrade que salen por poll_trades, para la fase de reproducibilidad.
    """

    def __init__(self, inner: OkxSpotConnector) -> None:
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

    La fase de reproducibilidad: los MISMOS trades por el MISMO camino de normalizacion
    y dedup. Si el resultado persistido cambiase al repetir la entrada, la ingesta no
    seria reproducible y el footprint que se agregue encima tampoco.
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
    """Catalogo real por-sesion con el rol de INGESTA (solo ese rol escribe el catalogo,
    regla 5.20). Satisface CatalogWriterPort.
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

    La prueba FUERTE de no duplicacion: si filas == claves distintas, no hay ni un solo
    trade repetido (identidad natural exchange/market_type/symbol/trade_id).
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


def _limpiar_trades(owner_db: Database) -> None:
    """Borra los trades de demo. EXIGE el rol de MIGRACIONES (owner): market_trade es
    append-only y la migracion 0017 REVOCA el DELETE a los tres roles de runtime.
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
    print("\n=== COMPROBACION (a): lado AGRESOR por los dos lados ===")
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
        "delataria un bug traduciendo el campo 'side' de OKX."
        if ok
        else f"  {marca} falta un lado: 'side' NO se traduce bien (o el mercado no dio "
        "actividad de los dos lados en la ventana)."
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
    connector: OkxSpotConnector,
    engine: TradeIngestionEngine,
    observing: _ObservingSource,
    reader_db: Database,
) -> bool:
    """(c) Reconexion real: el relleno PAGINADO de OKX TAPA el hueco y el solape dedupa.

    Es la prueba de lo propio de OKX: tras un corte, el motor rebootstrapea SOLO y el
    conector rellena por history-trades paginado. El hueco queda cubierto (covered=
    True): NO se apunta ninguna fila en market_trade_gap.
    """
    print("\n=== COMPROBACION (c): reconexion, el relleno de OKX TAPA el hueco ===")

    filas_antes, _ = _filas_y_claves(reader_db)
    reconn_antes = connector.metrics.reconnections
    boot_antes = engine.metrics.bootstrap_trades
    dup_antes = engine.metrics.duplicates_skipped
    gaps_antes = engine.metrics.uncovered_gaps
    print(f"  filas en market_trade ANTES: {filas_antes}")

    cerradas = connector.force_reconnect_all()
    print(f"  force_reconnect_all: cerro {cerradas} conexion(es)")

    # El arnes NO reinyecta nada: el conector marca el stream reconectado y el MOTOR
    # dispara su backfill solo, por el mismo camino de normalizacion + dedup.
    _drenar_durante(engine, observing, _RECONNECT_DRAIN_S, "drenaje tras la reconexion")

    filas_despues, _ = _filas_y_claves(reader_db)
    reconn = connector.metrics.reconnections - reconn_antes
    boot = engine.metrics.bootstrap_trades - boot_antes
    dup = engine.metrics.duplicates_skipped - dup_antes
    gaps_nuevos = engine.metrics.uncovered_gaps - gaps_antes
    print(f"  reconnections={reconn} bootstrap_trades={boot} duplicates_skipped={dup}")
    print(
        f"  filas ANTES={filas_antes} DESPUES={filas_despues} "
        f"(relleno: +{filas_despues - filas_antes} trades nuevos); "
        f"huecos_nuevos={gaps_nuevos}"
    )

    sin_dup, _, _ = _check_dedup(reader_db, "tras la reconexion")
    # covered=True: el relleno tapo el hueco, asi que NO se apunto ningun hueco nuevo.
    ok = reconn >= 1 and boot > 0 and dup > 0 and sin_dup and gaps_nuevos == 0
    marca = "[OK]" if ok else "[FALLO]"
    print(
        f"  {marca} hubo reconexion real ({reconn}); el motor rebootstrapeo SOLO "
        f"({boot} trades por REST paginado); el solape se dedupo ({dup}); NINGUN hueco "
        "marcado (el relleno de OKX tapo el corte entero)"
    )
    return ok


def _check_reproducibilidad(
    observing: _ObservingSource, ingestion_db: Database, reader_db: Database
) -> bool:
    """(d) Mismos trades -> mismo resultado persistido. Cero filas nuevas."""
    print("\n=== COMPROBACION (d): reproducibilidad (segundo pase) ===")
    recogidos = list(observing.vistos)
    if not recogidos:
        print("  [FALLO] no se recogio ni un RawTrade: no hay nada que re-ingerir.")
        return False

    filas_antes, _ = _filas_y_claves(reader_db)
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
        f"duplicados={m2.duplicates_skipped} rechazos={sum(m2.rejected.values())}"
    )
    print(f"  filas ANTES={filas_antes} DESPUES={filas_despues} (nuevas: {nuevas})")

    ok = nuevas == 0 and filas_despues == claves_despues and m2.duplicates_skipped > 0
    marca = "[OK]" if ok else "[FALLO]"
    print(
        f"  {marca} re-ingerir el MISMO conjunto no anadio ni una fila: la ingesta es "
        "reproducible por la identidad natural del trade (dedup por ON CONFLICT)"
    )
    return ok


def _check_hueco_mayor_que_el_tope(connector: OkxSpotConnector) -> bool:
    """(e) Un hueco MAYOR que el tope de esfuerzo se marca INCOMPLETO (fail-safe).

    Lo propio de OKX -- que el relleno paginado tapa el hueco -- tiene un limite
    honesto: _BACKFILL_MAX_PAGES paginas. Un corte mas largo que eso no se puede
    recuperar, y lo unico honesto es covered=False para que 3b marque las barras.

    COMO SE PROVOCA SIN ESPERAR HORAS: OKX numera los trades con un id ENTERO y CONTIGUO
    por instrumento (verificado en el sondeo), asi que un id muy anterior al actual es
    un trade REAL que existio. Se pide el relleno con un last_seen cuyo id esta
    DELIBERADAMENTE mas atras de lo que el tope de paginas alcanza: el relleno pagina
    hasta el tope, no llega, y declara el hueco. Camino de produccion, contra REST REAL.
    """
    print(
        "\n=== COMPROBACION (e): hueco > tope de esfuerzo -> INCOMPLETO (fail-safe) ==="
    )

    ventana = _rest_backfill(
        connector, LastSeenTrade(trade_id=None, event_time_ms=None)
    ).raw_trades
    if not ventana:
        print("  [FALLO] el REST no dio ventana con la que anclar el hueco.")
        return False
    id_actual = max(int(t.trade_id) for t in ventana)
    # Mas atras de lo que _BACKFILL_MAX_PAGES paginas de _REST_TRADES_PAGE alcanzan, con
    # margen x2: el relleno agotara el tope sin cubrirlo.
    alcance = _BACKFILL_MAX_PAGES * _REST_TRADES_PAGE
    id_sintetico = id_actual - 2 * alcance
    print(
        f"  id actual (REST): {id_actual}; last_seen sintetico (real y contiguo): "
        f"{id_sintetico} -- {2 * alcance} trades atras, > alcance del tope ({alcance})"
    )

    resultado = _rest_backfill(
        connector,
        LastSeenTrade(trade_id=str(id_sintetico), event_time_ms=1),
    )
    ok = not resultado.covered and resultado.gap_from_event_time_ms is not None
    marca = "[OK]" if ok else "[FALLO]"
    print(
        f"  paginas pedidas <= {_BACKFILL_MAX_PAGES}; trades del relleno="
        f"{len(resultado.raw_trades)}; covered={resultado.covered}; "
        f"hueco=[{resultado.gap_from_event_time_ms}, {resultado.gap_to_event_time_ms}]"
    )
    print(
        f"  {marca} el relleno NO alcanzo el last_seen y el hueco quedo DECLARADO "
        "(covered=False): OKX tapa mucho, pero no miente sobre lo que no alcanza."
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

    connector = OkxSpotConnector()
    observing = _ObservingSource(connector)
    engine = TradeIngestionEngine(
        source=observing,
        writer=PostgresTradeWriter(ingestion_db),
    )

    stream_ok = False
    checks: dict[str, bool] = {}
    try:
        print(
            "Validacion en caliente REAL de trades de OKX (P07b 3a-ii-OKX). Sandbox "
            "local, feed publico, sin credenciales."
        )
        print(
            f"  parametros: ventana={ventana_s:.0f}s objetivo={objetivo} trades "
            f"pagina REST={_REST_TRADES_PAGE} tope de paginas={_BACKFILL_MAX_PAGES}\n"
        )

        # FASE 0: sonda REST. Si OKX no responde, se para AQUI, sin abrir socket.
        _sonda(connector)

        # Estado limpio: el historico es append-only; se limpia el stream de demo con el
        # rol propietario antes de medir nada.
        _limpiar_trades(migrations_db)

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
            print(f"  stream abierto: {_CLAVE_STR} (canal trades-all)")
            _drenar_durante(
                engine,
                observing,
                ventana_s,
                "FASE 2: streaming REAL de trades",
                objetivo=objetivo,
            )

            if engine.metrics.trades_persisted == 0:
                print(
                    "FALLO: no se persistio NI UN trade real en la ventana. El feed no "
                    "esta vivo (revisa red/geo).",
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

                checks["a) agresor por los dos lados"] = _check_agresor(migrations_db)
                print("\n=== COMPROBACION (b): dedup por identidad natural ===")
                checks["b) dedup"] = _check_dedup(migrations_db, "tras el streaming")[0]
                checks["c) reconexion tapa el hueco"] = _check_reconexion(
                    connector, engine, observing, migrations_db
                )
                checks["d) reproducibilidad"] = _check_reproducibilidad(
                    observing, ingestion_db, migrations_db
                )
                checks["e) hueco > tope -> fail-safe"] = _check_hueco_mayor_que_el_tope(
                    connector
                )
    finally:
        # REGLA DURA: parar el hilo de fondo del connector SIEMPRE. Los lectores son
        # daemon: shutdown() les senala el fin y no pueden colgar el proceso al salir.
        connector.shutdown()
        print("\nCONECTOR DETENIDO (hilo de fondo parado).")
        try:
            _limpiar_trades(migrations_db)
            print("LIMPIEZA OK (trades de demo borrados con el rol owner).")
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
            "\nVALIDACION EN CALIENTE 3a-ii-OKX (trades): FALLIDA. Una validacion que "
            "miente es peor que ninguna.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(
        "\nVALIDACION EN CALIENTE 3a-ii-OKX (trades): OK. Trades reales de OKX -> "
        "hechos persistidos, con lado agresor exacto, dedup, reconexion que TAPA el "
        "hueco por relleno paginado, reproducibilidad y fail-safe si el hueco excede."
    )


if __name__ == "__main__":
    main()
