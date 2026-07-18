"""Check "identity" (P06b, CA-07 opcion A): las ventanillas de identidad son estrechas.

Sin este check, la opcion A de CA-07 seria una promesa escrita en un documento. Con
el, es un hecho verificado en cada build. FALLA si: el rol de aplicacion tiene
CUALQUIER privilegio directo de tabla sobre app_user/user_credential/user_session; una
ventanilla no es SECURITY DEFINER, no fija search_path, tiene EXECUTE para PUBLIC, no
lo tiene para el rol de aplicacion, usa SQL dinamico, o cambia su firma o su retorno
(ensanchar la ventanilla es tan grave como abrir la puerta); aparece una funcion
SECURITY DEFINER nueva que NO esta en la allowlist explicita con justificacion escrita
(CA-07 p.6); alguna tabla de identidad pierde RLS ENABLE+FORCE; o alguna deja de estar
allowlistada en el check 7.8.

GUARDIA GLOBAL DE SECURITY DEFINER: la regla "ninguna SECURITY DEFINER sin
justificacion escrita" (CA-07 p.6) NO es de identidad, es de TODO el esquema. Cada pieza
declara SUS ventanillas en SU propio check (identidad aqui, market en
tools/check_market_access.py, rules en tools/check_rules_access.py), y este guardia
consulta la UNION de esas allowlists para que no sobreviva ninguna funcion HUERFANA. Los
roles de runtime vigilados incluyen al INGESTOR (regla 5.20): el ingestor no lee
credenciales, y eso deja de ser una promesa.

Lee el catalogo con pg_catalog y has_table_privilege/has_function_privilege (NUNCA
information_schema, que oculta objetos segun privilegios), con el DSN de migraciones
para visibilidad total. La logica pura (check_identity) es testeable sin PostgreSQL;
solo load_identity_facts toca el catalogo.
"""

import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
from check_tenancy import TABLAS_SIN_TENANT_PERMITIDAS  # noqa: E402

IDENTITY_TABLES: tuple[str, ...] = ("app_user", "user_credential", "user_session")

# Ningun privilegio de tabla, de ningun tipo, para ningun rol de runtime.
_TABLE_PRIVILEGES: tuple[str, ...] = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
# El INGESTOR (P07) entra aqui: no toca identidad. Hasta ahora eso era una promesa
# (nadie le habia dado privilegios); desde la regla 5.20 se VERIFICA en cada build.
# Es la direccion (b) de la prueba negativa bidireccional: la API no escribe velas, y
# el ingestor no lee credenciales.
_RUNTIME_ROLES: tuple[str, ...] = (
    APP_ROLE_NAME,
    OPERATOR_ROLE_NAME,
    INGESTION_ROLE_NAME,
)

# Marcadores de SQL dinamico en el cuerpo de una funcion. El SQL dinamico dentro de
# una SECURITY DEFINER es la via directa a la inyeccion con privilegios de dueno.
_DYNAMIC_SQL_MARKERS: tuple[str, ...] = (
    "execute ",
    "execute(",
    "format(",
    "quote_ident",
    "quote_literal",
    "dblink",
)
_SPACES = re.compile(r"\s+")
_DECLARE_BLOCK = re.compile(r"\bDECLARE\b(.*?)\bBEGIN\b", re.IGNORECASE | re.DOTALL)


def _normalize(text: str) -> str:
    return _SPACES.sub(" ", text).strip().lower()


@dataclass(frozen=True, slots=True)
class AllowedFunction:
    """Una ventanilla admitida, con su firma EXACTA y su justificacion escrita."""

    arguments: str
    result: str
    justification: str


# ALLOWLIST EXPLICITA de funciones SECURITY DEFINER (CA-07 p.6/p.7). Cualquier funcion
# SECURITY DEFINER del esquema public que NO este aqui rompe el build: una via de
# escalada de privilegios no entra por descuido, entra por decision escrita y revisada.
IDENTITY_FUNCTIONS: dict[str, AllowedFunction] = {
    "auth_register_user": AllowedFunction(
        arguments="p_email text, p_password_hash text",
        result="uuid",
        justification=(
            "Alta de usuario: el rol de aplicacion no puede INSERT en app_user. "
            "Recibe el hash ya calculado en Python; no devuelve datos de nadie."
        ),
    ),
    "auth_credential_for_email": AllowedFunction(
        arguments="p_email text",
        result="TABLE(out_user_id uuid, out_password_hash text, out_status text)",
        justification=(
            "Login: UNA fila para UN email exacto. Sin comodines ni patrones; no "
            "enumera usuarios. La verificacion Argon2id ocurre en Python."
        ),
    ),
    "auth_create_session": AllowedFunction(
        arguments=(
            "p_user_id uuid, p_refresh_token_hash text, "
            "p_expires_at timestamp with time zone"
        ),
        result="uuid",
        justification=(
            "Apertura de sesion: guarda el HASH del refresh token. Devuelve solo el "
            "identificador de sesion."
        ),
    ),
    "auth_rotate_session": AllowedFunction(
        arguments=(
            "p_refresh_token_hash text, p_new_refresh_token_hash text, "
            "p_expires_at timestamp with time zone"
        ),
        result="TABLE(out_outcome text, out_user_id uuid, out_session_id uuid)",
        justification=(
            "Rotacion de refresh token con deteccion de reuso: un token gastado "
            "revoca la familia entera. Atomica dentro de una transaccion."
        ),
    ),
    "auth_revoke_session_family": AllowedFunction(
        arguments="p_refresh_token_hash text",
        result="integer",
        justification=(
            "Logout: revoca la familia de sesiones del token. No devuelve datos."
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class TableFacts:
    """Estado de una tabla de identidad segun el catalogo."""

    name: str
    exists: bool
    has_rls: bool
    has_force_rls: bool


@dataclass(frozen=True, slots=True)
class FunctionFacts:
    """Estado de una funcion del esquema public segun el catalogo."""

    name: str
    is_security_definer: bool
    config: tuple[str, ...]
    arguments: str
    result: str
    body: str
    execute_for_public: bool
    execute_for_app: bool
    # Los usa check_market_access (P07): su ventanilla la ejecuta el INGESTOR y NO
    # deben ejecutarla ni la app ni el operador. Por defecto False para no obligar a
    # este check, que no los mira, a rellenarlos.
    execute_for_ingestion: bool = False
    execute_for_operator: bool = False

    def has_fixed_search_path(self) -> bool:
        return any(item.lower().startswith("search_path=") for item in self.config)

    def uses_dynamic_sql(self) -> bool:
        body = self.body.lower()
        return any(marker in body for marker in _DYNAMIC_SQL_MARKERS)

    def argument_names(self) -> tuple[str, ...]:
        """Nombres de los parametros de entrada, en orden."""
        if not self.arguments.strip():
            return ()
        names: list[str] = []
        for chunk in self.arguments.split(","):
            parts = chunk.strip().split()
            if parts:
                names.append(parts[0].lower())
        return tuple(names)

    def result_column_names(self) -> tuple[str, ...]:
        """Nombres de las columnas de salida si el retorno es TABLE(...)."""
        result = self.result.strip()
        if not result.lower().startswith("table("):
            return ()
        inner = result[result.index("(") + 1 : result.rindex(")")]
        names: list[str] = []
        for chunk in inner.split(","):
            parts = chunk.strip().split()
            if parts:
                names.append(parts[0].lower())
        return tuple(names)

    def declared_variable_names(self) -> tuple[str, ...]:
        """Nombres declarados en los bloques DECLARE del cuerpo."""
        names: list[str] = []
        for block in _DECLARE_BLOCK.findall(self.body):
            for line in block.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("--"):
                    continue
                names.append(stripped.split()[0].lower())
        return tuple(names)


def _table_violations(tables: Mapping[str, TableFacts]) -> list[str]:
    out: list[str] = []
    for name in IDENTITY_TABLES:
        table = tables.get(name)
        if table is None or not table.exists:
            out.append(f"{name}: la tabla de identidad no existe (P06b, CA-07).")
            continue
        if not (table.has_rls and table.has_force_rls):
            out.append(
                f"{name}: sin RLS ENABLE + FORCE (CA-07 p.2): no se elimina el RLS "
                "por ser system."
            )
        if TABLAS_SIN_TENANT_PERMITIDAS.get(name) != "system":
            out.append(
                f"{name}: no esta allowlistada como system en tools/check_tenancy.py "
                "(CA-07 p.1): su clasificacion debe ser visible en el diff."
            )
    return out


def _privilege_violations(privileges: Mapping[tuple[str, str, str], bool]) -> list[str]:
    out: list[str] = []
    for role in _RUNTIME_ROLES:
        for table in IDENTITY_TABLES:
            for privilege in _TABLE_PRIVILEGES:
                if privileges.get((role, table, privilege), False):
                    out.append(
                        f"{table}: el rol {role} tiene {privilege} directo (CA-07 "
                        "p.3): los roles de runtime no tocan las tablas de "
                        "identidad, solo ejecutan las ventanillas."
                    )
    return out


def _function_violations(functions: Mapping[str, FunctionFacts]) -> list[str]:
    out: list[str] = []
    for name, allowed in IDENTITY_FUNCTIONS.items():
        fn = functions.get(name)
        if fn is None:
            out.append(f"{name}: la ventanilla no existe (P06b, CA-07 p.4).")
            continue
        if not fn.is_security_definer:
            out.append(f"{name}: no es SECURITY DEFINER (CA-07 p.4).")
        if not fn.has_fixed_search_path():
            out.append(
                f"{name}: sin search_path fijado (CA-07 p.4): el secuestro de "
                "search_path es EL exploit clasico de SECURITY DEFINER."
            )
        if fn.execute_for_public:
            out.append(
                f"{name}: tiene EXECUTE para PUBLIC (CA-07 p.4): la ventanilla es "
                "solo para el rol de aplicacion."
            )
        if not fn.execute_for_app:
            out.append(
                f"{name}: el rol {APP_ROLE_NAME} no tiene EXECUTE (CA-07 p.4): la "
                "API no podria autenticar a nadie."
            )
        if fn.uses_dynamic_sql():
            out.append(
                f"{name}: usa SQL dinamico (CA-07 p.4): dentro de una SECURITY "
                "DEFINER es la via directa a la inyeccion con privilegios de dueno."
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
        for argument in fn.argument_names():
            if not argument.startswith("p_"):
                out.append(
                    f"{name}: el parametro '{argument}' no usa el prefijo p_ (CA-09 "
                    "p.3): sin la convencion, un nombre puede colisionar con una "
                    "columna y la sentencia se vuelve ambigua en ejecucion."
                )
        for column in fn.result_column_names():
            if not column.startswith("out_"):
                out.append(
                    f"{name}: la columna de salida '{column}' no usa el prefijo out_ "
                    "(CA-09 p.3): PostgreSQL la convierte en una variable de la "
                    "funcion y colisionaria con la columna del mismo nombre."
                )
        for variable in fn.declared_variable_names():
            if not variable.startswith("v_"):
                out.append(
                    f"{name}: la variable declarada '{variable}' no usa el prefijo v_ "
                    "(CA-09 p.3)."
                )
    # La regla "ninguna SECURITY DEFINER sin justificacion escrita" (CA-07 p.6) es
    # GLOBAL, no de identidad: cada pieza declara SUS ventanillas en SU check, y este
    # guardia verifica que no exista ninguna HUERFANA. Por eso consulta la UNION de
    # allowlists.
    #
    # Import DIFERIDO a proposito: check_market_access y check_rules_access importan
    # FunctionFacts (y AllowedFunction) de ESTE modulo; un import de nivel de modulo
    # aqui cerraria el ciclo y ninguno de los dos cargaria.
    from check_market_access import MARKET_FUNCTIONS  # noqa: PLC0415
    from check_rules_access import RULES_FUNCTIONS  # noqa: PLC0415

    allowlisted = set(IDENTITY_FUNCTIONS) | set(MARKET_FUNCTIONS) | set(RULES_FUNCTIONS)
    for name, fn in functions.items():
        if fn.is_security_definer and name not in allowlisted:
            out.append(
                f"{name}: funcion SECURITY DEFINER fuera de la allowlist (CA-07 "
                "p.6): toda SECURITY DEFINER exige justificacion escrita y revision "
                "explicita; declarala en la allowlist de SU pieza "
                "(IDENTITY_FUNCTIONS o MARKET_FUNCTIONS) o retirala."
            )
    return out


def check_identity(
    tables: Mapping[str, TableFacts],
    functions: Mapping[str, FunctionFacts],
    privileges: Mapping[tuple[str, str, str], bool],
) -> list[str]:
    """Logica pura del check identity: devuelve las violaciones (vacia = verde)."""
    problems: list[str] = []
    problems.extend(_table_violations(tables))
    problems.extend(_privilege_violations(privileges))
    problems.extend(_function_violations(functions))
    return problems


_TABLES_SQL = """
SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname = 'public'
"""

_FUNCTIONS_SQL = """
SELECT p.proname,
       p.prosecdef,
       coalesce(p.proconfig, ARRAY[]::text[]),
       pg_get_function_arguments(p.oid),
       pg_get_function_result(p.oid),
       p.prosrc,
       has_function_privilege('public', p.oid, 'EXECUTE'),
       has_function_privilege(%s, p.oid, 'EXECUTE')
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
"""


def _read_privileges(
    rows: Sequence[tuple[object, ...]],
) -> dict[tuple[str, str, str], bool]:
    return {(str(row[0]), str(row[1]), str(row[2])): bool(row[3]) for row in rows}


def load_identity_facts(
    database: Database,
) -> tuple[
    dict[str, TableFacts],
    dict[str, FunctionFacts],
    dict[tuple[str, str, str], bool],
]:
    """Lee del catalogo las tablas de identidad, las funciones y los privilegios."""
    with database.transaction() as session:
        table_rows = session.fetchall(_TABLES_SQL)
        function_rows = session.fetchall(_FUNCTIONS_SQL, (APP_ROLE_NAME,))

        present = {str(row[0]): (bool(row[1]), bool(row[2])) for row in table_rows}
        tables: dict[str, TableFacts] = {}
        for name in IDENTITY_TABLES:
            if name in present:
                has_rls, has_force = present[name]
                tables[name] = TableFacts(name, True, has_rls, has_force)
            else:
                tables[name] = TableFacts(name, False, False, False)

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
            )

        combos = [
            (role, table, privilege)
            for role in _RUNTIME_ROLES
            for table in IDENTITY_TABLES
            for privilege in _TABLE_PRIVILEGES
            if tables[table].exists
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
            privileges = _read_privileges(session.fetchall(priv_sql, params))

    return tables, functions, privileges


def main() -> int:
    database = PsycopgDatabase(DbConfig.migrations_from_env())
    try:
        tables, functions, privileges = load_identity_facts(database)
    finally:
        database.close()
    problems = check_identity(tables, functions, privileges)
    if problems:
        print("FAIL check identity (ventanillas de identidad, P06b/CA-07):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        "OK check identity (P06b/CA-07): el rol de aplicacion no tiene ningun "
        f"privilegio de tabla sobre {', '.join(IDENTITY_TABLES)}; las "
        f"{len(IDENTITY_FUNCTIONS)} ventanillas son SECURITY DEFINER con search_path "
        "fijo, sin SQL dinamico, sin EXECUTE para PUBLIC y con la firma esperada."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
