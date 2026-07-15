"""Arnes de VALIDACION EN CALIENTE de P07 (DOC_ENTREGABLES sec.5).

NO es un test de CI: es comprobacion VIVA. Enciende el sistema contra PostgreSQL REAL
y demuestra, con salida imprimible y veredictos, las CUATRO validaciones en caliente de
la pieza:

  1. OBLIGATORIA (Roadmap): un alta de interes ENCIENDE el stream por ref-count; una
     baja, pasada la histeresis, lo APAGA.
  2. ADICION (a): dos tenants distintos interesados en el MISMO flujo comparten UN solo
     stream (los publicos no se duplican por tenant); al irse uno, el stream sigue vivo.
  3. ADICION (b): tras un reinicio SIN memoria, el ref-count se RECONSTRUYE desde los
     intents persistidos (ni uno de mas ni de menos): ahi es donde v4 se habria roto.
  4. ADICION (c): la histeresis absorbe la demanda intermitente sin un solo parpadeo.

SANDBOX, DATOS DE JUGUETE, JAMAS DINERO REAL. Siembra dos usuarios de demo con emails
FIJOS (reusa en cada ejecucion, no multiplica) y limpia sus intents al empezar y al
terminar. Termina con codigo != 0 si CUALQUIER veredicto falla: una validacion en
caliente que miente es peor que ninguna.

LA VENTANILLA es la REAL (market_public_demand() via PostgresPublicDemand con el rol de
INGESTA): asi se demuestra la agregacion cross-tenant SIN fuga: el worker solo ve
{clave: cuantos}, jamas QUIENES.

GUARDIA 5.20 (leccion de B9): un solo proceso legitimamente porta VARIOS roles aqui
(app, migraciones, ingesta) porque esta validacion EXIGE el sistema completo. Cada
cargador de config recibe el sub-entorno con SOLO SU DSN, que es EXACTAMENTE el entorno
que portaria el proceso real de ese rol; no es una puerta trasera, es la misma
restriccion que la guardia hace cumplir en produccion.

Uso: python tools/validate_p07_hot.py
Requiere CE_V5_DATABASE_URL (app), CE_V5_MIGRATIONS_DATABASE_URL (migraciones) y
CE_V5_INGESTION_DATABASE_URL (ingesta). Si falta alguno, FALLA con mensaje claro (no se
salta: esta validacion no existe sin el sistema entero).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))
sys.path.insert(0, str(REPO_ROOT / "contracts"))

from ce_v5.core.clock import Clock, SimulatedClock  # noqa: E402
from ce_v5.infra.db.config import (  # noqa: E402
    DSN_ENV_VAR,
    INGESTION_DSN_ENV_VAR,
    MIGRATIONS_DSN_ENV_VAR,
    DbConfig,
    IngestionDbConfig,
)
from ce_v5.infra.db.identity import register_user  # noqa: E402
from ce_v5.infra.db.market_store import (  # noqa: E402
    PostgresInstrumentCatalog,
    PostgresIntentStore,
    PostgresPublicDemand,
)
from ce_v5.infra.db.ports import Database  # noqa: E402
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase  # noqa: E402
from ce_v5.infra.db.tenancy import (  # noqa: E402
    TenantScopedDatabase,
    provision_tenant_for_user,
)
from ce_v5.platform.market.registry import MarketInterestRegistry  # noqa: E402
from ce_v5.platform.market.subscriptions import (  # noqa: E402
    HysteresisConfig,
    SubscriptionManager,
)
from source.families.market import (  # noqa: E402
    IntentSourceType,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    StreamScope,
    SubscriptionIntent,
    Timeframe,
)

# Datos de juguete FIJOS: re-ejecutar reutiliza los mismos usuarios (y tenants) en vez
# de multiplicarlos. El user_id lo asigna la ventanilla de identidad (P06b, CA-07).
_EMAIL_A = "hot-p07-a@ejemplo.test"
_EMAIL_B = "hot-p07-b@ejemplo.test"
_PASSWORD_HASH = "hash-de-prueba-no-es-argon2"

# El flujo publico de la demo: BTC-USDT 1m en Binance Spot (el exchange real elegido).
_CLAVE = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.CANDLES,
    timeframe=Timeframe.M1,
)
_CLAVE_STR = _CLAVE.as_stream_key()

_AHORA = 1_784_073_600_000  # instante fijo del SimulatedClock (UTC epoch ms).
_OFF_DELAY_MS = 30_000  # retardo de apagado de la histeresis (anti-flapping).


def _solo(*claves: str) -> dict[str, str]:
    """El sub-entorno con SOLO esas variables de os.environ (las que existan).

    Es la restriccion de la guardia 5.20 hecha explicita: cada cargador de config ve el
    entorno que su proceso real portaria, ni una credencial de mas.
    """
    return {clave: os.environ[clave] for clave in claves if clave in os.environ}


def _exigir_dsn() -> None:
    """Falla RUIDOSO si falta cualquiera de los tres DSN. No se salta (regla 5.18)."""
    faltan = [
        var
        for var in (DSN_ENV_VAR, MIGRATIONS_DSN_ENV_VAR, INGESTION_DSN_ENV_VAR)
        if not os.environ.get(var, "").strip()
    ]
    if faltan:
        print(
            "FALLO: faltan DSN obligatorios para la validacion en caliente de P07: "
            f"{', '.join(faltan)}.\n"
            "Esta validacion EXIGE el sistema completo (app + migraciones + ingesta): "
            "no se salta, se configura el entorno.",
            file=sys.stderr,
        )
        raise SystemExit(2)


class _Marcador:
    """Contador de veredictos. Imprime [OK]/[FALLO] y recuerda cuantos fallaron."""

    def __init__(self) -> None:
        self.fallos = 0

    def veredicto(self, ok: bool, texto: str) -> None:
        self.fallos += 0 if ok else 1
        print(f"  {'[OK]' if ok else '[FALLO]'} {texto}")


class RecordingStreamController:
    """El "mundo real" de streams para el arnes: satisface StreamControllerPort por
    forma. NO toca la red (la reconexion REAL sobre Binance es B12b, aparte): aqui solo
    REGISTRA que clave se abre/cierra y en que instante del reloj simulado, y lo imprime
    en el momento en que ocurre. active() devuelve las claves realmente abiertas.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._abiertos: dict[str, int] = {}  # clave -> instante de apertura
        self.opens = 0
        self.closes = 0

    def open(self, key: MarketStreamKey) -> None:
        clave = key.as_stream_key()
        self._abiertos[clave] = self._clock.now_ms()
        self.opens += 1
        print(f"    [controller] OPEN  {clave}  @t={self._clock.now_ms()}")

    def close(self, key: MarketStreamKey) -> None:
        clave = key.as_stream_key()
        self._abiertos.pop(clave, None)
        self.closes += 1
        print(f"    [controller] CLOSE {clave}  @t={self._clock.now_ms()}")

    def active(self) -> set[str]:
        return set(self._abiertos)


class _CatalogoEnIngesta:
    """Catalogo real por-sesion con el rol de INGESTA (mismo patron que _CatalogOnDb de
    la composicion). La ESCRITURA del catalogo solo la permite el rol de ingesta (5.20);
    la LECTURA la usa el registry para validar que el interes apunta a algo real.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    def has_exchange(self, exchange: str) -> bool:
        with self._database.transaction() as session:
            return PostgresInstrumentCatalog(session).has_exchange(exchange)

    def exists(self, exchange: str, market_type: str, symbol: str) -> bool:
        with self._database.transaction() as session:
            return PostgresInstrumentCatalog(session).exists(
                exchange, market_type, symbol
            )

    def is_tradable(self, exchange: str, market_type: str, symbol: str) -> bool:
        with self._database.transaction() as session:
            return PostgresInstrumentCatalog(session).is_tradable(
                exchange, market_type, symbol
            )

    def native_symbol(self, exchange: str, market_type: str, symbol: str) -> str | None:
        with self._database.transaction() as session:
            return PostgresInstrumentCatalog(session).native_symbol(
                exchange, market_type, symbol
            )

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


class _DemandaEnIngesta:
    """La VENTANILLA REAL (CA-P07-D): PostgresPublicDemand con el rol de INGESTA, una
    transaccion por snapshot (mismo patron que _PublicDemandOnDb de la composicion).
    Es la UNICA via por la que el worker conoce la demanda: {clave: cuantos}, jamas
    QUIENES.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    def snapshot(self) -> dict[str, int]:
        with self._database.transaction() as session:
            return PostgresPublicDemand(session).snapshot()


class _IntentStoreEnApp:
    """Intereses sobre PostgreSQL, con el rol de APLICACION bajo RLS. Satisface
    IntentStorePort abriendo una transaccion TENANT-SCOPED por operacion, igual que el
    backend haria por peticion: el user_id de la operacion fija el contexto RLS.
    """

    def __init__(self, scoped_db: TenantScopedDatabase) -> None:
        self._scoped_db = scoped_db

    def count_for_subject(self, tenant_id: UUID, user_id: UUID) -> int:
        with self._scoped_db.transaction(user_id) as scoped:
            return PostgresIntentStore(scoped).count_for_subject(tenant_id, user_id)

    def insert(self, intent: SubscriptionIntent) -> None:
        with self._scoped_db.transaction(intent.user_id) as scoped:
            PostgresIntentStore(scoped).insert(intent)

    def delete(
        self,
        tenant_id: UUID,
        user_id: UUID,
        source_type: IntentSourceType,
        source_ref: str,
        market_stream_key: str,
    ) -> int:
        with self._scoped_db.transaction(user_id) as scoped:
            return PostgresIntentStore(scoped).delete(
                tenant_id, user_id, source_type, source_ref, market_stream_key
            )

    def list_for_subject(
        self, tenant_id: UUID, user_id: UUID
    ) -> Sequence[SubscriptionIntent]:
        with self._scoped_db.transaction(user_id) as scoped:
            return PostgresIntentStore(scoped).list_for_subject(tenant_id, user_id)


class _TimeframesBinance:
    """Los intervalos que Binance sirve de verdad. Es una CAPACIDAD DEL ADAPTADOR
    (ADR-008), no un dato de la base; se declara aqui para que el registry valide 1m.
    """

    def timeframes_for(self, exchange: str) -> frozenset[Timeframe]:
        if exchange == "binance":
            return frozenset(Timeframe)  # los seis divisores exactos del dia
        return frozenset()


def _usuario_para_email(migrations_db: Database, app_db: Database, email: str) -> UUID:
    """El usuario de demo, dado de alta por la ventanilla si aun no existe.

    La busqueda va con el rol de MIGRACIONES: el de aplicacion no tiene ningun
    privilegio de tabla sobre app_user (CA-07), solo puede EJECUTAR la ventanilla.
    """
    with migrations_db.transaction() as session:
        row = session.fetchone(
            "SELECT user_id FROM app_user WHERE email = %s", (email,)
        )
    if row is not None:
        return UUID(str(row[0]))
    return register_user(app_db, email, _PASSWORD_HASH)


def _tenant_para_usuario(
    migrations_db: Database, app_db: Database, user_id: UUID
) -> UUID:
    """El tenant del usuario; lo crea (rol de app, bajo RLS) si aun no tiene."""
    with migrations_db.transaction() as session:
        row = session.fetchone(
            "SELECT tenant_id FROM user_tenant_membership WHERE user_id = %s",
            (str(user_id),),
        )
    if row is not None:
        return UUID(str(row[0]))
    return provision_tenant_for_user(app_db, user_id)


def _limpiar_intents(scoped_db: TenantScopedDatabase, tenant: UUID, user: UUID) -> None:
    """Borra TODOS los intents del sujeto, bajo su propio contexto RLS. Deja la base
    como estaba. Un DELETE dirigido a otro tenant no veria sus filas (RLS): aqui solo
    toca las del sujeto en curso.
    """
    with scoped_db.transaction(user) as scoped:
        scoped.session.execute(
            "DELETE FROM market_subscription_intent "
            "WHERE tenant_id = %s AND user_id = %s",
            (str(tenant), str(user)),
        )


def _alta(
    registry: MarketInterestRegistry, tenant: UUID, user: UUID, source_ref: str
) -> None:
    registry.add(
        tenant_id=tenant,
        user_id=user,
        stream_scope=StreamScope.PUBLIC_MARKET,
        stream_key=_CLAVE,
        source_type=IntentSourceType.WIDGET,
        source_ref=source_ref,
    )


def _baja(
    registry: MarketInterestRegistry, tenant: UUID, user: UUID, source_ref: str
) -> bool:
    return registry.remove(
        tenant_id=tenant,
        user_id=user,
        source_type=IntentSourceType.WIDGET,
        source_ref=source_ref,
        stream_key=_CLAVE,
    )


def _nuevo_mundo(
    demand: _DemandaEnIngesta, clock: Clock
) -> tuple[RecordingStreamController, SubscriptionManager]:
    """Un controller y un manager NUEVOS (sin memoria): el reinicio del worker."""
    controller = RecordingStreamController(clock)
    manager = SubscriptionManager(
        demand=demand,
        controller=controller,
        clock=clock,
        hysteresis=HysteresisConfig(off_delay_ms=_OFF_DELAY_MS),
    )
    return controller, manager


def _escenario_obligatorio(
    registry: MarketInterestRegistry,
    demand: _DemandaEnIngesta,
    clock: SimulatedClock,
    m: _Marcador,
    tenant_a: UUID,
    user_a: UUID,
) -> None:
    print(
        "\n=== VALIDACION OBLIGATORIA (Roadmap): alta/baja de interes "
        "enciende/apaga por ref-count ==="
    )
    controller, manager = _nuevo_mundo(demand, clock)

    _alta(registry, tenant_a, user_a, "obligatorio")
    r = manager.reconcile()
    print(
        f"  alta de A -> reconcile: opened={r.opened} "
        f"ref-count={r.ref_counts.get(_CLAVE_STR)}"
    )
    m.veredicto(
        _CLAVE_STR in r.opened
        and controller.opens == 1
        and r.ref_counts.get(_CLAVE_STR) == 1,
        "un alta ABRE el stream y ref-count = 1",
    )

    _baja(registry, tenant_a, user_a, "obligatorio")
    r = manager.reconcile()
    print(
        f"  baja de A -> reconcile: ref-count={r.ref_counts.get(_CLAVE_STR, 0)} "
        f"pending_close={r.pending_close} closes={controller.closes}"
    )
    m.veredicto(
        _CLAVE_STR in r.pending_close
        and _CLAVE_STR not in r.ref_counts
        and controller.closes == 0,
        "una baja deja ref-count = 0 y el cierre PENDIENTE (histeresis)",
    )

    clock.advance(_OFF_DELAY_MS + 1)
    r = manager.reconcile()
    print(
        f"  reloj +{_OFF_DELAY_MS + 1}ms -> reconcile: closed={r.closed} "
        f"closes={controller.closes}"
    )
    m.veredicto(
        _CLAVE_STR in r.closed and controller.closes == 1,
        "pasada la histeresis, la baja CIERRA el stream",
    )


def _escenario_sin_duplicar(
    registry: MarketInterestRegistry,
    demand: _DemandaEnIngesta,
    clock: SimulatedClock,
    m: _Marcador,
    tenant_a: UUID,
    user_a: UUID,
    tenant_b: UUID,
    user_b: UUID,
) -> None:
    print("\n=== ADICION (a): los publicos NO se duplican por tenant ===")
    _alta(registry, tenant_a, user_a, "adic-a")
    _alta(registry, tenant_b, user_b, "adic-a")

    mapa = demand.snapshot()
    print(f"  ventanilla (solo CUANTOS, jamas QUIENES): {dict(mapa)}")
    m.veredicto(
        mapa.get(_CLAVE_STR) == 2 and len(mapa) == 1,
        "dos tenants distintos, UNA sola clave con intent_count = 2",
    )

    controller, manager = _nuevo_mundo(demand, clock)
    r = manager.reconcile()
    print(
        f"  reconcile: opened={r.opened} streams_abiertos={len(controller.active())} "
        f"ref-count={r.ref_counts.get(_CLAVE_STR)}"
    )
    m.veredicto(
        list(r.opened).count(_CLAVE_STR) == 1
        and controller.opens == 1
        and len(controller.active()) == 1
        and r.ref_counts.get(_CLAVE_STR) == 2,
        "UN solo open para dos tenants; streams abiertos = 1; ref-count = 2",
    )
    m.veredicto(
        set(mapa.keys()) == {_CLAVE_STR},
        "el worker NO sabe QUIENES son: la ventanilla solo dio {clave: contador}",
    )

    _baja(registry, tenant_a, user_a, "adic-a")
    r = manager.reconcile()
    print(
        f"  A se retira -> reconcile: ref-count={r.ref_counts.get(_CLAVE_STR)} "
        f"closes={controller.closes} activo={_CLAVE_STR in controller.active()}"
    )
    m.veredicto(
        controller.closes == 0
        and r.ref_counts.get(_CLAVE_STR) == 1
        and _CLAVE_STR in controller.active(),
        "al irse un tenant, ref-count = 1 y CERO close: el stream SIGUE VIVO",
    )


def _escenario_reinicio(
    registry: MarketInterestRegistry,
    demand: _DemandaEnIngesta,
    clock: SimulatedClock,
    m: _Marcador,
    limpiar: Callable[[], None],
    tenant_a: UUID,
    user_a: UUID,
    tenant_b: UUID,
    user_b: UUID,
) -> None:
    print("\n=== ADICION (b): reconstruccion tras reinicio ===")
    limpiar()  # parte de cero para que el "deseado" sea EXACTAMENTE A + B
    _alta(registry, tenant_a, user_a, "adic-b")
    _alta(registry, tenant_b, user_b, "adic-b")
    n_a = len(registry.list_for_subject(tenant_a, user_a))
    n_b = len(registry.list_for_subject(tenant_b, user_b))
    print(f"  intereses persistidos: A={n_a} B={n_b}")

    # DESCARTA manager y controller: reinicio del worker, SIN memoria. El controller
    # nuevo reporta CERO streams activos (nada que "recordar", solo que "reconstruir").
    controller, manager = _nuevo_mundo(demand, clock)
    print(
        f"  worker reiniciado: streams activos en memoria = {len(controller.active())}"
    )
    r = manager.reconcile()
    print(
        f"  reconcile desde la BASE: opened={r.opened} "
        f"activos={sorted(controller.active())}"
    )
    m.veredicto(
        list(r.opened) == [_CLAVE_STR]
        and controller.opens == 1
        and controller.active() == {_CLAVE_STR},
        "abre EXACTAMENTE las claves deseadas reconstruidas desde los intents, "
        "sin duplicar",
    )


def _escenario_histeresis(
    registry: MarketInterestRegistry,
    demand: _DemandaEnIngesta,
    clock: SimulatedClock,
    m: _Marcador,
    limpiar: Callable[[], None],
    tenant_a: UUID,
    user_a: UUID,
) -> None:
    print("\n=== ADICION (c): histeresis (anti-flapping) ===")
    limpiar()  # un solo sujeto controla la demanda: asi el flapping es limpio
    _alta(registry, tenant_a, user_a, "flap")
    controller, manager = _nuevo_mundo(demand, clock)
    manager.reconcile()  # parte de un stream ABIERTO
    print(
        f"  stream abierto de partida: activos={sorted(controller.active())} "
        f"closes={controller.closes}"
    )

    # alta/baja RAPIDAS, avanzando el reloj SIEMPRE por debajo de off_delay entre cada
    # una: cada vez que vuelve la demanda se cancela el cierre pendiente, y nunca se
    # cumple el plazo. El stream no debe parpadear ni una vez.
    paso = _OFF_DELAY_MS // 6
    for accion in ("baja", "alta", "baja", "alta", "baja", "alta"):
        if accion == "alta":
            _alta(registry, tenant_a, user_a, "flap")
        else:
            _baja(registry, tenant_a, user_a, "flap")
        clock.advance(paso)
        r = manager.reconcile()
        print(
            f"    {accion}: reloj +{paso}ms "
            f"ref-count={r.ref_counts.get(_CLAVE_STR, 0)} "
            f"pending={r.pending_close} closes={controller.closes}"
        )

    m.veredicto(
        controller.closes == 0,
        "pese a la demanda intermitente, CERO close en todo el episodio",
    )


def main() -> None:
    _exigir_dsn()

    # CONEXIONES. Cada cargador ve SOLO su DSN (guardia 5.20, leccion de B9): es el
    # entorno que su proceso real portaria, no una puerta trasera. Migraciones no tiene
    # guardia (nunca corre en runtime), asi que lee el entorno tal cual.
    app_db = PsycopgDatabase(DbConfig.from_env(_solo(DSN_ENV_VAR)))
    migrations_db = PsycopgDatabase(DbConfig.migrations_from_env())
    ingestion_dsn = IngestionDbConfig.from_env(_solo(INGESTION_DSN_ENV_VAR)).dsn
    ingestion_db = PsycopgDatabase(DbConfig(dsn=ingestion_dsn))

    m = _Marcador()
    try:
        # PREPARACION (idempotente). El catalogo lo escribe el rol de INGESTA (5.20);
        # sin el par en el catalogo, el registry rechazaria el intent (control de
        # seguridad, no comodidad).
        catalogo = _CatalogoEnIngesta(ingestion_db)
        catalogo.upsert("binance", "spot", "BTC-USDT", "BTCUSDT", "active")

        user_a = _usuario_para_email(migrations_db, app_db, _EMAIL_A)
        user_b = _usuario_para_email(migrations_db, app_db, _EMAIL_B)
        tenant_a = _tenant_para_usuario(migrations_db, app_db, user_a)
        tenant_b = _tenant_para_usuario(migrations_db, app_db, user_b)
        print("Preparacion OK (datos de juguete, jamas reales):")
        print("  catalogo : binance/spot/BTC-USDT 1m activo")
        print(f"  A : {_EMAIL_A}  tenant={tenant_a}")
        print(f"  B : {_EMAIL_B}  tenant={tenant_b}")

        scoped_db = TenantScopedDatabase(app_db)
        registry = MarketInterestRegistry(
            catalog=catalogo,
            store=_IntentStoreEnApp(scoped_db),
            timeframes=_TimeframesBinance(),
            clock=SimulatedClock(start_ms=_AHORA),
        )
        demand = _DemandaEnIngesta(ingestion_db)

        def limpiar() -> None:
            _limpiar_intents(scoped_db, tenant_a, user_a)
            _limpiar_intents(scoped_db, tenant_b, user_b)

        # LIMPIA al empezar: partir de cero pese a re-ejecuciones.
        limpiar()

        # El reloj de los escenarios (compartido: avanza hacia delante). El registry usa
        # su propio reloj para el created_at de los intents; el manager y la histeresis
        # usan ESTE, que es el que movemos a mano.
        clock = SimulatedClock(start_ms=_AHORA)

        _escenario_obligatorio(registry, demand, clock, m, tenant_a, user_a)
        _escenario_sin_duplicar(
            registry, demand, clock, m, tenant_a, user_a, tenant_b, user_b
        )
        _escenario_reinicio(
            registry, demand, clock, m, limpiar, tenant_a, user_a, tenant_b, user_b
        )
        _escenario_histeresis(registry, demand, clock, m, limpiar, tenant_a, user_a)

        # LIMPIEZA final: deja la base como estaba.
        limpiar()
        print("\nLIMPIEZA OK")
    finally:
        app_db.close()
        migrations_db.close()
        ingestion_db.close()

    print()
    if m.fallos:
        print(
            f"VALIDACION EN CALIENTE P07: FALLIDA ({m.fallos} veredicto(s) en rojo). "
            "Una validacion que miente es peor que ninguna.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print("VALIDACION EN CALIENTE P07: TODO EN VERDE. Las cuatro obligatorias, vivas.")


if __name__ == "__main__":
    main()
