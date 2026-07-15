"""El CAMINO PRIVADO (BYOC), gateado y aislado (P07: privados con RLS y geo-gate).

Con el gate REAL (PolicyLifecycleGate + PolicyEvaluator + PostgresPolicyStore), el
Supervisor REAL y PostgreSQL REAL. Lo que se demuestra:

  A. Un sujeto SIN entitlement de connect_broker queda DENEGADO fail-closed: la
     instancia va a QUARANTINED y el initialize() del connector NO SE EJECUTA. La
     conexion privada no llega a abrirse.
  B. Un sujeto CON entitlement inicializa y llega a RUNNING.
  C. Los intereses PRIVADOS estan aislados por RLS y JAMAS aparecen en la demanda
     publica.
  D. La denegacion es OBSERVABLE: component.quarantined con su reason_code.

SOBRE LA JURISDICCION: los inputs reales de geo/KYC/VPN los aporta P06b desde la sesion
verificada, y hoy NO hay proveedor comercial detras (esa seleccion es frontera de
decision de Alvaro). Sin proveedor, lo desconocido DENIEGA lo sensible (D5/D6), que es
la respuesta correcta. P07 demuestra EL CAMINO DE ENFORCEMENT con connect_broker, no la
fuente del dato de jurisdiccion: por eso aqui los inputs se fijan explicitamente y el
UNICO diferenciador entre el usuario bloqueado y el habilitado es SU ENTITLEMENT.

AQUI NO SE FABRICA NINGUN execution.*: el hecho privado real (fill, balance) es de esa
familia, y esa familia la define y la produce P10b. Inventarla seria falsificar un
contrato ajeno (CA-04: jamas fabricar hechos).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ce_v5.components.market_connector_private_fake import build
from ce_v5.core.bus import EventBus
from ce_v5.core.clock import SystemClock
from ce_v5.core.component import (
    ComponentDefinition,
    LifecycleScope,
    LifecycleState,
    Supervisor,
)
from ce_v5.core.manifest import validate_manifest
from ce_v5.core.policy import (
    CachedPolicyEvaluator,
    CapabilitySetCache,
    PolicyEvaluator,
)
from ce_v5.core.policy.inputs import (
    EvidenceSource,
    KycStatus,
    PolicyInputs,
    ResolvedJurisdiction,
)
from ce_v5.core.policy.lifecycle_gate import PolicyLifecycleGate
from ce_v5.infra.db.market_store import PostgresIntentStore, PostgresPublicDemand
from ce_v5.infra.db.policy_store import PostgresPolicyStore
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.tenancy import TenantScopedDatabase, provision_tenant_for_user
from source.envelope import Envelope
from source.families.component import ComponentLifecyclePayload
from source.families.market import (
    IntentSourceType,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    StreamScope,
    SubscriptionIntent,
    Timeframe,
)
from support.inmemory_bus import InMemoryEventBus, LogicalClock

_DSN = os.environ.get("CE_V5_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None, reason="requiere CE_V5_DATABASE_URL (PostgreSQL local)"
)

_CAPABILITY = "connect_broker"  # SENSIBLE (lista cerrada de P06, D1).
_POLICY_VERSION = "p07-private-fake"
_SOURCE = "test.p07.private"
_AHORA = 1_784_073_600_000

_CLAVE_PRIVADA = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.CANDLES,
    timeframe=Timeframe.M1,
)


class _FeedPrivadoFalso:
    """Feed privado FAKE: sin IO, sin broker, sin credenciales."""

    def __init__(self) -> None:
        self.conectado = False
        self.conexiones = 0

    def connect(self) -> None:
        self.conectado = True
        self.conexiones += 1

    def disconnect(self) -> None:
        self.conectado = False

    def connected(self) -> bool:
        return self.conectado


class _ResolverDeSujeto:
    """Inputs FIJOS: el unico diferenciador entre sujetos es su ENTITLEMENT.

    Si los inputs fueran desconocidos (sin proveedor de geo/KYC/VPN), D5/D6 denegarian
    a TODOS y el caso B no podria distinguirse del A. Fijarlos aqui aisla lo que esta
    pieza demuestra: el CAMINO del gate, no la fuente del dato de jurisdiccion.
    """

    def resolve(self, tenant_id: str, user_id: str | None) -> PolicyInputs:
        return PolicyInputs(
            subject_tenant_id=tenant_id,
            subject_user_id=user_id,
            jurisdiction=ResolvedJurisdiction(
                jurisdiction="ES", source=EvidenceSource.KYC, conflicting=False
            ),
            kyc_status=KycStatus.VERIFIED,
            vpn_detected=False,
            plan=None,
            role=None,
        )


@pytest.fixture
def limpiar_politica(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """La politica de este test se siembra y se retira: no contamina a los demas."""

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute(
                "DELETE FROM policy_rule WHERE policy_version = %s", (_POLICY_VERSION,)
            )
            session.execute(
                "DELETE FROM policy_version WHERE policy_version = %s",
                (_POLICY_VERSION,),
            )

    _wipe()
    yield
    _wipe()


def _sembrar_politica(migrator_db: PsycopgDatabase) -> None:
    """policy_version vigente + regla ALLOW de connect_broker (como seed_p06_fake).

    La regla ALLOW por si sola NO basta para una capacidad SENSIBLE: D6 exige
    entitlement EXPLICITO. Esa es justo la diferencia entre el caso A y el B.
    """
    with migrator_db.transaction() as session:
        session.execute(
            "UPDATE policy_version SET status = 'superseded' WHERE status = 'current'"
        )
        session.execute(
            "INSERT INTO policy_version (policy_version, status, actor) "
            "VALUES (%s, 'current', %s) "
            "ON CONFLICT (policy_version) DO UPDATE SET status = 'current'",
            (_POLICY_VERSION, _SOURCE),
        )
        session.execute(
            "INSERT INTO policy_rule (rule_id, policy_version, capability_id, "
            "effect, reason_code) VALUES (%s, %s, %s, 'allow', 'allowed_by_policy')",
            (str(uuid4()), _POLICY_VERSION, _CAPABILITY),
        )


def _conceder_entitlement(
    migrator_db: PsycopgDatabase, tenant_id: UUID, user_id: UUID
) -> None:
    with migrator_db.transaction() as session:
        session.execute(
            "INSERT INTO policy_entitlement (entitlement_id, tenant_id, user_id, "
            "capability_id, source) VALUES (%s, %s, %s, %s, 'admin')",
            (str(uuid4()), str(tenant_id), str(user_id), _CAPABILITY),
        )


def _definicion() -> ComponentDefinition:
    """El manifest REAL del componente, leido de su carpeta (no uno inventado)."""
    carpeta = (
        Path(__file__).resolve().parents[2]
        / "backend"
        / "src"
        / "ce_v5"
        / "components"
        / "market_connector_private_fake"
    )
    datos = json.loads((carpeta / "manifest.json").read_text(encoding="utf-8"))
    return ComponentDefinition(manifest=validate_manifest(datos), path=carpeta)


def _gate(app_db: PsycopgDatabase) -> PolicyLifecycleGate:
    """El gate REAL, cableado como en el composition root."""
    store = PostgresPolicyStore(app_db)
    clock = SystemClock()
    evaluator = CachedPolicyEvaluator(
        PolicyEvaluator(store, clock),
        CapabilitySetCache(clock, max_staleness_ms=60_000),
    )
    return PolicyLifecycleGate(
        evaluator=evaluator, kill_switches=store, resolver=_ResolverDeSujeto()
    )


def _tenant_de(app_db: PsycopgDatabase, user_id: UUID) -> UUID:
    scoped_db = TenantScopedDatabase(app_db)
    with scoped_db.transaction(user_id) as scoped:
        return scoped.context.tenant_id


def _envelopes(bus: EventBus) -> list[Envelope[ComponentLifecyclePayload]]:
    recibidos = bus.replay("component", start=None, max_messages=1000)
    return [
        Envelope[ComponentLifecyclePayload].model_validate_json(r.message.envelope)
        for r in recibidos
    ]


def _supervisor(bus: EventBus, app_db: PsycopgDatabase) -> Supervisor:
    return Supervisor(bus, SystemClock(), source=_SOURCE, gate=_gate(app_db))


class TestElGateCortaElCaminoPrivado:
    def test_a_un_sujeto_sin_entitlement_no_llega_a_conectar(
        self,
        app_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
        limpiar_politica: None,
    ) -> None:
        # CASO A. La regla de politica PERMITE connect_broker... pero es una capacidad
        # SENSIBLE, y una sensible exige ENTITLEMENT EXPLICITO (D6): no se concede
        # "porque ninguna regla la prohibe". Sin entitlement: DENY fail-closed.
        _sembrar_politica(migrator_db)
        user = crear_usuario()
        provision_tenant_for_user(app_db, user)
        tenant = _tenant_de(app_db, user)

        bus = InMemoryEventBus(clock=LogicalClock())
        sup = _supervisor(bus, app_db)
        feed = _FeedPrivadoFalso()
        connector = build(feed)
        inst = sup.register(
            _definicion(),
            connector,
            scope=LifecycleScope.USER,
            instance_id="privado-bloqueado",
            tenant_id=str(tenant),
            user_id=str(user),
        )

        sup.initialize("privado-bloqueado")

        assert inst.state is LifecycleState.QUARANTINED
        # LO QUE IMPORTA: el enganche NO se ejecuto, asi que NO SE ABRIO NINGUNA
        # CONEXION PRIVADA. El gate no "avisa despues": impide.
        assert feed.conexiones == 0
        assert feed.conectado is False
        assert connector.status().initialized is False

    def test_d_la_denegacion_es_observable_en_el_bus(
        self,
        app_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
        limpiar_politica: None,
    ) -> None:
        # CASO D. Un camino privado denegado en silencio seria indistinguible de uno que
        # nadie pidio. Se emite component.quarantined con su reason_code.
        _sembrar_politica(migrator_db)
        user = crear_usuario()
        provision_tenant_for_user(app_db, user)
        tenant = _tenant_de(app_db, user)

        bus = InMemoryEventBus(clock=LogicalClock())
        sup = _supervisor(bus, app_db)
        sup.register(
            _definicion(),
            build(_FeedPrivadoFalso()),
            scope=LifecycleScope.USER,
            instance_id="privado-observable",
            tenant_id=str(tenant),
            user_id=str(user),
        )
        sup.initialize("privado-observable")

        ultimo = _envelopes(bus)[-1]
        assert ultimo.event_type == "component.quarantined"
        assert ultimo.payload.new_state is LifecycleState.QUARANTINED
        assert ultimo.payload.error_code is not None  # el reason_code REAL del motor.

    def test_b_un_sujeto_con_entitlement_inicializa_y_arranca(
        self,
        app_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
        limpiar_politica: None,
    ) -> None:
        # CASO B. Mismo componente, misma politica: el UNICO cambio es que este sujeto
        # SI tiene el entitlement. Ahora el gate PERMITE y el camino privado se abre.
        _sembrar_politica(migrator_db)
        user = crear_usuario()
        provision_tenant_for_user(app_db, user)
        tenant = _tenant_de(app_db, user)
        _conceder_entitlement(migrator_db, tenant, user)

        bus = InMemoryEventBus(clock=LogicalClock())
        sup = _supervisor(bus, app_db)
        feed = _FeedPrivadoFalso()
        connector = build(feed)
        inst = sup.register(
            _definicion(),
            connector,
            scope=LifecycleScope.USER,
            instance_id="privado-habilitado",
            tenant_id=str(tenant),
            user_id=str(user),
        )

        sup.initialize("privado-habilitado")
        tras_initialize: LifecycleState = inst.state
        assert tras_initialize is LifecycleState.INITIALIZED
        assert feed.conexiones == 1  # AHORA si se "conecta" (fake, sin IO).

        sup.start("privado-habilitado")
        tras_start: LifecycleState = inst.state
        assert tras_start is LifecycleState.RUNNING
        assert connector.running is True

        # Y NO SE EMITIO NINGUN EVENTO DE DOMINIO: solo component.* del lifecycle. El
        # hecho privado real (fill, balance) es execution.*, familia de P10b.
        tipos = [e.event_type for e in _envelopes(bus)]
        assert all(t.startswith("component.") for t in tipos)
        assert not any(t.startswith("execution.") for t in tipos)


class TestAislamientoDeLoPrivado:
    def test_c_un_intent_privado_no_lo_ve_otro_sujeto_ni_la_demanda_publica(
        self,
        app_db: PsycopgDatabase,
        ingestion_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
    ) -> None:
        # CASO C. Lo privado es del sujeto y de nadie mas:
        #  1) otro usuario NO lo ve (RLS);
        #  2) NO aparece en la ventanilla de demanda PUBLICA. El worker publico agrega
        #     flujos compartidos; jamas aprende que pide un usuario en privado.
        user_a, user_b = crear_usuario(), crear_usuario()
        provision_tenant_for_user(app_db, user_a)
        provision_tenant_for_user(app_db, user_b)
        tenant_a = _tenant_de(app_db, user_a)
        tenant_b = _tenant_de(app_db, user_b)

        privado = SubscriptionIntent(
            intent_id=uuid4(),
            tenant_id=tenant_a,
            user_id=user_a,
            stream_scope=StreamScope.USER,  # PRIVADO
            stream_key=_CLAVE_PRIVADA,
            source_type=IntentSourceType.DATASOURCE,
            source_ref="byoc-fake",
            created_at=_AHORA,
            updated_at=_AHORA,
        )
        scoped_db = TenantScopedDatabase(app_db)
        with scoped_db.transaction(user_a) as scoped:
            PostgresIntentStore(scoped).insert(privado)

        # (1) El otro sujeto no lo ve: la RLS no le deja.
        with scoped_db.transaction(user_b) as scoped:
            store_b = PostgresIntentStore(scoped)
            assert store_b.count_for_subject(tenant_a, user_a) == 0
            assert store_b.list_for_subject(tenant_a, user_a) == []
            assert store_b.count_for_subject(tenant_b, user_b) == 0

        # (2) Y no suma en la demanda publica que ve el worker de ingesta.
        with ingestion_db.transaction() as session:
            demanda = PostgresPublicDemand(session).snapshot()
        assert _CLAVE_PRIVADA.as_stream_key() not in demanda
