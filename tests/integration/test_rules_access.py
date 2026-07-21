"""Tests de integracion del MOTOR DE REGLAS: frontera 5.20 (CA-P08-02/03/07).

Paridad exacta con lo que P07 hizo por su frontera en test_market_access.py: lo que se
demuestra aqui NO lo hace Python, lo hace el MOTOR. Cada negativo comprueba que
PostgreSQL RECHAZA la operacion (permission denied / row-level security), no que una
funcion nuestra devuelva un error. Un guardia que viviera en el codigo lo saltaria un
descuido; viviendo en el motor, no hay descuido posible.

Estos tests son el PISO OBLIGATORIO que faltaba: hasta la regla 5.22 el check
tools/check_rules_access.py existia pero NO estaba enganchado en ci.yml, y no habia ni
un solo test de integracion de P08. El check cubre lo ESTATICO (el catalogo); esto cubre
lo que solo se ve EJECUTANDO contra Postgres real.

Base de JUGUETE: nunca datos reales (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from uuid import UUID, uuid4

import pytest

from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.tenancy import SystemScopedDatabase
from source.rules.market_rules import AnyRule, RuleProduct

_DSN = os.environ.get("CE_V5_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None, reason="requiere CE_V5_DATABASE_URL (PostgreSQL local)"
)

_OPEN_TIME = 1_784_073_600_000
_CLOSE_TIME = _OPEN_TIME + 59_999

_INSERT_CANDLE = """
INSERT INTO market_candle (
    idempotency_key, stream_key, exchange, market_type, symbol, timeframe,
    open_time, close_time, open, high, low, close, volume, maturity_state
) VALUES (%s, %s, 'binance', 'spot', 'BTC-USDT', '1m', %s, %s,
          100, 110, 95, 105, 12.5, 'closed')
"""

_INSERT_OUTBOX = """
INSERT INTO outbox (event_id, idempotency_key, stream_key, event_type, envelope)
VALUES (%s, %s, %s, %s, %s)
"""

_UPSERT_STATE = """
INSERT INTO rule_lifecycle_state (rule_id, tenant_id, state, last_evaluated_open_time)
VALUES (%s, %s, 'firing', %s)
ON CONFLICT (rule_id) DO UPDATE SET state = EXCLUDED.state
"""


@pytest.fixture
def limpiar_outbox(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """Limpia outbox y velas entre tests.

    rule_definition y rule_lifecycle_state NO se limpian aqui: caen en cascada al
    borrar el tenant (0013), y de eso ya se ocupa la fixture autouse de identidad.
    market_candle y outbox no cuelgan de nadie, asi que se acumularian y su clave
    unica chocaria -- el mismo defecto que dejo 837 tenants huerfanos en P06b.
    """

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM outbox")
            session.execute("DELETE FROM market_candle")

    _wipe()
    yield
    _wipe()


class TestElMotorSiHaceLoSuyo:
    """POSITIVOS: sin estos, un REVOKE de mas pasaria por 'seguridad' y el motor no
    podria evaluar. El check muerde en los dos sentidos y estos tests tambien."""

    def test_el_motor_lee_market_candle(
        self,
        rules_db: PsycopgDatabase,
        ingestion_db: PsycopgDatabase,
        limpiar_outbox: None,
    ) -> None:
        # D1 (migracion 0016): sin SELECT sobre market_candle el motor NO PUEDE EVALUAR.
        # La vela la escribe el INGESTOR (unico que fabrica market data, 5.20).
        clave = f"idem-{uuid4().hex}"
        with ingestion_db.transaction() as session:
            session.execute(
                _INSERT_CANDLE, (clave, "market:x", _OPEN_TIME, _CLOSE_TIME)
            )

        with rules_db.transaction() as session:
            row = session.fetchone(
                "SELECT close FROM market_candle WHERE idempotency_key = %s", (clave,)
            )
        assert row is not None

    def test_el_motor_escribe_su_estado_bajo_su_tenant(
        self,
        rules_db: PsycopgDatabase,
        regla_autorizada: Callable[[RuleProduct], tuple[UUID, AnyRule]],
        limpiar_outbox: None,
    ) -> None:
        # rule_lifecycle_state es SUYA: la escribe SOLO el motor (CA-P08-02 p.3), y bajo
        # el tenant AUTORITATIVO fijado por SystemScopedDatabase (el de la COLUMNA).
        tenant, rule = regla_autorizada(RuleProduct.ALERT)
        with SystemScopedDatabase(rules_db).transaction(tenant) as scoped:
            scoped.session.execute(
                _UPSERT_STATE, (str(rule.rule_id), str(tenant), _OPEN_TIME)
            )
            row = scoped.session.fetchone(
                "SELECT state FROM rule_lifecycle_state WHERE rule_id = %s",
                (str(rule.rule_id),),
            )
        assert row is not None and str(row[0]) == "firing"

    @pytest.mark.parametrize(
        "event_type",
        ["rule.firing", "rule.evaluation_completed", "signal.raised", "alert.raised"],
    )
    def test_el_motor_encola_sus_propias_familias(
        self, rules_db: PsycopgDatabase, event_type: str, limpiar_outbox: None
    ) -> None:
        with rules_db.transaction() as session:
            session.execute(
                _INSERT_OUTBOX,
                (str(uuid4()), f"idem-{uuid4().hex}", "rule:x", event_type, "{}"),
            )
            row = session.fetchone(
                "SELECT count(*) FROM outbox WHERE event_type = %s", (event_type,)
            )
        assert row is not None and int(str(row[0])) == 1


class TestElMotorNoEscribeLaAutoria:
    """NEGATIVO nuclear de CA-P08-02: quien evalua no se escribe sus propias reglas."""

    def test_el_motor_no_puede_insertar_una_regla(
        self, rules_db: PsycopgDatabase, limpiar_outbox: None
    ) -> None:
        # Si el motor pudiera AUTORARSE reglas, un motor comprometido se fabricaria la
        # senal que quisiera -- y en M5 eso son ORDENES REALES. Lo impide el MOTOR.
        with pytest.raises(Exception) as excinfo:
            with rules_db.transaction() as session:
                session.execute(
                    "INSERT INTO rule_definition "
                    "(rule_id, tenant_id, product, canonical_rule_hash, "
                    " schema_version, definition, enabled) "
                    "VALUES (%s, %s, 'alert', 'h', 1, '{}', true)",
                    (str(uuid4()), str(uuid4())),
                )
        assert "permission denied" in str(excinfo.value).lower()

    @pytest.mark.parametrize("operacion", ["SELECT", "UPDATE", "DELETE"])
    def test_el_motor_no_toca_rule_definition_fila_a_fila(
        self, rules_db: PsycopgDatabase, operacion: str, limpiar_outbox: None
    ) -> None:
        # Ni siquiera LEERLA directamente: su UNICO acceso a la autoria es la ventanilla
        # cross-tenant rules_for_market (CA-P08-03).
        sentencia = {
            "SELECT": "SELECT * FROM rule_definition",
            "UPDATE": "UPDATE rule_definition SET enabled = false",
            "DELETE": "DELETE FROM rule_definition",
        }[operacion]
        with pytest.raises(Exception) as excinfo:
            with rules_db.transaction() as session:
                session.execute(sentencia)
        assert "permission denied" in str(excinfo.value).lower()


class TestElMotorNoTocaHechosAjenos:
    """NEGATIVOS bidireccionales de 5.20: el motor no porta poder que no necesita."""

    @pytest.mark.parametrize(
        "sentencia",
        [
            # Identidad (P06b/CA-07).
            "SELECT * FROM app_user",
            "SELECT * FROM user_credential",
            "SELECT * FROM user_session",
            # Politica y kill switch (P06/CA-03).
            "SELECT * FROM policy_rule",
            "SELECT * FROM policy_entitlement",
            "SELECT * FROM policy_override",
            "SELECT * FROM kill_switch",
            # Auditoria (el motor no la lee ni la reescribe).
            "SELECT * FROM sensitive_action_audit",
            "SELECT * FROM operator_audit",
        ],
    )
    def test_el_motor_no_lee_identidad_politica_ni_auditoria(
        self, rules_db: PsycopgDatabase, sentencia: str, limpiar_outbox: None
    ) -> None:
        with pytest.raises(Exception) as excinfo:
            with rules_db.transaction() as session:
                session.fetchall(sentencia)
        assert "permission denied" in str(excinfo.value).lower()

    @pytest.mark.parametrize(
        "sentencia",
        [
            "SELECT * FROM market_instrument",
            "SELECT * FROM market_subscription_intent",
            "SELECT * FROM market_public_demand()",
        ],
    )
    def test_el_motor_no_ve_mas_mercado_que_las_velas(
        self, rules_db: PsycopgDatabase, sentencia: str, limpiar_outbox: None
    ) -> None:
        # NEGATIVOS de CA-P08-07 D1. El motor lee market_candle y NADA MAS de mercado:
        # no traduce simbolos nativos (market_instrument), el intent de una regla lo
        # escribe la AUTORIA (market_subscription_intent), y la ventanilla agregada es
        # del INGESTOR (market_public_demand).
        with pytest.raises(Exception) as excinfo:
            with rules_db.transaction() as session:
                session.fetchall(sentencia)
        assert "permission denied" in str(excinfo.value).lower()

    @pytest.mark.parametrize("operacion", ["INSERT", "UPDATE", "DELETE"])
    def test_el_motor_no_fabrica_ni_reescribe_market_data(
        self, rules_db: PsycopgDatabase, operacion: str, limpiar_outbox: None
    ) -> None:
        # La otra mitad de 5.20: quien CONSUME las velas no las PRODUCE. Si el motor
        # pudiera escribir una vela, se fabricaria el hecho que dispara su propia regla.
        sentencia = {
            "INSERT": _INSERT_CANDLE,
            "UPDATE": "UPDATE market_candle SET close = 1",
            "DELETE": "DELETE FROM market_candle",
        }[operacion]
        parametros = (
            (f"idem-{uuid4().hex}", "market:x", _OPEN_TIME, _CLOSE_TIME)
            if operacion == "INSERT"
            else None
        )
        with pytest.raises(Exception) as excinfo:
            with rules_db.transaction() as session:
                session.execute(sentencia, parametros)
        assert "permission denied" in str(excinfo.value).lower()


class TestOutboxDelMotorAcotadaPorElMotor:
    """La frontera de EXECUTION y BILLING no es una tabla (son de M5, aun no existen):
    es la POLICY de la outbox. Un motor comprometido no puede encolar el evento."""

    @pytest.mark.parametrize(
        ("stream_key", "event_type"),
        [
            ("execution:stream", "execution.order_placed"),
            ("policy:stream", "policy.kill_switch_activated"),
            ("market:stream", "market.candle_closed"),
            ("billing:stream", "billing.subscription_created"),
        ],
    )
    def test_el_motor_no_puede_fabricar_un_evento_ajeno(
        self,
        rules_db: PsycopgDatabase,
        stream_key: str,
        event_type: str,
        limpiar_outbox: None,
    ) -> None:
        # Mismo patron que la negativa del ingestor con execution (CA-04): lo rechaza el
        # WITH CHECK de la policy de outbox, es decir, el MOTOR.
        with pytest.raises(Exception) as excinfo:
            with rules_db.transaction() as session:
                session.execute(
                    _INSERT_OUTBOX,
                    (
                        str(uuid4()),
                        f"idem-{uuid4().hex}",
                        stream_key,
                        event_type,
                        "{}",
                    ),
                )
        assert "row-level security" in str(excinfo.value).lower()

    def test_el_motor_no_puede_borrar_de_la_outbox(
        self, rules_db: PsycopgDatabase, limpiar_outbox: None
    ) -> None:
        # Sin DELETE ni TRUNCATE (0013): un motor comprometido no puede borrar la
        # evidencia de lo que emitio.
        with pytest.raises(Exception) as excinfo:
            with rules_db.transaction() as session:
                session.execute("DELETE FROM outbox")
        assert "permission denied" in str(excinfo.value).lower()
