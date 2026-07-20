"""Check "rules" (P08, CA-P08-02/03): el motor de reglas lee por ventanilla y nada mas.

Sin este check, la separacion de poder de P08 seria una promesa escrita en un
documento. Con el, es un hecho verificado en cada build. FALLA si:

- ce_v5_rules tiene CUALQUIER privilegio directo de tabla sobre rule_definition: su
  UNICO acceso a la autoria es la ventanilla cross-tenant rules_for_market;
- la ventanilla rules_for_market deja de ser SECURITY DEFINER con search_path FIJO y
  sin SQL dinamico (el secuestro de search_path y el SQL dinamico son EL exploit
  clasico de una SECURITY DEFINER);
- PUBLIC o cualquier otro rol de runtime (ce_v5_app, ce_v5_ingestion, ce_v5_operator)
  tiene EXECUTE sobre la ventanilla: solo la ejecuta ce_v5_rules;
- la ventanilla expone dato de sujeto: sus columnas de retorno dejan de ser
  EXACTAMENTE {rule_id, tenant_id, product, canonical_rule_hash, schema_version,
  definition};
- ce_v5_rules tiene privilegio de escritura sobre market, identidad o policy: el motor
  no ingiere velas, no toca credenciales y no publica politica;
- la outbox de ce_v5_rules deja de estar acotada a las familias rule./signal./alert.,
  o el motor gana DELETE/TRUNCATE sobre la outbox;
- (D1, 0016) ce_v5_rules PIERDE el SELECT sobre market_candle -- sin el no puede leer la
  ventana de cierres y NO PUEDE EVALUAR --, o GANA cualquier otro acceso a mercado:
  escritura del historico, market_instrument (no imprescindible en v5.0: el motor no
  traduce simbolos nativos), market_subscription_intent (el intent de una regla lo
  escribe la AUTORIA, no el motor) o la ventanilla market_public_demand (es del
  ingestor). Es el unico check que muerde en los DOS sentidos: el positivo protege de
  un grant que desaparece, los negativos de un permiso ancho de mas.

ALCANCE (CA-P08-03). Este check cubre las pruebas ESTATICAS (verificables contra el
catalogo): 1, 2, 3, 4, 5, 6, 8, 13, 14 y 15. Las pruebas 7, 9, 10, 11, 12 y 16 son de
COMPORTAMIENTO (exigen ejecutar el motor contra datos reales: que la ventanilla
devuelva de verdad las reglas de varios tenants, que el estado se escriba, que un
evento acotado se encole y uno prohibido se rechace en caliente) y se cierran en la
VALIDACION EN CALIENTE de la tanda 4.3. Son BLOQUEANTES, no omitidas: sin ellas P08 no
cierra.

Lee el catalogo con pg_catalog, has_table_privilege y has_function_privilege (NUNCA
information_schema, que oculta objetos segun privilegios), con el DSN de migraciones
para visibilidad total. La logica pura (check_rules) es testeable sin PostgreSQL; solo
load_rules_facts toca el catalogo.
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
    RULES_ROLE_NAME,
)
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase  # noqa: E402

# Se REUTILIZAN las dataclasses del check de identidad (misma forma de mirar una
# funcion del catalogo), igual que hace check_market_access. La dependencia va en UN
# SOLO sentido: este modulo importa de check_identity_access, y check_identity_access
# importa RULES_FUNCTIONS de aqui con un import DIFERIDO dentro de la funcion que lo
# necesita, precisamente para no cerrar un ciclo de imports.
from check_identity_access import FunctionFacts  # noqa: E402

RULE_FUNCTION_NAME = "rules_for_market"

# La ventanilla la lee SOLO el motor.
RULE_DEFINITION_TABLE = "rule_definition"

# ALLOWLIST EXACTA de las columnas que la ventanilla PUEDE devolver. Exacta, no "al
# menos": ni una columna de mas, que revelaria dato de sujeto.
EXPECTED_RESULT_COLUMNS: tuple[str, ...] = (
    "rule_id",
    "tenant_id",
    "product",
    "canonical_rule_hash",
    "schema_version",
    "definition",
)

# Lista NEGRA explicita: fragmentos que, si aparecen en el retorno, delatan una fuga de
# identidad de sujeto. Redundante con la allowlist a proposito.
_FORBIDDEN_RESULT_FRAGMENTS: tuple[str, ...] = (
    "user_id",
    "owner",
    "email",
    "session",
    "plan",
)

_TABLE_PRIVILEGES: tuple[str, ...] = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
_WRITE_PRIVILEGES: tuple[str, ...] = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")

# --- D1: la rendija de LECTURA de mercado (0016, CA-P08-04) ------------------
# El motor evalua sobre la ventana de cierres del historico, asi que NECESITA SELECT
# sobre market_candle -- y NADA MAS de la superficie de mercado. Las tres constantes
# siguientes hacen que eso sea verificable en los dos sentidos: si falta el SELECT el
# motor no puede evaluar (y el check lo dice), y si aparece cualquier otro privilegio o
# cualquier otra tabla, el check ROMPE EL BUILD.
MARKET_CANDLE_TABLE = "market_candle"

# La ventanilla de DEMANDA de suscripcion: es del INGESTOR (0012). El motor no decide
# que flujos se suscriben, asi que no la ejecuta.
MARKET_DEMAND_FUNCTION = "market_public_demand"

# Tablas de mercado sobre las que el motor NO puede tener NINGUN privilegio, ni
# siquiera lectura. market_instrument esta AQUI a proposito (prueba 2): el motor recibe
# exchange/symbol ya CANONICOS por el evento y por market_scope, y nunca resuelve un
# simbolo nativo, asi que el catalogo NO es imprescindible en v5.0. Un grant preventivo
# "por si acaso" es exactamente lo que la regla 5.20 prohibe.
MARKET_TABLES_FORBIDDEN: tuple[str, ...] = (
    "market_instrument",
    "market_subscription_intent",
)

# Tablas de mercado sobre las que el motor NO puede ESCRIBIR (market_candle incluida:
# el historico es append-only tambien para el motor).
MARKET_TABLES: tuple[str, ...] = (MARKET_CANDLE_TABLE,) + MARKET_TABLES_FORBIDDEN
IDENTITY_TABLES: tuple[str, ...] = ("app_user", "user_credential", "user_session")
POLICY_TABLES: tuple[str, ...] = (
    "policy_version",
    "policy_rule",
    "kill_switch",
    "operator_audit",
    "policy_entitlement",
    "policy_override",
    "sensitive_action_audit",
)
# No hay tablas de billing ni de execution todavia (M5+): el rol nace sin privilegio
# sobre ellas por defecto. Cuando existan, se anaden aqui; no se inventan nombres.
FORBIDDEN_TABLES: tuple[str, ...] = (
    (RULE_DEFINITION_TABLE,) + MARKET_TABLES + IDENTITY_TABLES + POLICY_TABLES
)

OUTBOX_TABLE = "outbox"

# Las TRES familias que el motor puede encolar. Familia = prefijo con el punto.
RULES_EVENT_FAMILIES: tuple[str, ...] = ("rule.", "signal.", "alert.")

# Familias que JAMAS deben aparecer en una policy de outbox del motor. execution.*,
# market.* y policy.* son los negativos conceptuales explicitos de CA-P08-03; el resto
# completa la lista con el mismo criterio que check_market_access.
_FORBIDDEN_EVENT_PREFIXES: tuple[str, ...] = (
    "execution.",
    "market.",
    "policy.",
    "notification.",
    "billing.",
    "user.",
    "component.",
    "datasource.",
)

_RULES_OUTBOX_POLICIES: tuple[str, ...] = (
    "outbox_rules_insert",
    "outbox_rules_read",
    "outbox_rules_update",
)


def _function_violations(
    function: FunctionFacts | None, execute_for_rules: bool
) -> list[str]:
    """Pruebas 2, 3, 4, 5, 6 y 8: la ventanilla es estrecha y ciega a la identidad."""
    out: list[str] = []
    if function is None:
        out.append(
            f"{RULE_FUNCTION_NAME}: la ventanilla no existe (P08, CA-P08-03): sin ella "
            "el motor no tiene forma legitima de descubrir reglas cross-tenant."
        )
        return out

    # Prueba 2: el motor accede a rule_definition SOLO via la ventanilla (EXECUTE).
    if not execute_for_rules:
        out.append(
            f"{RULE_FUNCTION_NAME}: el rol {RULES_ROLE_NAME} sin EXECUTE (CA-P08-03 "
            "p.2): sin la ventanilla el motor no puede descubrir que reglas evaluar, y "
            "su unico acceso a rule_definition es esta."
        )

    # Prueba 3: search_path FIJO. El secuestro de search_path es el exploit clasico.
    if not function.has_fixed_search_path():
        out.append(
            f"{RULE_FUNCTION_NAME}: sin search_path fijado (CA-P08-03 p.3): "
            "el secuestro de search_path es EL exploit clasico de una SECURITY DEFINER."
        )

    # Prueba 4: sin SQL dinamico (misma comprobacion que check_market sobre su ventana).
    if function.uses_dynamic_sql():
        out.append(
            f"{RULE_FUNCTION_NAME}: usa SQL dinamico (CA-P08-03 p.4): dentro de una "
            "SECURITY DEFINER es la via a la inyeccion con privilegios de dueno."
        )

    # Prueba 5: PUBLIC no tiene EXECUTE.
    if function.execute_for_public:
        out.append(
            f"{RULE_FUNCTION_NAME}: tiene EXECUTE para PUBLIC (CA-P08-03 p.5): la "
            "ventanilla es solo para el motor de reglas."
        )

    # Prueba 6: ningun otro rol de runtime ejecuta la ventanilla.
    if function.execute_for_app:
        out.append(
            f"{RULE_FUNCTION_NAME}: el rol {APP_ROLE_NAME} tiene EXECUTE (CA-P08-03 "
            "p.6): la API no evalua reglas; nadie mas necesita esta ventanilla."
        )
    if function.execute_for_ingestion:
        out.append(
            f"{RULE_FUNCTION_NAME}: el rol {INGESTION_ROLE_NAME} tiene EXECUTE "
            "(CA-P08-03 p.6): el ingestor no evalua reglas; nadie mas la necesita."
        )
    if function.execute_for_operator:
        out.append(
            f"{RULE_FUNCTION_NAME}: el rol {OPERATOR_ROLE_NAME} tiene EXECUTE "
            "(CA-P08-03 p.6): el operador no evalua reglas; nadie mas la necesita."
        )

    # Prueba 8: retorno minimo, EXACTAMENTE las seis columnas, sin dato de sujeto.
    columns = function.result_column_names()
    if set(columns) != set(EXPECTED_RESULT_COLUMNS):
        out.append(
            f"{RULE_FUNCTION_NAME}: las columnas de salida son {columns or '()'} y "
            f"deben ser EXACTAMENTE {EXPECTED_RESULT_COLUMNS} (CA-P08-03 p.8): "
            "cualquier columna de mas podria revelar dato de sujeto; el retorno es "
            "minimo."
        )
    result = function.result.lower()
    for fragment in _FORBIDDEN_RESULT_FRAGMENTS:
        if fragment in result:
            out.append(
                f"{RULE_FUNCTION_NAME}: el retorno contiene '{fragment}' (CA-P08-03 "
                "p.8): la ventanilla no expone datos de sujeto "
                "(usuario/owner/email/sesion/plan)."
            )
    return out


def _privilege_violations(privileges: Mapping[tuple[str, str, str], bool]) -> list[str]:
    """Pruebas 1, 13 y 14: el motor no tiene privilegio directo donde no debe."""
    out: list[str] = []

    # Pruebas 1 y 13: NINGUN privilegio directo sobre rule_definition (ni lectura ni
    # escritura): su unico acceso es la ventanilla.
    for privilege in _TABLE_PRIVILEGES:
        if privileges.get((RULES_ROLE_NAME, RULE_DEFINITION_TABLE, privilege), False):
            out.append(
                f"{RULE_DEFINITION_TABLE}: el rol {RULES_ROLE_NAME} tiene {privilege} "
                "directo (CA-P08-03 p.1/p.13): el motor no toca la autoria fila a "
                "fila; su UNICO acceso a rule_definition es rules_for_market."
            )

    # Prueba 14: nada de escritura sobre market, identidad ni policy.
    for table in MARKET_TABLES + IDENTITY_TABLES + POLICY_TABLES:
        for privilege in _WRITE_PRIVILEGES:
            if privileges.get((RULES_ROLE_NAME, table, privilege), False):
                out.append(
                    f"{table}: el rol {RULES_ROLE_NAME} tiene {privilege} (CA-P08-03 "
                    "p.14): el motor no ingiere market data, no toca identidad y no "
                    "publica politica. Un proceso no porta un poder que no necesita."
                )
    return out


def _market_read_violations(
    privileges: Mapping[tuple[str, str, str], bool], demand_execute_for_rules: bool
) -> list[str]:
    """Pruebas D1 (1-7, 10, 26-28): la rendija de mercado es de SOLO LECTURA y minima.

    Muerde en los DOS sentidos, que es lo que la hace util:
    - POSITIVO (1): sin SELECT sobre market_candle el motor no puede leer la ventana de
      cierres y NO PUEDE EVALUAR. Un grant que desaparece en un refactor dejaria el
      motor mudo en produccion; aqui rompe el build.
    - NEGATIVOS (2-7, 10, 26-28): cualquier privilegio de mas -- escritura del
      historico, el catalogo de instrumentos, la ventanilla de demanda, la escritura de
      intents -- rompe el build. Un permiso ancho de mas es exactamente el fallo que
      este check existe para impedir.
    """
    out: list[str] = []

    # Prueba 1 (POSITIVO): el motor PUEDE leer el historico de velas.
    if not privileges.get((RULES_ROLE_NAME, MARKET_CANDLE_TABLE, "SELECT"), False):
        out.append(
            f"{MARKET_CANDLE_TABLE}: el rol {RULES_ROLE_NAME} NO tiene SELECT (P08 "
            "D1, 0016): el motor evalua sobre la ventana de cierres del historico; "
            "sin esa lectura no puede evaluar nada. Es la UNICA rendija que necesita."
        )

    # Pruebas 3, 4, 5 y 27 (NEGATIVOS): el historico es APPEND-ONLY tambien para el
    # motor. Las cubre _privilege_violations (p.14) sobre MARKET_TABLES, pero se
    # reafirman aqui con el mensaje de D1 para que el fallo se lea en su contexto.
    for privilege in _WRITE_PRIVILEGES:
        if privileges.get((RULES_ROLE_NAME, MARKET_CANDLE_TABLE, privilege), False):
            out.append(
                f"{MARKET_CANDLE_TABLE}: el rol {RULES_ROLE_NAME} tiene {privilege} "
                "(P08 D1, 0016): el motor LEE el mercado, no lo escribe. Nadie "
                "reescribe la historia del mercado, tampoco el motor."
            )

    # Pruebas 2 y 7 (NEGATIVOS): ninguna otra tabla de mercado, ni para leer.
    # market_instrument: NO IMPRESCINDIBLE en v5.0 (el motor no traduce simbolos
    # nativos). market_subscription_intent: la escritura del intent de una regla es de
    # la AUTORIA (ce_v5_app), no del motor.
    for table in MARKET_TABLES_FORBIDDEN:
        for privilege in _TABLE_PRIVILEGES:
            if privileges.get((RULES_ROLE_NAME, table, privilege), False):
                out.append(
                    f"{table}: el rol {RULES_ROLE_NAME} tiene {privilege} (P08 D1): la "
                    "0016 abre SOLO SELECT sobre market_candle. El motor no traduce "
                    "simbolos nativos ni declara demanda de suscripcion, asi que no "
                    "necesita esta tabla: un grant 'por si acaso' viola la regla 5.20."
                )

    # Prueba 6 (NEGATIVO): la ventanilla de DEMANDA es del ingestor, no del motor.
    if demand_execute_for_rules:
        out.append(
            f"{MARKET_DEMAND_FUNCTION}: el rol {RULES_ROLE_NAME} tiene EXECUTE (P08 "
            "D1): esa ventanilla agrega la DEMANDA de suscripcion y es del INGESTOR "
            "(0012). El motor no decide que flujos se suscriben."
        )
    return out


def _outbox_violations(
    outbox_policies: Mapping[str, str],
    privileges: Mapping[tuple[str, str, str], bool],
) -> list[str]:
    """Prueba 15: la outbox del motor la acota el MOTOR, no el codigo."""
    out: list[str] = []
    for name in _RULES_OUTBOX_POLICIES:
        expression = outbox_policies.get(name)
        if expression is None:
            out.append(
                f"outbox: falta la policy '{name}' del rol {RULES_ROLE_NAME} "
                "(CA-P08-03 p.15, patron CA-04): sin ella el motor no acota lo que "
                "puede encolar."
            )
            continue
        lowered = expression.lower()
        for family in RULES_EVENT_FAMILIES:
            if family not in lowered:
                out.append(
                    f"outbox: la policy '{name}' no acota a la familia '{family}' "
                    "(CA-P08-03 p.15): debe admitir EXACTAMENTE rule./signal./alert."
                )
        # NEGATIVOS conceptuales: execution.*, market.*, policy.* (y familias ajenas)
        # quedan FUERA. Un motor comprometido no puede fabricar un execution.* falso.
        for prefix in _FORBIDDEN_EVENT_PREFIXES:
            if prefix in lowered:
                out.append(
                    f"outbox: la policy '{name}' menciona '{prefix}' (CA-P08-03 "
                    "p.15): un motor comprometido no puede fabricar un "
                    "execution.*/market.*/policy.* falso; su outbox se acota a "
                    "rule./signal./alert. y a nada mas."
                )

    # El motor encola y marca lo suyo; no borra la outbox de nadie.
    for privilege in ("DELETE", "TRUNCATE"):
        if privileges.get((RULES_ROLE_NAME, OUTBOX_TABLE, privilege), False):
            out.append(
                f"outbox: el rol {RULES_ROLE_NAME} tiene {privilege} (CA-P08-03 "
                "p.15): el motor encola y marca published_at; no borra la outbox."
            )
    return out


def check_rules(
    function: FunctionFacts | None,
    execute_for_rules: bool,
    privileges: Mapping[tuple[str, str, str], bool],
    outbox_policies: Mapping[str, str],
    demand_execute_for_rules: bool = False,
) -> list[str]:
    """Logica pura del check rules: devuelve las violaciones (vacia = verde)."""
    problems: list[str] = []
    problems.extend(_function_violations(function, execute_for_rules))
    problems.extend(_privilege_violations(privileges))
    problems.extend(_market_read_violations(privileges, demand_execute_for_rules))
    problems.extend(_outbox_violations(outbox_policies, privileges))
    return problems


_FUNCTION_SQL = """
SELECT p.proname,
       p.prosecdef,
       coalesce(p.proconfig, ARRAY[]::text[]),
       pg_get_function_arguments(p.oid),
       pg_get_function_result(p.oid),
       p.prosrc,
       has_function_privilege('public', p.oid, 'EXECUTE'),
       has_function_privilege(%s, p.oid, 'EXECUTE'),
       has_function_privilege(%s, p.oid, 'EXECUTE'),
       has_function_privilege(%s, p.oid, 'EXECUTE'),
       has_function_privilege(%s, p.oid, 'EXECUTE')
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public' AND p.proname = %s
"""

_EXISTING_TABLES_SQL = """
SELECT c.relname
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


# EXECUTE del motor sobre la ventanilla de DEMANDA (del ingestor). Se pregunta por
# nombre de funcion, no por oid fijo: si un dia se le anadieran sobrecargas, todas
# quedan cubiertas. coalesce(bool_or(...), false) da FALSE cuando la funcion no existe.
_DEMAND_EXECUTE_SQL = """
SELECT coalesce(bool_or(has_function_privilege(%s, p.oid, 'EXECUTE')), false)
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public' AND p.proname = %s
"""


def load_rules_facts(
    database: Database,
) -> tuple[
    FunctionFacts | None, bool, dict[tuple[str, str, str], bool], dict[str, str], bool
]:
    """Lee del catalogo la ventanilla, su EXECUTE por rol, privilegios y outbox."""
    tables_to_probe = list(FORBIDDEN_TABLES) + [OUTBOX_TABLE]
    with database.transaction() as session:
        function_row = session.fetchone(
            _FUNCTION_SQL,
            (
                APP_ROLE_NAME,
                INGESTION_ROLE_NAME,
                OPERATOR_ROLE_NAME,
                RULES_ROLE_NAME,
                RULE_FUNCTION_NAME,
            ),
        )
        existing_rows = session.fetchall(_EXISTING_TABLES_SQL, (tables_to_probe,))
        outbox_rows = session.fetchall(_OUTBOX_POLICIES_SQL)
        demand_row = session.fetchone(
            _DEMAND_EXECUTE_SQL, (RULES_ROLE_NAME, MARKET_DEMAND_FUNCTION)
        )
        demand_execute_for_rules = bool(demand_row[0]) if demand_row else False

        function: FunctionFacts | None = None
        execute_for_rules = False
        if function_row is not None:
            raw_config = function_row[2]
            config: tuple[str, ...] = ()
            if isinstance(raw_config, list | tuple):
                config = tuple(str(item) for item in raw_config)
            function = FunctionFacts(
                name=str(function_row[0]),
                is_security_definer=bool(function_row[1]),
                config=config,
                arguments=str(function_row[3]),
                result=str(function_row[4]),
                body=str(function_row[5]),
                execute_for_public=bool(function_row[6]),
                execute_for_app=bool(function_row[7]),
                execute_for_ingestion=bool(function_row[8]),
                execute_for_operator=bool(function_row[9]),
            )
            execute_for_rules = bool(function_row[10])

        # has_table_privilege ABORTA si la tabla no existe: solo se preguntan las que el
        # catalogo confirma que estan.
        existentes = {str(row[0]) for row in existing_rows}
        combos = [
            (RULES_ROLE_NAME, table, privilege)
            for table in tables_to_probe
            for privilege in _TABLE_PRIVILEGES
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
    return (
        function,
        execute_for_rules,
        privileges,
        outbox_policies,
        demand_execute_for_rules,
    )


def main() -> int:
    database = PsycopgDatabase(DbConfig.migrations_from_env())
    try:
        (
            function,
            execute_for_rules,
            privileges,
            outbox_policies,
            demand_execute,
        ) = load_rules_facts(database)
    finally:
        database.close()
    problems = check_rules(
        function, execute_for_rules, privileges, outbox_policies, demand_execute
    )
    if problems:
        print("FAIL check rules (motor de reglas, CA-P08-02/03):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        f"OK check rules (CA-P08-03, estaticas 1/2/3/4/5/6/8/13/14/15 + D1): "
        f"{RULES_ROLE_NAME} no tiene privilegio directo sobre rule_definition (lee "
        "solo por la ventanilla), rules_for_market es SECURITY DEFINER con "
        "search_path fijo, sin SQL dinamico, sin EXECUTE para PUBLIC ni otros roles "
        "de runtime, con retorno minimo sin dato de sujeto; el motor no escribe "
        "market/identidad/policy y su outbox esta acotada a rule./signal./alert. D1 "
        f"(0016): {RULES_ROLE_NAME} SI lee {MARKET_CANDLE_TABLE} y NADA MAS de "
        "mercado -- sin escritura del historico, sin market_instrument (no "
        "imprescindible en v5.0), sin market_subscription_intent y sin la ventanilla "
        f"{MARKET_DEMAND_FUNCTION} (esa es del ingestor). Las de comportamiento "
        "(7/9/10/11/12/16) cierran en la 4.3 y el test 22 en validate_rules_worker."
    )
    return 0


# ALLOWLIST EXPLICITA de funciones SECURITY DEFINER de RULES. La regla "ninguna SECURITY
# DEFINER sin justificacion escrita" es GLOBAL (CA-07 p.6): cada pieza declara SUS
# ventanillas en SU check, y check_identity_access verifica que no exista ninguna
# huerfana consultando la UNION de allowlists (misma forma que MARKET_FUNCTIONS de P07).
RULES_FUNCTIONS: dict[str, str] = {
    "rules_for_market": (
        "Ventanilla de descubrimiento cross-tenant firmada en CA-P08-03. SECURITY "
        "DEFINER imprescindible para leer rule_definition bajo FORCE RLS: el motor "
        "evalua cada tick contra las reglas HABILITADAS de TODOS los tenants para un "
        "par+timeframe, lectura que la RLS de P05 impide (y hace bien) a un rol de "
        "runtime. Retorno minimo (rule_id, tenant_id, product, canonical_rule_hash, "
        "schema_version, definition) sin dato de sujeto. EXECUTE revocado a PUBLIC y "
        "concedido solo a ce_v5_rules."
    ),
}


if __name__ == "__main__":
    sys.exit(main())
