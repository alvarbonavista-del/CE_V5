"""Tests de integracion de MARKET DATA: regla 5.20 y ventanilla agregada (CA-P07-D/G).

Lo que se demuestra aqui NO lo hace Python: lo hace el MOTOR. Cada prueba negativa
comprueba que PostgreSQL RECHAZA la operacion (permission denied / violacion de policy),
no que una funcion nuestra devuelva un error. Si el guardia viviera en el codigo, un
descuido lo saltaria; viviendo en el motor, no hay descuido posible.

Base de JUGUETE: nunca datos reales (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from uuid import UUID, uuid4

import pytest

from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.tenancy import TenantScopedDatabase, provision_tenant_for_user

_DSN = os.environ.get("CE_V5_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None, reason="requiere CE_V5_DATABASE_URL (PostgreSQL local)"
)

# Ventana 1m alineada; el CHECK de la tabla rechaza una vela desalineada.
_OPEN_TIME = 1_784_073_600_000
_CLOSE_TIME = _OPEN_TIME + 59_999

_INSERT_INTENT = """
INSERT INTO market_subscription_intent (
    intent_id, tenant_id, user_id, stream_scope, market_stream_key,
    exchange, market_type, symbol, data_kind, timeframe, source_type, source_ref
) VALUES (%s, %s, %s, %s, %s, 'binance', 'spot', 'BTC-USDT', 'candles', '1m', %s, %s)
"""

_INSERT_CANDLE = """
INSERT INTO market_candle (
    idempotency_key, stream_key, exchange, market_type, symbol, timeframe,
    open_time, close_time, open, high, low, close, volume, maturity_state
) VALUES (%s, %s, 'binance', 'spot', 'BTC-USDT', '1m', %s, %s,
          100, 110, 95, 105, 12.5, 'closed')
"""

_INSERT_TRADE = """
INSERT INTO market_trade (
    exchange, market_type, symbol, trade_id, price, qty, aggressor_side, event_time
) VALUES ('binance', 'spot', 'BTC-USDT', %s, 100, 1.5, 'buy', %s)
"""

# Una sola celda; los totales de barra cuadran con ella y bar_delta = buy - sell, que es
# lo que exige market_footprint_totales_coherentes.
_INSERT_FOOTPRINT = """
INSERT INTO market_footprint (
    idempotency_key, stream_key, exchange, market_type, symbol, timeframe,
    open_time, close_time, cells, bar_buy_volume, bar_sell_volume, bar_delta,
    trade_count, maturity_state
) VALUES (%s, %s, 'binance', 'spot', 'BTC-USDT', '1m', %s, %s,
          '[{"price": "100", "buy_volume": "2", "sell_volume": "1", "delta": "1"}]',
          2, 1, 1, 3, 'closed')
"""

_INSERT_OUTBOX = """
INSERT INTO outbox (event_id, idempotency_key, stream_key, event_type, envelope)
VALUES (%s, %s, %s, %s, %s)
"""

# Sentencia de reescritura por tabla del historico: el UPDATE toca una columna real de
# cada una para que el rechazo sea por PRIVILEGIO y no por SQL invalido.
_REESCRITURAS: dict[tuple[str, str], str] = {
    ("market_trade", "UPDATE"): "UPDATE market_trade SET qty = 1 WHERE trade_id = 'x'",
    ("market_trade", "DELETE"): "DELETE FROM market_trade WHERE trade_id = 'x'",
    ("market_footprint", "UPDATE"): (
        "UPDATE market_footprint SET trade_count = 1 WHERE idempotency_key = 'x'"
    ),
    ("market_footprint", "DELETE"): (
        "DELETE FROM market_footprint WHERE idempotency_key = 'x'"
    ),
}
_TABLAS_HISTORICO_P07B: tuple[str, ...] = ("market_trade", "market_footprint")


@pytest.fixture
def limpiar_market(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """Limpia velas, trades, footprints y outbox entre tests (los intents caen por
    cascada al borrar app_user/tenant en la fixture autouse de identidad).

    market_candle NO tiene FK a nadie: si no se limpiara aqui, las velas se
    acumularian entre ejecuciones y la PK (idempotency_key) chocaria. Es el mismo
    defecto que dejo 837 tenants huerfanos en P06b; no se repite. market_trade y
    market_footprint (P07b) son iguales: sin FK y append-only, asi que se limpian por
    el mismo motivo. El borrado va con el rol de MIGRACIONES a proposito: a los roles
    de runtime el DELETE les esta revocado, que es justo lo que estos tests prueban.
    """

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_candle")
            session.execute("DELETE FROM market_trade")
            session.execute("DELETE FROM market_footprint")
            session.execute("DELETE FROM outbox")

    _wipe()
    yield
    _wipe()


def _dos_sujetos(
    app_db: PsycopgDatabase, crear_usuario: Callable[[], UUID]
) -> tuple[UUID, UUID]:
    """Dos usuarios, cada uno con su tenant (aislados)."""
    user_a, user_b = crear_usuario(), crear_usuario()
    provision_tenant_for_user(app_db, user_a)
    provision_tenant_for_user(app_db, user_b)
    return user_a, user_b


def _crear_intent(
    app_db: PsycopgDatabase,
    user_id: UUID,
    stream_key: str,
    *,
    stream_scope: str = "public_market",
    source_ref: str = "widget-1",
) -> None:
    """Alta de un interes CON el rol de aplicacion, bajo su contexto de tenant."""
    scoped_db = TenantScopedDatabase(app_db)
    with scoped_db.transaction(user_id) as scoped:
        scoped.session.execute(
            _INSERT_INTENT,
            (
                str(uuid4()),
                str(scoped.context.tenant_id),
                str(user_id),
                stream_scope,
                stream_key,
                "widget",
                source_ref,
            ),
        )


def _entero(valor: object) -> int:
    assert isinstance(valor, int)
    return valor


def _demanda(ingestion_db: PsycopgDatabase, stream_key: str) -> int | None:
    """Lo que el WORKER ve por la ventanilla: cuantos piden ese stream."""
    with ingestion_db.transaction() as session:
        row = session.fetchone(
            "SELECT out_intent_count FROM market_public_demand() "
            "WHERE out_market_stream_key = %s",
            (stream_key,),
        )
    return None if row is None else _entero(row[0])


class TestVentanillaAgregada:
    def test_p4_dos_tenants_mismo_stream_agregacion_sin_fuga_de_identidad(
        self,
        app_db: PsycopgDatabase,
        ingestion_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
        limpiar_market: None,
    ) -> None:
        # LA PRUEBA CLAVE (CA-P07-D): dos tenants DISTINTOS piden el MISMO flujo
        # publico. El worker ve que son DOS -- y por eso abre UN SOLO stream, que es
        # el proposito entero de ADR-014 -- pero NO PUEDE SABER QUIENES SON.
        stream_key = f"market:candles:binance:spot:BTC-USDT:1m:{uuid4().hex[:8]}"
        user_a, user_b = _dos_sujetos(app_db, crear_usuario)
        _crear_intent(app_db, user_a, stream_key)
        _crear_intent(app_db, user_b, stream_key)

        assert _demanda(ingestion_db, stream_key) == 2

        # La ventanilla devuelve CUANTOS, jamas QUIENES: sus unicas columnas son la
        # clave del stream y el contador.
        with ingestion_db.transaction() as session:
            row = session.fetchone(
                "SELECT * FROM market_public_demand() WHERE out_market_stream_key = %s",
                (stream_key,),
            )
        assert row is not None
        assert len(row) == 2  # ni tenant_id, ni user_id, ni intent_id: no caben.

    def test_p5_un_intent_privado_no_suma_en_la_agregacion_publica(
        self,
        app_db: PsycopgDatabase,
        ingestion_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
        limpiar_market: None,
    ) -> None:
        # El interes PRIVADO/BYOC no aparece ni suma: lo filtra la funcion Y, sobre
        # todo, lo filtra la POLICY DEL DUENO en el motor (CA-P07-G).
        stream_key = f"market:candles:binance:spot:BTC-USDT:1m:{uuid4().hex[:8]}"
        user_a, user_b = _dos_sujetos(app_db, crear_usuario)
        _crear_intent(app_db, user_a, stream_key)
        _crear_intent(app_db, user_b, stream_key, stream_scope="user")

        assert _demanda(ingestion_db, stream_key) == 1

    def test_p6_el_ingestor_no_puede_ni_mirar_la_tabla_de_intereses(
        self, ingestion_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        # P6: NO es que no vea filas. Es que el MOTOR le prohibe mirar. Su unico
        # acceso a la demanda es la ventanilla agregada.
        with pytest.raises(Exception) as excinfo:
            with ingestion_db.transaction() as session:
                session.fetchall("SELECT * FROM market_subscription_intent")
        assert "permission denied" in str(excinfo.value).lower()


class TestAislamientoDeLaDemanda:
    def test_p7_la_app_solo_opera_sus_propios_intents(
        self,
        app_db: PsycopgDatabase,
        crear_usuario: Callable[[], UUID],
        limpiar_market: None,
    ) -> None:
        # Patron exacto de test_tenancy_isolation: con el contexto de A no se ve, ni
        # se borra, ni se modifica lo de B. La RLS de la tabla base sigue INTACTA.
        stream_key = f"market:candles:binance:spot:BTC-USDT:1m:{uuid4().hex[:8]}"
        user_a, user_b = _dos_sujetos(app_db, crear_usuario)
        _crear_intent(app_db, user_a, stream_key)
        _crear_intent(app_db, user_b, stream_key)

        scoped_db = TenantScopedDatabase(app_db)
        with scoped_db.transaction(user_a) as scoped:
            rows = scoped.session.fetchall(
                "SELECT user_id FROM market_subscription_intent"
            )
            assert [UUID(str(row[0])) for row in rows] == [user_a]

            # Borrar lo de B: la RLS no le deja verlo, asi que no borra nada.
            scoped.session.execute(
                "DELETE FROM market_subscription_intent WHERE user_id = %s",
                (str(user_b),),
            )
            scoped.session.execute(
                "UPDATE market_subscription_intent SET priority = 999 "
                "WHERE user_id = %s",
                (str(user_b),),
            )

        # Lo de B sigue intacto, comprobado bajo el contexto de B.
        with scoped_db.transaction(user_b) as scoped:
            rows = scoped.session.fetchall(
                "SELECT user_id, priority FROM market_subscription_intent"
            )
            assert [(UUID(str(r[0])), _entero(r[1])) for r in rows] == [(user_b, 100)]


class TestReglaLaApiNoFabricaHechos:
    def test_5_20_a_la_api_no_puede_insertar_una_vela(
        self, app_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        # MITAD (a) de la prueba negativa bidireccional. La API esta expuesta a
        # internet: si pudiera insertar una vela, podria FABRICAR un hecho de mercado
        # que alimenta reglas -> senales -> en M5, ORDENES REALES. Lo impide el MOTOR.
        with pytest.raises(Exception) as excinfo:
            with app_db.transaction() as session:
                session.execute(
                    _INSERT_CANDLE,
                    (f"idem-{uuid4().hex}", "market:x", _OPEN_TIME, _CLOSE_TIME),
                )
        assert "permission denied" in str(excinfo.value).lower()

    def test_5_20_a_la_api_no_puede_escribir_el_catalogo(
        self, app_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        with pytest.raises(Exception) as excinfo:
            with app_db.transaction() as session:
                session.execute(
                    "INSERT INTO market_instrument "
                    "(exchange, market_type, symbol, native_symbol) "
                    "VALUES ('binance', 'spot', 'FAKE-USDT', 'FAKEUSDT')"
                )
        assert "permission denied" in str(excinfo.value).lower()

    def test_5_20_a_la_api_no_puede_insertar_un_trade(
        self, app_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        # P07b: un trade fabricado es peor que una vela fabricada, porque el footprint
        # se DERIVA de los trades: quien pueda inventar trades inventa el orderflow
        # entero. La API solo LEE (regla 5.20).
        with pytest.raises(Exception) as excinfo:
            with app_db.transaction() as session:
                session.execute(_INSERT_TRADE, (f"t-{uuid4().hex}", _OPEN_TIME))
        assert "permission denied" in str(excinfo.value).lower()

    def test_5_20_a_la_api_no_puede_insertar_un_footprint(
        self, app_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        with pytest.raises(Exception) as excinfo:
            with app_db.transaction() as session:
                session.execute(
                    _INSERT_FOOTPRINT,
                    (f"idem-{uuid4().hex}", "market:x", _OPEN_TIME, _CLOSE_TIME),
                )
        assert "permission denied" in str(excinfo.value).lower()


class TestReglaElIngestorNoTocaHechosAjenos:
    @pytest.mark.parametrize(
        "sentencia",
        [
            "SELECT * FROM app_user",
            "SELECT * FROM kill_switch",
            "SELECT * FROM sensitive_action_audit",
            "SELECT * FROM policy_rule",
        ],
    )
    def test_5_20_b_el_ingestor_no_toca_identidad_politica_ni_auditoria(
        self, ingestion_db: PsycopgDatabase, sentencia: str, limpiar_market: None
    ) -> None:
        # MITAD (b): el ingestor no porta un poder que su funcion no necesita. No es
        # una promesa del codigo: el motor le dice que no.
        with pytest.raises(Exception) as excinfo:
            with ingestion_db.transaction() as session:
                session.fetchall(sentencia)
        assert "permission denied" in str(excinfo.value).lower()


class TestHistoricoAppendOnly:
    def test_el_ingestor_si_puede_insertar_una_vela(
        self, ingestion_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        # El caso VERDE: el ingestor es el UNICO que escribe market data.
        clave = f"idem-{uuid4().hex}"
        with ingestion_db.transaction() as session:
            session.execute(
                _INSERT_CANDLE, (clave, "market:x", _OPEN_TIME, _CLOSE_TIME)
            )
            row = session.fetchone(
                "SELECT count(*) FROM market_candle WHERE idempotency_key = %s",
                (clave,),
            )
        assert row is not None and _entero(row[0]) == 1

    @pytest.mark.parametrize("operacion", ["UPDATE", "DELETE"])
    def test_nadie_reescribe_la_historia_del_mercado(
        self, ingestion_db: PsycopgDatabase, operacion: str, limpiar_market: None
    ) -> None:
        # APPEND-ONLY REAL, tambien para QUIEN LA ESCRIBE. El propio ingestor no puede
        # modificar ni borrar una vela que el mismo acaba de insertar.
        clave = f"idem-{uuid4().hex}"
        with ingestion_db.transaction() as session:
            session.execute(
                _INSERT_CANDLE, (clave, "market:x", _OPEN_TIME, _CLOSE_TIME)
            )

        sentencia = (
            "UPDATE market_candle SET close = 1 WHERE idempotency_key = %s"
            if operacion == "UPDATE"
            else "DELETE FROM market_candle WHERE idempotency_key = %s"
        )
        with pytest.raises(Exception) as excinfo:
            with ingestion_db.transaction() as session:
                session.execute(sentencia, (clave,))
        assert "permission denied" in str(excinfo.value).lower()

    @pytest.mark.parametrize("operacion", ["UPDATE", "DELETE"])
    def test_la_api_tampoco_reescribe_la_historia(
        self, app_db: PsycopgDatabase, operacion: str, limpiar_market: None
    ) -> None:
        sentencia = (
            "UPDATE market_candle SET close = 1 WHERE idempotency_key = 'x'"
            if operacion == "UPDATE"
            else "DELETE FROM market_candle WHERE idempotency_key = 'x'"
        )
        with pytest.raises(Exception) as excinfo:
            with app_db.transaction() as session:
                session.execute(sentencia)
        assert "permission denied" in str(excinfo.value).lower()

    def test_el_ingestor_si_puede_insertar_un_trade(
        self, ingestion_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        # El caso VERDE de P07b: el ingestor es el UNICO que escribe trades.
        trade_id = f"t-{uuid4().hex}"
        with ingestion_db.transaction() as session:
            session.execute(_INSERT_TRADE, (trade_id, _OPEN_TIME))
            row = session.fetchone(
                "SELECT count(*) FROM market_trade WHERE trade_id = %s", (trade_id,)
            )
        assert row is not None and _entero(row[0]) == 1

    def test_el_ingestor_si_puede_insertar_un_footprint(
        self, ingestion_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        clave = f"idem-{uuid4().hex}"
        with ingestion_db.transaction() as session:
            session.execute(
                _INSERT_FOOTPRINT, (clave, "market:x", _OPEN_TIME, _CLOSE_TIME)
            )
            row = session.fetchone(
                "SELECT count(*) FROM market_footprint WHERE idempotency_key = %s",
                (clave,),
            )
        assert row is not None and _entero(row[0]) == 1

    @pytest.mark.parametrize("tabla", _TABLAS_HISTORICO_P07B)
    @pytest.mark.parametrize("operacion", ["UPDATE", "DELETE"])
    def test_el_ingestor_no_reescribe_trades_ni_footprints(
        self,
        ingestion_db: PsycopgDatabase,
        tabla: str,
        operacion: str,
        limpiar_market: None,
    ) -> None:
        # APPEND-ONLY REAL sobre el historico nuevo, tambien para QUIEN LO ESCRIBE: el
        # trade crudo es la base de la reproducibilidad bit a bit; si el ingestor
        # pudiera retocarlo, el footprint dejaria de ser reproducible.
        with pytest.raises(Exception) as excinfo:
            with ingestion_db.transaction() as session:
                session.execute(_REESCRITURAS[(tabla, operacion)])
        assert "permission denied" in str(excinfo.value).lower()

    @pytest.mark.parametrize("tabla", _TABLAS_HISTORICO_P07B)
    @pytest.mark.parametrize("operacion", ["UPDATE", "DELETE"])
    def test_la_api_no_reescribe_trades_ni_footprints(
        self,
        app_db: PsycopgDatabase,
        tabla: str,
        operacion: str,
        limpiar_market: None,
    ) -> None:
        with pytest.raises(Exception) as excinfo:
            with app_db.transaction() as session:
                session.execute(_REESCRITURAS[(tabla, operacion)])
        assert "permission denied" in str(excinfo.value).lower()

    @pytest.mark.parametrize("tabla", _TABLAS_HISTORICO_P07B)
    @pytest.mark.parametrize("operacion", ["UPDATE", "DELETE"])
    def test_el_operador_no_reescribe_trades_ni_footprints(
        self,
        operator_db: PsycopgDatabase,
        tabla: str,
        operacion: str,
        limpiar_market: None,
    ) -> None:
        # El tercer rol de runtime: el operador opera kill switches y politica; sobre
        # market data no tiene NADA (REVOKE ALL), asi que ni siquiera llega a intentar
        # la reescritura.
        with pytest.raises(Exception) as excinfo:
            with operator_db.transaction() as session:
                session.execute(_REESCRITURAS[(tabla, operacion)])
        assert "permission denied" in str(excinfo.value).lower()


class TestOutboxDelIngestorAcotadaPorElMotor:
    def test_el_ingestor_puede_encolar_un_market_candle_closed(
        self, ingestion_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        # El caso VERDE: lo que SI es suyo, lo encola sin problema.
        with ingestion_db.transaction() as session:
            session.execute(
                _INSERT_OUTBOX,
                (
                    str(uuid4()),
                    f"idem-{uuid4().hex}",
                    "market:candles:binance:spot:BTC-USDT:1m",
                    "market.candle_closed",
                    "{}",
                ),
            )
            row = session.fetchone(
                "SELECT count(*) FROM outbox WHERE event_type = 'market.candle_closed'"
            )
        assert row is not None and _entero(row[0]) == 1

    @pytest.mark.parametrize(
        "event_type", ["market.footprint_closed", "market.footprint_corrected"]
    )
    def test_el_ingestor_puede_encolar_los_dos_market_footprint(
        self, ingestion_db: PsycopgDatabase, event_type: str, limpiar_market: None
    ) -> None:
        # P07b: la 0017 RECREA las policies de 0012 con los CINCO market.*. Sin esto,
        # el ingestor podria persistir el footprint y no poder publicarlo: el WITH
        # CHECK de la policy lo rechazaria y la pieza estaria muerta a medias.
        with ingestion_db.transaction() as session:
            session.execute(
                _INSERT_OUTBOX,
                (
                    str(uuid4()),
                    f"idem-{uuid4().hex}",
                    "market:footprint:binance:spot:BTC-USDT:1m",
                    event_type,
                    "{}",
                ),
            )
            row = session.fetchone(
                "SELECT count(*) FROM outbox WHERE event_type = %s", (event_type,)
            )
        assert row is not None and _entero(row[0]) == 1

    def test_el_ingestor_no_puede_fabricar_un_execution_falso(
        self, ingestion_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        # Mismo patron que la prueba de CA-04 con el operador: un ingestor comprometido
        # NO puede inventarse una orden. Se lo impide el WITH CHECK de la policy, es
        # decir, el MOTOR.
        with pytest.raises(Exception) as excinfo:
            with ingestion_db.transaction() as session:
                session.execute(
                    _INSERT_OUTBOX,
                    (
                        str(uuid4()),
                        f"idem-{uuid4().hex}",
                        "execution:stream",
                        "execution.order_placed",
                        "{}",
                    ),
                )
        assert "row-level security" in str(excinfo.value).lower()

    def test_el_ingestor_no_puede_fabricar_un_policy_falso(
        self, ingestion_db: PsycopgDatabase, limpiar_market: None
    ) -> None:
        with pytest.raises(Exception) as excinfo:
            with ingestion_db.transaction() as session:
                session.execute(
                    _INSERT_OUTBOX,
                    (
                        str(uuid4()),
                        f"idem-{uuid4().hex}",
                        "policy:stream",
                        "policy.kill_switch_activated",
                        "{}",
                    ),
                )
        assert "row-level security" in str(excinfo.value).lower()
