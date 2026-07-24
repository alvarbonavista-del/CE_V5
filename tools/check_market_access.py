"""Check "market" (P07, regla 5.20 + CA-P07-D/G): ingesta estrecha y ventanilla ciega.

Sin este check, la regla 5.20 seria una promesa escrita en un documento. Con el, es un
hecho verificado en cada build. FALLA si:

- la API (ce_v5_app) puede ESCRIBIR market data: una vela falsa es un HECHO FABRICADO
  que alimenta reglas -> senales -> en M5, ORDENES REALES;
- CUALQUIER rol de runtime (tambien el propio ingestor) puede UPDATE/DELETE/TRUNCATE el
  historico: nadie reescribe la historia del mercado;
- el ingestor tiene algun privilegio sobre la demanda (market_subscription_intent): su
  UNICO acceso a ella es la ventanilla agregada;
- el ingestor toca politica o auditoria, o el operador toca market data: es la prueba
  negativa BIDIRECCIONAL que exige la regla 5.20;
- la ventanilla market_public_demand deja de ser ciega: si acepta parametros, si cambia
  su firma o su retorno, o si sus columnas de salida dejan de ser EXACTAMENTE
  (out_market_stream_key, out_intent_count), podria revelar QUIEN pide un stream, y solo
  puede revelar CUANTOS;
- el ingestor puede encolar en la outbox algo que no sea uno de los siete market.*: un
  ingestor comprometido no puede fabricar un execution.* falso;
- market_subscription_intent pierde su RLS: la excepcion de CA-P07-G se apoya en que la
  RLS esta activa.

Lee el catalogo con pg_catalog, has_table_privilege y has_function_privilege (NUNCA
information_schema, que oculta objetos segun privilegios), con el DSN de migraciones
para visibilidad total. La logica pura (check_market) es testeable sin PostgreSQL; solo
load_market_facts toca el catalogo.
"""

import sys
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "backend" / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(REPO_ROOT / "contracts"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from ce_v5.infra.db.config import DbConfig  # noqa: E402
from ce_v5.infra.db.ports import Database  # noqa: E402
from ce_v5.infra.db.provision import (  # noqa: E402
    APP_ROLE_NAME,
    INGESTION_ROLE_NAME,
    OPERATOR_ROLE_NAME,
)
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase  # noqa: E402

# Se REUTILIZAN las dataclasses del check de identidad (misma forma de mirar una
# funcion del catalogo). La dependencia va en UN SOLO sentido: este modulo importa de
# check_identity_access, y check_identity_access importa MARKET_FUNCTIONS de aqui con un
# import DIFERIDO dentro de la funcion que lo necesita, precisamente para no cerrar un
# ciclo de imports.
from check_identity_access import (  # noqa: E402
    AllowedFunction,
    FunctionFacts,
    _normalize,
)

MARKET_TABLES: tuple[str, ...] = (
    "market_candle",
    "market_instrument",
    "market_subscription_intent",
    "market_trade",
    "market_footprint",
    "market_trade_gap",
    "market_orderbook_snapshot",
    "market_orderbook_discontinuity",
)

# El historico es APPEND-ONLY: nadie, ni siquiera quien lo escribe, lo reescribe.
_APPEND_ONLY_PRIVILEGES: tuple[str, ...] = ("UPDATE", "DELETE", "TRUNCATE")
_APPEND_ONLY_TABLES: tuple[str, ...] = (
    "market_candle",
    "market_footprint",
    "market_trade",
    # Un hueco no se "arregla" borrandolo: el dato perdido no vuelve y borrar la fila
    # solo borraria la prueba de que falta.
    "market_trade_gap",
    # El libro L2 (P07c): la foto top-K y el resync tampoco se reescriben. Un resync
    # borrado seria un hueco del que nadie se entera.
    "market_orderbook_snapshot",
    "market_orderbook_discontinuity",
)
_WRITE_PRIVILEGES: tuple[str, ...] = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")
_ALL_PRIVILEGES: tuple[str, ...] = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)

RUNTIME_ROLES: tuple[str, ...] = (
    APP_ROLE_NAME,
    INGESTION_ROLE_NAME,
    OPERATOR_ROLE_NAME,
)

# El ingestor NO toca politica ni auditoria. Direccion (b) de la prueba negativa
# bidireccional de la regla 5.20: no basta con que la API no escriba velas; el ingestor
# tampoco puede tocar lo que no es suyo.
POLICY_AND_AUDIT_TABLES: tuple[str, ...] = (
    "policy_version",
    "policy_rule",
    "kill_switch",
    "operator_audit",
    "policy_entitlement",
    "policy_override",
    "sensitive_action_audit",
)

# Los SIETE unicos event_type que el ingestor puede encolar (candle x3 + footprint x2 +
# orderbook x2). La variante 'sample' del snapshot NO esta: se persiste, no se publica.
MARKET_EVENT_TYPES: tuple[str, ...] = (
    "market.candle_updated",
    "market.candle_closed",
    "market.candle_corrected",
    "market.footprint_closed",
    "market.footprint_corrected",
    "market.orderbook_frontier",
    "market.orderbook_resynced",
)

# Familias que JAMAS deben aparecer en una policy de outbox del ingestor.
_FORBIDDEN_EVENT_PREFIXES: tuple[str, ...] = (
    "execution.",
    "policy.",
    "signal.",
    "alert.",
    "notification.",
    "billing.",
    "user.",
    "component.",
    "rule.",
    "datasource.",
)

_INGESTION_OUTBOX_POLICIES: tuple[str, ...] = (
    "outbox_ingestion_insert",
    "outbox_ingestion_read",
    "outbox_ingestion_update",
)

# ALLOWLIST de las columnas que la ventanilla PUEDE devolver. Exacta, no "al menos".
DEMAND_RESULT_COLUMNS: tuple[str, ...] = ("out_market_stream_key", "out_intent_count")

# Lista NEGRA explicita: fragmentos que, si aparecen en el retorno, delatan una fuga de
# identidad. Redundante con la allowlist a proposito: la allowlist dice que SI se puede
# devolver; esto grita QUE es exactamente lo que nunca debe salir.
_FORBIDDEN_RESULT_FRAGMENTS: tuple[str, ...] = (
    "tenant_id",
    "user_id",
    "intent_id",
    "source_ref",
    "rule_id",
    "widget_id",
)

# ALLOWLIST EXPLICITA de funciones SECURITY DEFINER de MARKET. La regla "ninguna
# SECURITY DEFINER sin justificacion escrita" es GLOBAL (CA-07 p.6): cada pieza declara
# SUS ventanillas en SU check, y check_identity_access verifica que no exista ninguna
# huerfana consultando la UNION de allowlists.
MARKET_FUNCTIONS: dict[str, AllowedFunction] = {
    "market_public_demand": AllowedFunction(
        arguments="",
        result="TABLE(out_market_stream_key text, out_intent_count bigint)",
        justification=(
            "Ventanilla agregada (CA-P07-D/G): el proposito de ADR-014 es que dos "
            "tenants interesados en el MISMO flujo compartan UN SOLO stream, y esa "
            "union es cross-tenant por naturaleza, pero la RLS de P05 impide (y hace "
            "bien) que el worker lea los intereses de todos los tenants. Esta funcion "
            "devuelve SOLO la demanda agregada: la clave del stream y CUANTOS la "
            "piden. NUNCA quienes. NO ACEPTA PARAMETROS a proposito: una ventanilla "
            "sin parametros no puede ser interrogada por tenant ni por usuario; no hay "
            "forma de pedirle 'dime los intereses del tenant X' porque no admite esa "
            "pregunta. Solo la ejecuta ce_v5_ingestion."
        ),
    ),
}


def _function_violations(functions: Mapping[str, FunctionFacts]) -> list[str]:
    out: list[str] = []
    for name, allowed in MARKET_FUNCTIONS.items():
        fn = functions.get(name)
        if fn is None:
            out.append(f"{name}: la ventanilla no existe (P07, CA-P07-D).")
            continue
        if not fn.is_security_definer:
            out.append(
                f"{name}: no es SECURITY DEFINER (CA-P07-D): sin eso no podria agregar "
                "a traves de tenants y la ventanilla no tendria sentido."
            )
        if not fn.has_fixed_search_path():
            out.append(
                f"{name}: sin search_path fijado (CA-07 p.4): el secuestro de "
                "search_path es EL exploit clasico de SECURITY DEFINER."
            )
        if fn.uses_dynamic_sql():
            out.append(
                f"{name}: usa SQL dinamico (CA-07 p.4): dentro de una SECURITY DEFINER "
                "es la via directa a la inyeccion con privilegios de dueno."
            )
        if fn.execute_for_public:
            out.append(
                f"{name}: tiene EXECUTE para PUBLIC (CA-P07-D): la ventanilla es solo "
                "para el worker de ingesta."
            )
        if not fn.execute_for_ingestion:
            out.append(
                f"{name}: el rol {INGESTION_ROLE_NAME} no tiene EXECUTE (CA-P07-D): el "
                "worker no podria saber que streams debe abrir."
            )
        if fn.execute_for_app:
            out.append(
                f"{name}: el rol {APP_ROLE_NAME} tiene EXECUTE (CA-P07-D): la API no "
                "agrega demanda cross-tenant; nadie mas necesita esta ventanilla."
            )
        if fn.execute_for_operator:
            out.append(
                f"{name}: el rol {OPERATOR_ROLE_NAME} tiene EXECUTE (CA-P07-D): el "
                "operador no ingiere market data; nadie mas necesita esta ventanilla."
            )

        # SIN PARAMETROS: no se le puede preguntar por un tenant concreto.
        if fn.argument_names():
            out.append(
                f"{name}: la ventanilla ACEPTA PARAMETROS ({fn.arguments}) y no debe "
                "(CA-P07-D): una ventanilla sin parametros no puede ser interrogada "
                "por tenant ni por usuario; con parametros, alguien podria pedirle "
                "'dime los intereses del tenant X'."
            )
        if _normalize(fn.arguments) != _normalize(allowed.arguments):
            out.append(
                f"{name}: la firma cambio (CA-07 p.7). Esperada: "
                f"'{allowed.arguments}'; encontrada: '{fn.arguments}'."
            )
        if _normalize(fn.result) != _normalize(allowed.result):
            out.append(
                f"{name}: el retorno cambio (CA-07 p.7): ensanchar la ventanilla es "
                f"tan grave como abrir la puerta. Esperado: '{allowed.result}'; "
                f"encontrado: '{fn.result}'."
            )

        # ALLOWLIST ESTRICTA de columnas de retorno: EXACTAMENTE estas dos.
        columns = fn.result_column_names()
        if columns != DEMAND_RESULT_COLUMNS:
            out.append(
                f"{name}: las columnas de salida son {columns or '()'} y deben ser "
                f"EXACTAMENTE {DEMAND_RESULT_COLUMNS} (CA-P07-D): cualquier columna de "
                "mas revelaria QUIEN pide un stream, y la ventanilla solo puede "
                "revelar CUANTOS."
            )
        result = fn.result.lower()
        for fragment in _FORBIDDEN_RESULT_FRAGMENTS:
            if fragment in result:
                out.append(
                    f"{name}: el retorno contiene '{fragment}' (CA-P07-D): la "
                    "ventanilla revelaria QUIEN pide un stream. Solo puede revelar "
                    "CUANTOS lo piden; la identidad del sujeto JAMAS sale de aqui."
                )
    return out


def _privilege_violations(privileges: Mapping[tuple[str, str, str], bool]) -> list[str]:
    out: list[str] = []

    # La API LEE market data; NO la escribe (regla 5.20).
    for table in (
        "market_candle",
        "market_instrument",
        "market_trade",
        "market_footprint",
        "market_trade_gap",
        "market_orderbook_snapshot",
        "market_orderbook_discontinuity",
    ):
        for privilege in _WRITE_PRIVILEGES:
            if privileges.get((APP_ROLE_NAME, table, privilege), False):
                out.append(
                    f"{table}: el rol {APP_ROLE_NAME} tiene {privilege} (regla 5.20): "
                    "la API esta expuesta a internet; si pudiera insertar una vela, "
                    "podria FABRICAR un hecho de mercado que alimenta reglas, senales "
                    "y, en M5, ordenes reales."
                )

    # El historico es APPEND-ONLY para TODOS, tambien para quien lo escribe.
    for role in RUNTIME_ROLES:
        for table in _APPEND_ONLY_TABLES:
            for privilege in _APPEND_ONLY_PRIVILEGES:
                if privileges.get((role, table, privilege), False):
                    out.append(
                        f"{table}: el rol {role} tiene {privilege} (regla 5.20): el "
                        "historico de mercado es APPEND-ONLY; nadie reescribe la "
                        "historia, ni siquiera el ingestor que la escribe."
                    )

    # El ingestor NO ve la demanda: su unico acceso es la ventanilla agregada.
    for privilege in _ALL_PRIVILEGES:
        if privileges.get(
            (INGESTION_ROLE_NAME, "market_subscription_intent", privilege), False
        ):
            out.append(
                f"market_subscription_intent: el rol {INGESTION_ROLE_NAME} tiene "
                f"{privilege} directo (CA-P07-D): el ingestor no lee la demanda fila a "
                "fila (sabria QUIEN pide que); su UNICO acceso es la ventanilla "
                "agregada market_public_demand."
            )

    # El operador no toca market data.
    for table in MARKET_TABLES:
        for privilege in _ALL_PRIVILEGES:
            if privileges.get((OPERATOR_ROLE_NAME, table, privilege), False):
                out.append(
                    f"{table}: el rol {OPERATOR_ROLE_NAME} tiene {privilege} (regla "
                    "5.20): el operador opera kill switches y politica; no ingiere ni "
                    "lee market data."
                )

    # Y AL REVES: el ingestor no toca politica ni auditoria (prueba bidireccional).
    for table in POLICY_AND_AUDIT_TABLES:
        for privilege in _ALL_PRIVILEGES:
            if privileges.get((INGESTION_ROLE_NAME, table, privilege), False):
                out.append(
                    f"{table}: el rol {INGESTION_ROLE_NAME} tiene {privilege} (regla "
                    "5.20): el ingestor no toca politica ni auditoria. Un proceso no "
                    "porta un poder que su funcion no necesita."
                )
    return out


def _outbox_violations(
    outbox_policies: Mapping[str, str],
    privileges: Mapping[tuple[str, str, str], bool],
) -> list[str]:
    """La outbox del ingestor la acota el MOTOR, no la buena conducta del codigo."""
    out: list[str] = []
    for name in _INGESTION_OUTBOX_POLICIES:
        expression = outbox_policies.get(name)
        if expression is None:
            out.append(
                f"outbox: falta la policy '{name}' del rol {INGESTION_ROLE_NAME} "
                "(regla 5.20, patron CA-04): sin ella el motor no acota lo que el "
                "ingestor puede encolar."
            )
            continue
        lowered = expression.lower()
        for event_type in MARKET_EVENT_TYPES:
            if event_type not in lowered:
                out.append(
                    f"outbox: la policy '{name}' no menciona '{event_type}' (regla "
                    "5.20): debe acotar EXACTAMENTE a los siete market.*."
                )
        for prefix in _FORBIDDEN_EVENT_PREFIXES:
            if prefix in lowered:
                out.append(
                    f"outbox: la policy '{name}' menciona '{prefix}' (regla 5.20): un "
                    "ingestor comprometido no puede fabricar un execution.* falso; se "
                    "lo impide el MOTOR. Su outbox se acota a los siete market.* y a "
                    "nada mas."
                )

    for privilege in ("DELETE", "TRUNCATE"):
        if privileges.get((INGESTION_ROLE_NAME, "outbox", privilege), False):
            out.append(
                f"outbox: el rol {INGESTION_ROLE_NAME} tiene {privilege} (regla 5.20): "
                "el ingestor encola y marca lo suyo; no borra la outbox de nadie."
            )
    return out


def _rls_violations(rls: Mapping[str, tuple[bool, bool]]) -> list[str]:
    """La tabla base conserva su RLS: la excepcion de CA-P07-G se apoya en ella."""
    out: list[str] = []
    estado = rls.get("market_subscription_intent")
    if estado is None:
        out.append(
            "market_subscription_intent: la tabla de demanda no existe (P07, ADR-014)."
        )
        return out
    has_rls, has_force = estado
    if not (has_rls and has_force):
        out.append(
            "market_subscription_intent: sin RLS ENABLE + FORCE (CA-P07-G): la "
            "ventanilla agregada solo es admisible porque la tabla base conserva su "
            "RLS INTACTA. Si la RLS cae, la excepcion no vale nada."
        )
    return out


def check_market(
    functions: Mapping[str, FunctionFacts],
    privileges: Mapping[tuple[str, str, str], bool],
    outbox_policies: Mapping[str, str],
    rls: Mapping[str, tuple[bool, bool]],
) -> list[str]:
    """Logica pura del check market: devuelve las violaciones (vacia = verde)."""
    problems: list[str] = []
    problems.extend(_function_violations(functions))
    problems.extend(_privilege_violations(privileges))
    problems.extend(_outbox_violations(outbox_policies, privileges))
    problems.extend(_rls_violations(rls))
    return problems


_FUNCTIONS_SQL = """
SELECT p.proname,
       p.prosecdef,
       coalesce(p.proconfig, ARRAY[]::text[]),
       pg_get_function_arguments(p.oid),
       pg_get_function_result(p.oid),
       p.prosrc,
       has_function_privilege('public', p.oid, 'EXECUTE'),
       has_function_privilege(%s, p.oid, 'EXECUTE'),
       has_function_privilege(%s, p.oid, 'EXECUTE'),
       has_function_privilege(%s, p.oid, 'EXECUTE')
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public' AND p.proname = ANY(%s)
"""

_RLS_SQL = """
SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname = 'public' AND c.relname = ANY(%s)
"""

# La expresion que se valida es la RECONSTRUIDA POR EL MOTOR desde el catalogo, no un
# regex sobre el .sql: un regex comprueba lo que alguien escribio; el catalogo comprueba
# lo que la base REALMENTE TIENE.
_OUTBOX_POLICIES_SQL = """
SELECT policyname, coalesce(qual, '') || ' ' || coalesce(with_check, '')
FROM pg_policies
WHERE schemaname = 'public' AND tablename = 'outbox'
"""


def load_market_facts(
    database: Database,
) -> tuple[
    dict[str, FunctionFacts],
    dict[tuple[str, str, str], bool],
    dict[str, str],
    dict[str, tuple[bool, bool]],
]:
    """Lee del catalogo la ventanilla, los privilegios, la outbox y la RLS."""
    tablas_a_probar = list(MARKET_TABLES) + list(POLICY_AND_AUDIT_TABLES) + ["outbox"]
    with database.transaction() as session:
        function_rows = session.fetchall(
            _FUNCTIONS_SQL,
            (
                APP_ROLE_NAME,
                INGESTION_ROLE_NAME,
                OPERATOR_ROLE_NAME,
                list(MARKET_FUNCTIONS),
            ),
        )
        rls_rows = session.fetchall(_RLS_SQL, (list(MARKET_TABLES),))
        outbox_rows = session.fetchall(_OUTBOX_POLICIES_SQL)
        existing_rows = session.fetchall(_RLS_SQL, (tablas_a_probar,))

        functions: dict[str, FunctionFacts] = {}
        for row in function_rows:
            raw_config = row[2]
            config: tuple[str, ...] = ()
            if isinstance(raw_config, list | tuple):
                config = tuple(str(item) for item in raw_config)
            functions[str(row[0])] = FunctionFacts(
                name=str(row[0]),
                is_security_definer=bool(row[1]),
                config=config,
                arguments=str(row[3]),
                result=str(row[4]),
                body=str(row[5]),
                execute_for_public=bool(row[6]),
                execute_for_app=bool(row[7]),
                execute_for_ingestion=bool(row[8]),
                execute_for_operator=bool(row[9]),
            )

        # has_table_privilege ABORTA si la tabla no existe: solo se preguntan las que
        # el catalogo confirma que estan.
        existentes = {str(row[0]) for row in existing_rows}
        combos = [
            (role, table, privilege)
            for role in RUNTIME_ROLES
            for table in tablas_a_probar
            for privilege in _ALL_PRIVILEGES
            if table in existentes
        ]
        privileges: dict[tuple[str, str, str], bool] = {}
        if combos:
            placeholders = ", ".join(["(%s, %s, %s)"] * len(combos))
            params: list[str] = [value for combo in combos for value in combo]
            priv_sql = (
                "SELECT v.role, v.tbl, v.priv, "
                "has_table_privilege(v.role, v.tbl, v.priv) "
                f"FROM (VALUES {placeholders}) AS v(role, tbl, priv)"
            )
            privileges = {
                (str(row[0]), str(row[1]), str(row[2])): bool(row[3])
                for row in session.fetchall(priv_sql, params)
            }

    outbox_policies = {str(row[0]): str(row[1]) for row in outbox_rows}
    rls = {str(row[0]): (bool(row[1]), bool(row[2])) for row in rls_rows}
    return functions, privileges, outbox_policies, rls


def main() -> int:
    database = PsycopgDatabase(DbConfig.migrations_from_env())
    try:
        functions, privileges, outbox_policies, rls = load_market_facts(database)
    finally:
        database.close()
    problems = check_market(functions, privileges, outbox_policies, rls)
    if problems:
        print("FAIL check market (rol de ingesta y ventanilla, 5.20 / CA-P07-D/G):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        f"OK check market (regla 5.20, CA-P07-D/G): {APP_ROLE_NAME} no puede escribir "
        f"market data; el historico es append-only para TODOS; {INGESTION_ROLE_NAME} "
        "no ve la demanda fila a fila (solo la ventanilla agregada), no toca politica "
        "ni auditoria, y su outbox esta acotada por el motor a los siete market.*."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
