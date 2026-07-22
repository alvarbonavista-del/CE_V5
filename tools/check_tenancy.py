"""Check 7.8: tenancy y RLS (DOC_ESTRUCTURA sec.7.8, ADR-011).

Materializa en CI las reglas de aislamiento del ADR-011: cada tabla declara
su isolation_scope; las tablas de alcance tenant o user llevan tenant_id (y
user_id cuando toca), tienen RLS habilitado Y forzado, y toda su policy ata
la fila al tenant de la transaccion via app_current_tenant_id(); TODA tabla de
alcance system solo se admite si esta en una allowlist explicita, tenga o no
columna tenant_id (asi una tabla con datos de tenant no puede autodeclararse
system y esquivar el registro visible en el diff); y el rol de aplicacion
existe sin poder saltarse el RLS. Corre en cada build contra la base ya
migrada. La logica pura (check_schema) es testeable sin PostgreSQL; solo
load_schema toca el catalogo.
"""

import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "backend" / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(REPO_ROOT / "contracts"))

from ce_v5.infra.db.config import DbConfig  # noqa: E402
from ce_v5.infra.db.ports import Database  # noqa: E402
from ce_v5.infra.db.provision import (  # noqa: E402
    APP_ROLE_NAME,
    INGESTION_ROLE_NAME,
    OPERATOR_ROLE_NAME,
)
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase  # noqa: E402

ISOLATION_SCOPES = ("public_market", "tenant", "user", "system")

# TODA tabla de alcance system exige una entrada explicita aqui con su alcance,
# TENGA O NO columna tenant_id; tambien las public_market sin tenant_id. Es
# deliberado: sumar una linea a esta allowlist es una decision visible en el
# diff (alguien la revisa en el PR), no un descuido que se cuela. Si una tabla
# system no aparece aqui, el check 7.8 falla.
TABLAS_SIN_TENANT_PERMITIDAS: dict[str, str] = {
    "outbox": "system",
    "inbox": "system",
    "audit_log": "system",
    "schema_migrations": "system",
    # Catalogo/artefacto de PLATAFORMA (P06), no superficie de tenant:
    "policy_version": "system",  # edicion vigente del reglamento (ADR-012).
    "policy_rule": "system",  # reglamento de negocio de Alvaro (ADR-012).
    "kill_switch": "system",  # artefacto de operador (CA-03); no es de tenant.
    "operator_audit": "system",  # auditoria canonica de operador (CA-05).
    # Canon de IDENTIDAD (P06b, CA-07 opcion A). El canon de identidad PRECEDE al
    # tenant: el tenant se DERIVA de la pertenencia user->tenant (ADR-011). Darles
    # tenant_id invertiria la causalidad (haria falta un tenant para autenticar,
    # cuando el tenant se obtiene DESPUES de autenticar y resolver pertenencia).
    # Compensacion: el rol de aplicacion NO tiene NINGUN privilegio de tabla sobre
    # ellas; solo ejecuta las cinco ventanillas SECURITY DEFINER. Lo verifica el
    # check bloqueante tools/check_identity_access.py.
    "app_user": "system",
    "user_credential": "system",
    "user_session": "system",
    # MARKET DATA PUBLICA (P07, ADR-014). Sin tenant_id A PROPOSITO: el dato
    # publico se comparte cross-tenant y un solo stream sirve a todos los
    # interesados (ADR-011: los publicos NO se duplican por tenant). Darles
    # tenant_id multiplicaria el mismo hecho de mercado por cada tenant y seria
    # exactamente la explosion N x M que ADR-014 existe para evitar.
    # Compensacion (regla 5.20): el rol de aplicacion solo tiene SELECT; el UNICO
    # que puede escribirlas es ce_v5_ingestion. Lo verifica el check bloqueante
    # tools/check_market_access.py.
    "market_candle": "public_market",
    "market_instrument": "public_market",
    "market_trade": "public_market",
    "market_footprint": "public_market",
}

# Roles de RUNTIME: los que se conectan con una credencial en un proceso vivo.
# Una policy allowlistada JAMAS puede aplicar a uno de ellos.
RUNTIME_ROLES: tuple[str, ...] = (
    APP_ROLE_NAME,
    INGESTION_ROLE_NAME,
    OPERATOR_ROLE_NAME,
)


@dataclass(frozen=True, slots=True)
class AllowedPolicy:
    """Una policy admitida que NO ata la fila al tenant, con su justificacion.

    R5 (toda policy de una tabla tenant/user ata la fila al tenant) SIGUE SIENDO
    LA REGLA POR DEFECTO. Esta allowlist es la UNICA excepcion, y es mas estrecha
    que la regla que relaja: exige lectura pura, sin roles de runtime y con el
    filtro declarado presente en la expresion REAL del catalogo.
    """

    command: str  # comando permitido (SELECT); nunca escritura.
    required_in_using: tuple[str, ...]  # fragmentos que DEBEN estar en el USING.
    justification: str


# ALLOWLIST EXPLICITA de policies NO atadas al tenant (CA-P07-G, firmada).
# Misma filosofia que la allowlist de tablas (P05/D7): la excepcion es UNA LINEA
# VISIBLE EN EL DIFF con justificacion escrita, no un descuido que se cuela.
POLICIES_SIN_TENANT_PERMITIDAS: dict[tuple[str, str], AllowedPolicy] = {
    ("market_subscription_intent", "market_intent_owner_read"): AllowedPolicy(
        command="SELECT",
        required_in_using=("stream_scope", "public_market"),
        justification=(
            "Policy del DUENO de la ventanilla market_public_demand (CA-P07-D/G). "
            "Una SECURITY DEFINER corre con los privilegios de su dueno y FORCE RLS "
            "somete tambien al dueno: sin ella la ventanilla veria CERO FILAS y la "
            "agregacion cross-tenant que exige ADR-014 seria imposible. No puede "
            "atarse al tenant porque su funcion es precisamente agregar A TRAVES de "
            "tenants. Va estrechada a SELECT y a stream_scope='public_market': ni el "
            "dueno lee por esta via los intereses privados/BYOC. NO aplica a ningun "
            "rol de runtime, y el worker solo obtiene CUANTOS piden un stream, jamas "
            "QUIENES."
        ),
    ),
    ("rule_definition", "rule_definition_owner_read"): AllowedPolicy(
        command="SELECT",
        required_in_using=("enabled",),
        justification=(
            "Policy del DUENO de la ventanilla rules_for_market (P08, mismo patron "
            "que market_intent_owner_read de P07). Una SECURITY DEFINER corre con los "
            "privilegios de su dueno y FORCE RLS somete tambien al dueno: sin ella la "
            "ventanilla veria CERO FILAS y la evaluacion cross-tenant que necesita el "
            "motor seria imposible. No puede atarse al tenant porque su funcion es "
            "precisamente leer las reglas de TODOS los tenants para un par+timeframe. "
            "Va estrechada a SELECT y a enabled=true: el dueno solo ve por esta via lo "
            "que la ventanilla expone. NO aplica a ningun rol de runtime."
        ),
    ),
}

_TENANT_SCOPES = ("tenant", "user")
_APP_TENANT_FN = "app_current_tenant_id()"
_SCOPE_RE = re.compile(r"isolation_scope=([a-z_]+)")


@dataclass(frozen=True, slots=True)
class PolicyInfo:
    """Una policy de RLS y sus expresiones USING / WITH CHECK reconstruidas.

    roles y command salen del CATALOGO (pg_policies), no de un regex sobre el
    fichero .sql: un regex comprueba lo que alguien ESCRIBIO; el catalogo
    comprueba lo que la base REALMENTE TIENE.
    """

    table: str
    name: str
    using_expr: str
    with_check_expr: str
    roles: tuple[str, ...]
    command: str


@dataclass(frozen=True, slots=True)
class TableInfo:
    """Una tabla del esquema public con lo que el check 7.8 necesita mirar."""

    name: str
    declared_scope: str | None
    has_rls: bool
    has_force_rls: bool
    columns: frozenset[str]
    policies: tuple[PolicyInfo, ...]


@dataclass(frozen=True, slots=True)
class AppRoleInfo:
    """Atributos del rol de aplicacion que deciden si el RLS le aplica."""

    name: str
    is_superuser: bool
    can_bypass_rls: bool


def _policy_uses_tenant(policy: PolicyInfo) -> bool:
    return (
        _APP_TENANT_FN in policy.using_expr or _APP_TENANT_FN in policy.with_check_expr
    )


def _table_violations(table: TableInfo) -> list[str]:
    out: list[str] = []
    scope = table.declared_scope

    # R1: isolation_scope declarado y reconocido.
    if scope is None:
        out.append(
            f"{table.name}: R1 sin isolation_scope en el COMMENT de la tabla "
            "(ADR-011, 7.8): toda tabla declara su alcance de aislamiento."
        )
        return out
    if scope not in ISOLATION_SCOPES:
        out.append(
            f"{table.name}: R1 isolation_scope '{scope}' no reconocido "
            f"(ADR-011, 7.8); validos: {', '.join(ISOLATION_SCOPES)}."
        )
        return out

    has_tenant = "tenant_id" in table.columns
    scoped = scope in _TENANT_SCOPES

    # R2: alcance tenant o user exige columna tenant_id.
    if scoped and not has_tenant:
        out.append(
            f"{table.name}: R2 alcance '{scope}' sin columna tenant_id (ADR-011, 7.8)."
        )

    # R3: alcance user exige ademas user_id u owner_user_id.
    if scope == "user" and not (
        "user_id" in table.columns or "owner_user_id" in table.columns
    ):
        out.append(
            f"{table.name}: R3 alcance 'user' sin columna user_id ni "
            "owner_user_id (ADR-011, 7.8)."
        )

    # R4: alcance tenant o user exige RLS habilitado Y forzado.
    if scoped and not (table.has_rls and table.has_force_rls):
        out.append(
            f"{table.name}: R4 alcance '{scope}' requiere RLS habilitado y "
            "forzado (ENABLE + FORCE ROW LEVEL SECURITY) (ADR-011, 7.8)."
        )

    # R5: alcance tenant o user exige policies, todas atadas al tenant. La UNICA
    # excepcion es una policy allowlistada, y entra por la puerta estrecha de R8.
    if scoped:
        if not table.policies:
            out.append(
                f"{table.name}: R5 alcance '{scope}' sin ninguna policy de RLS "
                "(ADR-011, 7.8)."
            )
        for policy in table.policies:
            if _policy_uses_tenant(policy):
                continue
            allowed = POLICIES_SIN_TENANT_PERMITIDAS.get((table.name, policy.name))
            if allowed is None:
                out.append(
                    f"{table.name}: R5 la policy '{policy.name}' no referencia "
                    f"{_APP_TENANT_FN} en USING ni en WITH CHECK (ADR-011, "
                    "7.8): no ata la fila al tenant de la transaccion."
                )
                continue
            out.extend(_allowed_policy_violations(table, policy, allowed))

    # R6: clasificacion sin superficie de tenant propia. TODA tabla 'system'
    # debe estar en la allowlist explicita, TENGA O NO columna tenant_id: si no,
    # una tabla con datos de tenant podria autodeclararse system y esquivar el
    # registro visible en el diff. Las 'public_market' sin tenant_id, tambien.
    needs_allowlist = scope == "system" or (scope == "public_market" and not has_tenant)
    if needs_allowlist:
        permitido = TABLAS_SIN_TENANT_PERMITIDAS.get(table.name)
        if permitido is None:
            if scope == "system":
                out.append(
                    f"{table.name}: R6 tabla system no allowlistada: anadela a "
                    "la allowlist de tools/check_tenancy.py con justificacion "
                    "escrita (ADR-011, 7.8)."
                )
            else:
                out.append(
                    f"{table.name}: R6 tabla sin tenant_id fuera de la allowlist "
                    "TABLAS_SIN_TENANT_PERMITIDAS (ADR-011, 7.8): declara su "
                    "alcance alli de forma explicita o anade tenant_id."
                )
        elif permitido != scope:
            out.append(
                f"{table.name}: R6 la allowlist permite alcance '{permitido}' "
                f"pero la tabla declara '{scope}' (ADR-011, 7.8)."
            )

    return out


def _allowed_policy_violations(
    table: TableInfo, policy: PolicyInfo, allowed: AllowedPolicy
) -> list[str]:
    """R8/R9 (CA-P07-G): la excepcion de R5 es MAS ESTRECHA que la regla que relaja.

    Una policy allowlistada solo se sostiene si: no alcanza a ningun rol de
    runtime, es de LECTURA pura, no tiene WITH CHECK, conserva el filtro que
    justifico la excepcion, y la tabla mantiene su RLS activa.
    """
    out: list[str] = []

    # R8a: jamas alcanza a un rol de runtime.
    alcanzados = [role for role in policy.roles if role in RUNTIME_ROLES]
    if alcanzados:
        out.append(
            f"{table.name}: R8a la policy allowlistada '{policy.name}' alcanza al "
            f"rol de runtime {', '.join(alcanzados)} (CA-P07-G): una policy "
            "allowlistada que alcanza a un rol de runtime es una puerta abierta al "
            "aislamiento; el rol de runtime debe pasar por la policy atada al tenant."
        )

    # R8b: la excepcion es de LECTURA.
    if policy.command.upper() != allowed.command.upper():
        out.append(
            f"{table.name}: R8b la policy allowlistada '{policy.name}' tiene comando "
            f"'{policy.command}' y solo se admite '{allowed.command}' (CA-P07-G): la "
            "excepcion es de LECTURA; una policy allowlistada no escribe."
        )

    # R8c: WITH CHECK solo tiene sentido al escribir.
    if policy.with_check_expr.strip():
        out.append(
            f"{table.name}: R8c la policy allowlistada '{policy.name}' declara WITH "
            "CHECK (CA-P07-G): WITH CHECK solo tiene sentido al escribir, y esta "
            "excepcion no escribe."
        )

    # R8d: el filtro que JUSTIFICO la excepcion sigue en la expresion REAL.
    using = policy.using_expr.lower()
    perdidos = [
        fragment
        for fragment in allowed.required_in_using
        if fragment.lower() not in using
    ]
    if perdidos:
        out.append(
            f"{table.name}: R8d la policy allowlistada '{policy.name}' ha perdido su "
            f"filtro (falta {', '.join(perdidos)} en el USING real del catalogo, "
            f"que es: '{policy.using_expr}') (CA-P07-G): sin ese filtro podria leer "
            "intereses privados/BYOC, que es justo lo que la excepcion prometia no "
            "hacer."
        )

    # R9: la excepcion se APOYA en que la RLS esta activa. Si la RLS cae, la
    # excepcion no vale nada (R4 ya lo cubre para tenant/user; aqui se declara
    # explicito porque es la premisa de la que cuelga toda la justificacion).
    if not (table.has_rls and table.has_force_rls):
        out.append(
            f"{table.name}: R9 la policy allowlistada '{policy.name}' existe pero la "
            "tabla no tiene RLS habilitado Y forzado (CA-P07-G): la excepcion se "
            "apoya en que la RLS esta activa; sin ella no se sostiene."
        )

    return out


def _app_role_violations(app_role: AppRoleInfo | None) -> list[str]:
    # R7: el rol de aplicacion existe y no puede saltarse el RLS.
    if app_role is None:
        return [
            f"R7 el rol de aplicacion {APP_ROLE_NAME} no existe (ADR-011, "
            "7.8): sin ese rol el RLS no tiene a quien aplicarse."
        ]
    out: list[str] = []
    if app_role.is_superuser:
        out.append(
            f"R7 el rol de aplicacion {app_role.name} tiene SUPERUSER "
            "(ADR-011, 7.8): se saltaria el RLS y el aislamiento seria decorativo."
        )
    if app_role.can_bypass_rls:
        out.append(
            f"R7 el rol de aplicacion {app_role.name} tiene BYPASSRLS "
            "(ADR-011, 7.8): se saltaria el RLS y el aislamiento seria decorativo."
        )
    return out


def check_schema(
    tables: Sequence[TableInfo], app_role: AppRoleInfo | None
) -> list[str]:
    """Logica pura del check 7.8: devuelve las violaciones (vacia = verde)."""
    violations: list[str] = []
    for table in tables:
        violations.extend(_table_violations(table))
    violations.extend(_app_role_violations(app_role))
    return violations


_TABLES_SQL = """
SELECT c.relname,
       obj_description(c.oid, 'pg_class'),
       c.relrowsecurity,
       c.relforcerowsecurity
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname = 'public'
ORDER BY c.relname
"""

_COLUMNS_SQL = """
SELECT c.relname, a.attname
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname = 'public'
  AND a.attnum > 0 AND NOT a.attisdropped
"""

# roles y cmd salen del catalogo: lo que la base TIENE, no lo que el .sql dice.
_POLICIES_SQL = """
SELECT tablename, policyname, coalesce(qual, ''), coalesce(with_check, ''),
       roles, cmd
FROM pg_policies
WHERE schemaname = 'public'
"""

_ROLE_SQL = "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s"


def _scope_from_comment(comment: str | None) -> str | None:
    if comment is None:
        return None
    match = _SCOPE_RE.search(comment)
    return match.group(1) if match is not None else None


def load_schema(database: Database) -> tuple[list[TableInfo], AppRoleInfo | None]:
    """Lee el esquema del catalogo (pg_catalog / pg_policies, nunca information_schema).

    information_schema oculta objetos segun los privilegios del rol conectado
    y podria dejar pasar una tabla sin grants; el catalogo del sistema los ve
    todos. Por eso el check corre con el DSN de migraciones.
    """
    with database.transaction() as session:
        table_rows = session.fetchall(_TABLES_SQL)
        column_rows = session.fetchall(_COLUMNS_SQL)
        policy_rows = session.fetchall(_POLICIES_SQL)
        role_row = session.fetchone(_ROLE_SQL, (APP_ROLE_NAME,))

    columns: dict[str, set[str]] = {}
    for relname, attname in column_rows:
        columns.setdefault(str(relname), set()).add(str(attname))

    policies: dict[str, list[PolicyInfo]] = {}
    for tablename, policyname, qual, with_check, roles, cmd in policy_rows:
        # pg_policies.roles es name[]; psycopg lo entrega como lista.
        role_names: tuple[str, ...] = ()
        if isinstance(roles, list | tuple):
            role_names = tuple(str(role) for role in roles)
        policies.setdefault(str(tablename), []).append(
            PolicyInfo(
                table=str(tablename),
                name=str(policyname),
                using_expr=str(qual),
                with_check_expr=str(with_check),
                roles=role_names,
                command=str(cmd),
            )
        )

    tables: list[TableInfo] = []
    for relname, comment, relrowsecurity, relforce in table_rows:
        name = str(relname)
        tables.append(
            TableInfo(
                name=name,
                declared_scope=_scope_from_comment(
                    None if comment is None else str(comment)
                ),
                has_rls=bool(relrowsecurity),
                has_force_rls=bool(relforce),
                columns=frozenset(columns.get(name, set())),
                policies=tuple(policies.get(name, [])),
            )
        )

    app_role: AppRoleInfo | None = None
    if role_row is not None:
        app_role = AppRoleInfo(
            name=APP_ROLE_NAME,
            is_superuser=bool(role_row[0]),
            can_bypass_rls=bool(role_row[1]),
        )
    return tables, app_role


def main() -> int:
    database = PsycopgDatabase(DbConfig.migrations_from_env())
    try:
        tables, app_role = load_schema(database)
    finally:
        database.close()

    violations = check_schema(tables, app_role)
    if violations:
        print("FAIL check 7.8 (tenancy/RLS):")
        for v in violations:
            print(f"  - {v}")
        return 1

    print(
        f"OK check 7.8 (tenancy/RLS): {len(tables)} tablas revisadas, "
        "aislamiento y RLS conformes (ADR-011). "
        f"{len(POLICIES_SIN_TENANT_PERMITIDAS)} policy(s) allowlistada(s) sin atadura "
        "al tenant (CA-P07-G), cada una verificada contra R8a-d y R9."
    )
    for table in tables:
        print(f"  - {table.name}: isolation_scope={table.declared_scope}")
    # La excepcion es VISIBLE en cada build: una excepcion invisible se olvida.
    for (tabla, policy), allowed in POLICIES_SIN_TENANT_PERMITIDAS.items():
        print(f"  - policy allowlistada: {tabla}.{policy} ({allowed.command})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
