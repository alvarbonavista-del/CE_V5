"""Tests de integracion del CICLO NUCLEO del motor de reglas (CA-P08-01/02/06/07).

Lo que los unitarios con mocks NO prueban: el camino ENTERO contra PostgreSQL real,
con role-switching de verdad (la AUTORIA escribe con ce_v5_app, el MOTOR procesa con
ce_v5_rules bajo sesion system-driven). Se demuestra el DoD de M3 en su forma minima:
una vela cerrada entra, la regla evalua, la FSM transiciona, y el estado + los eventos
de la transicion (rule.evaluation_completed + rule.firing + la proyeccion alert.raised)
quedan escritos EN LA MISMA TRANSACCION.

Y la propiedad que sostiene todo lo demas: LA ATOMICIDAD. Si el encolado del outbox
falla, el estado NO avanza. No se demuestra con un mock del cursor: se demuestra
haciendo que el MOTOR rechace el evento (la policy de outbox de 0013 prohibe a
ce_v5_rules encolar familias ajenas) y comprobando que la fila de estado se quedo como
estaba. Un rollback fingido por un mock probaria que el mock funciona; esto prueba que
PostgreSQL funciona.

Base de JUGUETE: nunca datos reales (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from ce_v5.entrypoints.worker_rules.cycle import process_rule_cycle
from ce_v5.infra.db.outbox import OutboxEvent
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.rules import (
    LifecycleOperational,
    discover_rules,
    read_state,
    record_transition,
)
from ce_v5.infra.db.tenancy import SystemScopedDatabase
from ce_v5.platform.rules.catalog import DataSourceCatalog
from ce_v5.platform.rules.compiler import compile
from ce_v5.platform.rules.rawclose import (
    MARKET_CLOSE_SOURCE_ID,
    market_close_declaration,
)
from ce_v5.platform.rules.runtime import RuntimeState
from source.families.rule import EvaluationLifecycleState
from source.rules.market_rules import AnyRule, RuleProduct

_DSN = os.environ.get("CE_V5_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None, reason="requiere CE_V5_DATABASE_URL (PostgreSQL local)"
)

_OPEN_TIME = 1_784_073_600_000

# close 40000 > 30000 -> TRUE (dispara). close 20000 -> FALSE (resuelve).
_DISPARA = {MARKET_CLOSE_SOURCE_ID: (Decimal("40000"),)}
_NO_DISPARA = {MARKET_CLOSE_SOURCE_ID: (Decimal("20000"),)}

_EVENTOS_DE = (
    "SELECT event_type, event_id::text, envelope FROM outbox "
    "WHERE stream_key = %s ORDER BY event_type"
)


@pytest.fixture
def limpiar_outbox(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """rule_definition y rule_lifecycle_state caen en cascada con el tenant (0013);
    la outbox no cuelga de nadie y hay que limpiarla a mano."""

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM outbox")

    _wipe()
    yield
    _wipe()


@pytest.fixture
def catalogo() -> DataSourceCatalog:
    """El catalogo minimo de v5.0: market.close, la unica fuente POINT-LOCAL."""
    catalog = DataSourceCatalog()
    catalog.register(market_close_declaration())
    catalog.validate()
    return catalog


type Sobre = dict[str, object]


def _eventos(
    migrator_db: PsycopgDatabase, rule_id: UUID
) -> list[tuple[str, str, Sobre]]:
    """Lee la outbox con el rol de MIGRACIONES (visibilidad total, sin RLS)."""
    with migrator_db.transaction() as session:
        rows = session.fetchall(_EVENTOS_DE, (f"rule:{rule_id}",))
    return [
        (str(r[0]), str(r[1]), r[2] if isinstance(r[2], dict) else {}) for r in rows
    ]


def _payload(envelope: Sobre) -> Sobre:
    payload = envelope.get("payload")
    return payload if isinstance(payload, dict) else {}


def _operacional_limpio() -> LifecycleOperational:
    """El carrier operacional en su forma neutra (sin stale ni cuarentena).

    LifecycleOperational no tiene valores por defecto A PROPOSITO: el estado
    operacional se escribe SIEMPRE entero (CA-P08-05), y un default silencioso
    reiniciaria contadores que la FSM necesita acumular entre velas.
    """
    return LifecycleOperational(
        not_evaluable_count=0,
        consecutive_exceptions=0,
        is_stale=False,
        stale_reason=None,
        is_quarantined=False,
        quarantine_reason=None,
        last_technical_error=None,
    )


class TestLaVentanillaCrossTenant:
    def test_el_motor_descubre_la_regla_de_otro_tenant_por_la_ventanilla(
        self,
        rules_db: PsycopgDatabase,
        regla_autorizada: Callable[[RuleProduct], tuple[UUID, AnyRule]],
        limpiar_outbox: None,
    ) -> None:
        # CA-P08-03: el motor NO tiene privilegio sobre rule_definition, y aun asi ve la
        # regla -- porque la ventanilla SECURITY DEFINER se la da. Y le da el tenant de
        # la COLUMNA (autoritativo), que es el que luego scopea la escritura del estado.
        tenant, rule = regla_autorizada(RuleProduct.ALERT)

        with rules_db.transaction() as session:
            descubiertas = discover_rules(session, "binance", "BTC-USDT", "1h")

        mia = [d for d in descubiertas if d.rule_id == rule.rule_id]
        assert len(mia) == 1
        assert mia[0].tenant_id == tenant
        assert mia[0].product == RuleProduct.ALERT.value


class TestCicloNucleoEndToEnd:
    def test_la_vela_dispara_la_regla_y_proyecta_alert_raised(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        regla_autorizada: Callable[[RuleProduct], tuple[UUID, AnyRule]],
        catalogo: DataSourceCatalog,
        limpiar_outbox: None,
    ) -> None:
        # EL DoD DE M3 EN SU FORMA MINIMA: una Rule dispara sobre datos de mercado y
        # proyecta alert.* POR TRANSICION, con estado y eventos en un solo commit.
        tenant, rule = regla_autorizada(RuleProduct.ALERT)
        system_db = SystemScopedDatabase(rules_db)

        nuevo = process_rule_cycle(
            system_db,
            rule,
            compile(rule, catalogo),
            _DISPARA,
            RuntimeState(EvaluationLifecycleState.INACTIVE),
            _OPEN_TIME,
            tenant_id=tenant,
            rule_id=rule.rule_id,
        )

        assert nuevo.eval_state is EvaluationLifecycleState.FIRING

        eventos = _eventos(migrator_db, rule.rule_id)
        tipos = [t for t, _, _ in eventos]
        assert "rule.evaluation_completed" in tipos
        assert "rule.firing" in tipos
        assert "alert.raised" in tipos
        assert "signal.raised" not in tipos  # la proyeccion es POR PRODUCTO

        # ORDEN CAUSAL (ADR-015): la proyeccion cuelga del firing, jamas se emite sola.
        firing_id = next(eid for t, eid, _ in eventos if t == "rule.firing")
        raised = next(env for t, _, env in eventos if t == "alert.raised")
        assert raised.get("causation_id") == firing_id

        # El estado quedo PERSISTIDO y bajo el tenant autoritativo, no solo en memoria.
        with system_db.transaction(tenant) as scoped:
            estado = read_state(scoped.session, rule.rule_id)
        assert estado is not None
        assert estado.state == EvaluationLifecycleState.FIRING.value
        assert estado.tenant_id == tenant
        assert estado.last_evaluated_open_time == _OPEN_TIME

    def test_la_regla_de_senal_proyecta_signal_raised(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        regla_autorizada: Callable[[RuleProduct], tuple[UUID, AnyRule]],
        catalogo: DataSourceCatalog,
        limpiar_outbox: None,
    ) -> None:
        tenant, rule = regla_autorizada(RuleProduct.TRADING_SIGNAL)

        process_rule_cycle(
            SystemScopedDatabase(rules_db),
            rule,
            compile(rule, catalogo),
            _DISPARA,
            RuntimeState(EvaluationLifecycleState.INACTIVE),
            _OPEN_TIME,
            tenant_id=tenant,
            rule_id=rule.rule_id,
        )

        tipos = [t for t, _, _ in _eventos(migrator_db, rule.rule_id)]
        assert "signal.raised" in tipos
        assert "alert.raised" not in tipos

    def test_salir_de_firing_resuelve_y_no_proyecta(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        regla_autorizada: Callable[[RuleProduct], tuple[UUID, AnyRule]],
        catalogo: DataSourceCatalog,
        limpiar_outbox: None,
    ) -> None:
        # EMISION POR TRANSICION (CA-P08-01): el flanco de salida emite resolved y NO
        # vuelve a proyectar. Una alerta se levanta una vez, no en cada vela.
        tenant, rule = regla_autorizada(RuleProduct.ALERT)
        system_db = SystemScopedDatabase(rules_db)
        plan = compile(rule, catalogo)

        firing = process_rule_cycle(
            system_db,
            rule,
            plan,
            _DISPARA,
            RuntimeState(EvaluationLifecycleState.INACTIVE),
            _OPEN_TIME,
            tenant_id=tenant,
            rule_id=rule.rule_id,
        )
        resuelto = process_rule_cycle(
            system_db,
            rule,
            plan,
            _NO_DISPARA,
            firing,
            _OPEN_TIME + 60_000,
            tenant_id=tenant,
            rule_id=rule.rule_id,
        )

        assert resuelto.eval_state is EvaluationLifecycleState.RESOLVED
        eventos = _eventos(migrator_db, rule.rule_id)
        resolved = next(env for t, _, env in eventos if t == "rule.resolved")
        assert _payload(resolved).get("resolved_reason") == "condition_false"
        # UNA sola proyeccion en todo el ciclo: la del flanco de entrada.
        assert sum(1 for t, _, _ in eventos if t == "alert.raised") == 1

    def test_reprocesar_la_misma_vela_no_reemite(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        regla_autorizada: Callable[[RuleProduct], tuple[UUID, AnyRule]],
        catalogo: DataSourceCatalog,
        limpiar_outbox: None,
    ) -> None:
        # Sin flanco no hay evento: at-least-once en el bus exige que reprocesar sea
        # inocuo. El estado se reescribe igual (los contadores pueden cambiar).
        tenant, rule = regla_autorizada(RuleProduct.ALERT)
        system_db = SystemScopedDatabase(rules_db)
        plan = compile(rule, catalogo)

        firing = process_rule_cycle(
            system_db,
            rule,
            plan,
            _DISPARA,
            RuntimeState(EvaluationLifecycleState.INACTIVE),
            _OPEN_TIME,
            tenant_id=tenant,
            rule_id=rule.rule_id,
        )
        antes = len(_eventos(migrator_db, rule.rule_id))

        process_rule_cycle(
            system_db,
            rule,
            plan,
            _DISPARA,
            firing,
            _OPEN_TIME,
            tenant_id=tenant,
            rule_id=rule.rule_id,
        )

        assert len(_eventos(migrator_db, rule.rule_id)) == antes


class TestAtomicidadEstadoMasOutbox:
    """LA PROPIEDAD (CA-P08-02 p.2): estado y outbox viven o mueren juntos."""

    def test_si_el_outbox_falla_el_estado_hace_rollback(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        regla_autorizada: Callable[[RuleProduct], tuple[UUID, AnyRule]],
        catalogo: DataSourceCatalog,
        limpiar_outbox: None,
    ) -> None:
        # Se deja la regla en FIRING (estado real, persistido). Luego se pide una
        # transicion a RESOLVED que arrastra un evento PROHIBIDO para ce_v5_rules
        # (execution.*, rechazado por el WITH CHECK de la policy de outbox de 0013).
        #
        # record_transition hace el UPSERT del estado ANTES de encolar. Si no hubiera
        # atomicidad, el estado ya estaria en 'resolved' y el evento no existiria: el
        # motor habria "olvidado" avisar de algo que ya dio por hecho -- una alerta que
        # el usuario nunca recibe, sin rastro de que falto. Con atomicidad, ninguna de
        # las dos cosas pasa.
        tenant, rule = regla_autorizada(RuleProduct.ALERT)
        system_db = SystemScopedDatabase(rules_db)

        process_rule_cycle(
            system_db,
            rule,
            compile(rule, catalogo),
            _DISPARA,
            RuntimeState(EvaluationLifecycleState.INACTIVE),
            _OPEN_TIME,
            tenant_id=tenant,
            rule_id=rule.rule_id,
        )
        eventos_antes = _eventos(migrator_db, rule.rule_id)
        assert eventos_antes  # el ciclo bueno si escribio

        evento_prohibido = OutboxEvent(
            event_id=uuid4(),
            idempotency_key=f"idem-{uuid4().hex}",
            stream_key=f"rule:{rule.rule_id}",
            event_type="execution.order_placed",
            envelope={},
        )

        with pytest.raises(Exception) as excinfo:
            record_transition(
                system_db,
                tenant_id=tenant,
                rule_id=rule.rule_id,
                new_state=EvaluationLifecycleState.RESOLVED.value,
                last_evaluated_open_time=_OPEN_TIME + 60_000,
                operational=_operacional_limpio(),
                events=[evento_prohibido],
            )
        assert "row-level security" in str(excinfo.value).lower()

        # (a) El ESTADO no avanzo: sigue FIRING, con el open_time viejo.
        with system_db.transaction(tenant) as scoped:
            estado = read_state(scoped.session, rule.rule_id)
        assert estado is not None
        assert estado.state == EvaluationLifecycleState.FIRING.value
        assert estado.last_evaluated_open_time == _OPEN_TIME

        # (b) Y la outbox quedo exactamente como estaba: ni el evento prohibido, ni
        #     ningun otro rastro de la transicion abortada.
        assert _eventos(migrator_db, rule.rule_id) == eventos_antes

    def test_el_estado_no_nace_si_el_primer_evento_es_rechazado(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        regla_autorizada: Callable[[RuleProduct], tuple[UUID, AnyRule]],
        limpiar_outbox: None,
    ) -> None:
        # La otra cara: si la PRIMERA transicion de una regla falla al encolar, no debe
        # quedar ni la fila de estado. Un estado sin su evento es una regla que cree
        # haber disparado y de la que nadie se entero.
        tenant, rule = regla_autorizada(RuleProduct.ALERT)
        system_db = SystemScopedDatabase(rules_db)

        with pytest.raises(Exception) as excinfo:
            record_transition(
                system_db,
                tenant_id=tenant,
                rule_id=rule.rule_id,
                new_state=EvaluationLifecycleState.FIRING.value,
                last_evaluated_open_time=_OPEN_TIME,
                operational=_operacional_limpio(),
                events=[
                    OutboxEvent(
                        event_id=uuid4(),
                        idempotency_key=f"idem-{uuid4().hex}",
                        stream_key=f"rule:{rule.rule_id}",
                        event_type="policy.kill_switch_activated",
                        envelope={},
                    )
                ],
            )
        assert "row-level security" in str(excinfo.value).lower()

        with system_db.transaction(tenant) as scoped:
            assert read_state(scoped.session, rule.rule_id) is None
        assert _eventos(migrator_db, rule.rule_id) == []
