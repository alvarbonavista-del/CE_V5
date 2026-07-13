"""Tests de la logica pura del check identity (P06b, CA-07). Sin PostgreSQL."""

from check_identity_access import (
    IDENTITY_FUNCTIONS,
    IDENTITY_TABLES,
    FunctionFacts,
    TableFacts,
    check_identity,
)

APP = "ce_v5_app"
OPERATOR = "ce_v5_operator"


def _tables() -> dict[str, TableFacts]:
    return {name: TableFacts(name, True, True, True) for name in IDENTITY_TABLES}


def _function(name: str) -> FunctionFacts:
    allowed = IDENTITY_FUNCTIONS[name]
    return FunctionFacts(
        name=name,
        is_security_definer=True,
        config=("search_path=pg_catalog, public",),
        arguments=allowed.arguments,
        result=allowed.result,
        body="SELECT 1",
        execute_for_public=False,
        execute_for_app=True,
    )


def _functions() -> dict[str, FunctionFacts]:
    return {name: _function(name) for name in IDENTITY_FUNCTIONS}


def _privileges() -> dict[tuple[str, str, str], bool]:
    return {}


def test_esquema_conforme_no_tiene_violaciones() -> None:
    assert check_identity(_tables(), _functions(), _privileges()) == []


def test_privilegio_directo_del_rol_de_aplicacion_falla() -> None:
    privileges = {(APP, "user_credential", "SELECT"): True}
    problems = check_identity(_tables(), _functions(), privileges)
    assert any("tiene SELECT directo" in p for p in problems)


def test_privilegio_del_operador_falla() -> None:
    privileges = {(OPERATOR, "app_user", "SELECT"): True}
    problems = check_identity(_tables(), _functions(), privileges)
    assert any(OPERATOR in p for p in problems)


def test_ventanilla_sin_search_path_falla() -> None:
    functions = _functions()
    fn = functions["auth_credential_for_email"]
    functions["auth_credential_for_email"] = FunctionFacts(
        name=fn.name,
        is_security_definer=True,
        config=(),
        arguments=fn.arguments,
        result=fn.result,
        body=fn.body,
        execute_for_public=False,
        execute_for_app=True,
    )
    problems = check_identity(_tables(), functions, _privileges())
    assert any("search_path" in p for p in problems)


def test_ventanilla_con_execute_para_public_falla() -> None:
    functions = _functions()
    fn = functions["auth_register_user"]
    functions["auth_register_user"] = FunctionFacts(
        name=fn.name,
        is_security_definer=True,
        config=fn.config,
        arguments=fn.arguments,
        result=fn.result,
        body=fn.body,
        execute_for_public=True,
        execute_for_app=True,
    )
    problems = check_identity(_tables(), functions, _privileges())
    assert any("PUBLIC" in p for p in problems)


def test_ventanilla_con_sql_dinamico_falla() -> None:
    functions = _functions()
    fn = functions["auth_rotate_session"]
    functions["auth_rotate_session"] = FunctionFacts(
        name=fn.name,
        is_security_definer=True,
        config=fn.config,
        arguments=fn.arguments,
        result=fn.result,
        body="EXECUTE format('SELECT %I', p_refresh_token_hash)",
        execute_for_public=False,
        execute_for_app=True,
    )
    problems = check_identity(_tables(), functions, _privileges())
    assert any("SQL dinamico" in p for p in problems)


def test_retorno_ensanchado_falla() -> None:
    functions = _functions()
    fn = functions["auth_credential_for_email"]
    functions["auth_credential_for_email"] = FunctionFacts(
        name=fn.name,
        is_security_definer=True,
        config=fn.config,
        arguments=fn.arguments,
        result="TABLE(user_id uuid, password_hash text, status text, email text)",
        body=fn.body,
        execute_for_public=False,
        execute_for_app=True,
    )
    problems = check_identity(_tables(), functions, _privileges())
    assert any("el retorno cambio" in p for p in problems)


def test_security_definer_fuera_de_la_allowlist_falla() -> None:
    functions = _functions()
    functions["colada_por_la_puerta_de_atras"] = FunctionFacts(
        name="colada_por_la_puerta_de_atras",
        is_security_definer=True,
        config=("search_path=public",),
        arguments="",
        result="void",
        body="SELECT 1",
        execute_for_public=False,
        execute_for_app=True,
    )
    problems = check_identity(_tables(), functions, _privileges())
    assert any("fuera de la allowlist" in p for p in problems)


def test_tabla_sin_force_rls_falla() -> None:
    tables = _tables()
    tables["user_session"] = TableFacts("user_session", True, True, False)
    problems = check_identity(tables, _functions(), _privileges())
    assert any("FORCE" in p for p in problems)


# --- Convencion de nombres (CA-09 p.3) ---------------------------------------------
# PostgreSQL convierte las columnas de salida en variables de la funcion: si una se
# llamase como una columna, la sentencia seria ambigua y reventaria en ejecucion. Los
# prefijos p_ / v_ / out_ hacen la colision estructuralmente imposible, y estos tests
# vigilan que nadie los abandone.

_CUERPO_CONFORME = (
    "DECLARE\n    v_row user_session%ROWTYPE;\n    v_new_id uuid;\nBEGIN\n"
    "    RETURN;\nEND"
)


def _con_body(name: str, body: str) -> FunctionFacts:
    fn = _function(name)
    return FunctionFacts(
        name=fn.name,
        is_security_definer=True,
        config=fn.config,
        arguments=fn.arguments,
        result=fn.result,
        body=body,
        execute_for_public=False,
        execute_for_app=True,
    )


def test_ventanillas_reales_con_cuerpo_conforme_no_tienen_violaciones() -> None:
    functions = {name: _con_body(name, _CUERPO_CONFORME) for name in IDENTITY_FUNCTIONS}
    assert check_identity(_tables(), functions, _privileges()) == []


def test_parametro_sin_prefijo_p_falla() -> None:
    functions = _functions()
    fn = functions["auth_revoke_session_family"]
    functions["auth_revoke_session_family"] = FunctionFacts(
        name=fn.name,
        is_security_definer=True,
        config=fn.config,
        arguments="refresh_token_hash text",
        result=fn.result,
        body=fn.body,
        execute_for_public=False,
        execute_for_app=True,
    )
    problems = check_identity(_tables(), functions, _privileges())
    assert any("p_" in p for p in problems)


def test_columna_de_salida_sin_prefijo_out_falla() -> None:
    functions = _functions()
    fn = functions["auth_rotate_session"]
    functions["auth_rotate_session"] = FunctionFacts(
        name=fn.name,
        is_security_definer=True,
        config=fn.config,
        arguments=fn.arguments,
        result="TABLE(outcome text, user_id uuid, session_id uuid)",
        body=fn.body,
        execute_for_public=False,
        execute_for_app=True,
    )
    problems = check_identity(_tables(), functions, _privileges())
    assert any("out_" in p for p in problems)


def test_variable_declarada_sin_prefijo_v_falla() -> None:
    functions = _functions()
    functions["auth_revoke_session_family"] = _con_body(
        "auth_revoke_session_family",
        "DECLARE\n    family uuid;\nBEGIN\n    RETURN 0;\nEND",
    )
    problems = check_identity(_tables(), functions, _privileges())
    assert any("v_" in p for p in problems)
