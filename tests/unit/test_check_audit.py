"""Unit tests de la logica pura del check audit (P06), sin PostgreSQL."""

from __future__ import annotations

import check_audit
from check_audit import AuditTable

_APP = "ce_v5_app"
_OPERATOR = "ce_v5_operator"
_SENSITIVE = "sensitive_action_audit"
_OPERATOR_AUDIT = "operator_audit"


def _table(
    name: str,
    *,
    exists: bool = True,
    has_rls: bool = True,
    has_force_rls: bool = True,
    columns: frozenset[str] | None = None,
) -> AuditTable:
    return AuditTable(
        name=name,
        exists=exists,
        columns=(
            columns if columns is not None else check_audit._REQUIRED_COLUMNS[name]
        ),
        has_rls=has_rls,
        has_force_rls=has_force_rls,
    )


def _ok_tables() -> dict[str, AuditTable]:
    return {
        _SENSITIVE: _table(_SENSITIVE),
        _OPERATOR_AUDIT: _table(_OPERATOR_AUDIT),
    }


def test_catalogo_correcto_no_tiene_violaciones() -> None:
    assert check_audit.check_audit(_ok_tables(), {}) == []


def test_app_con_update_en_sensitive_es_violacion() -> None:
    problems = check_audit.check_audit(
        _ok_tables(), {(_APP, _SENSITIVE, "UPDATE"): True}
    )
    assert len(problems) == 1
    assert _APP in problems[0] and "UPDATE" in problems[0]


def test_app_con_delete_en_operator_audit_es_violacion() -> None:
    problems = check_audit.check_audit(
        _ok_tables(), {(_APP, _OPERATOR_AUDIT, "DELETE"): True}
    )
    assert any(_APP in p and "DELETE" in p for p in problems)


def test_operador_con_select_en_sensitive_es_violacion() -> None:
    problems = check_audit.check_audit(
        _ok_tables(), {(_OPERATOR, _SENSITIVE, "SELECT"): True}
    )
    assert any(_OPERATOR in p and "SELECT" in p and "por sujeto" in p for p in problems)


def test_operador_con_delete_en_operator_audit_es_violacion() -> None:
    problems = check_audit.check_audit(
        _ok_tables(), {(_OPERATOR, _OPERATOR_AUDIT, "DELETE"): True}
    )
    assert any(_OPERATOR in p and "reescribirla" in p for p in problems)


def test_sin_rls_force_es_violacion() -> None:
    tables = _ok_tables()
    tables[_SENSITIVE] = _table(_SENSITIVE, has_force_rls=False)
    problems = check_audit.check_audit(tables, {})
    assert any("RLS" in p for p in problems)


def test_tabla_inexistente_es_violacion() -> None:
    tables = _ok_tables()
    tables[_OPERATOR_AUDIT] = _table(_OPERATOR_AUDIT, exists=False)
    problems = check_audit.check_audit(tables, {})
    assert any("no existe" in p for p in problems)


def test_columna_faltante_es_violacion() -> None:
    tables = _ok_tables()
    faltan = frozenset(check_audit._REQUIRED_COLUMNS[_SENSITIVE] - {"context"})
    tables[_SENSITIVE] = _table(_SENSITIVE, columns=faltan)
    problems = check_audit.check_audit(tables, {})
    assert any("faltan columnas" in p and "context" in p for p in problems)
