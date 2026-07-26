"""Tests de la logica pura del check rules (P08, CA-P08-03 / D1). Sin PostgreSQL.

El check ya existia y el CI lo corre; lo que no existia era esta suite en frio. Se
anade con la remediacion G2 de P07c porque la superficie que se acaba de cerrar -- el
motor de reglas no ESCRIBE el libro L2 (CSA item 22) -- necesitaba una prueba que
mordiera sin base de datos: si manana alguien concede un INSERT sobre
market_orderbook_snapshot al rol de reglas, esto se pone rojo aqui, no en produccion.

Las pruebas contra el MOTOR real viven en tests/integration/test_rules_access.py.
"""

from __future__ import annotations

import check_rules_access
from check_identity_access import FunctionFacts
from check_rules_access import check_rules

RULES = "ce_v5_rules"

_OUTBOX_OK = (
    "(event_type ~~ 'rule.%'::text OR event_type ~~ 'signal.%'::text "
    "OR event_type ~~ 'alert.%'::text)"
)


def _funcion() -> FunctionFacts:
    """La ventanilla CONFORME: el caso verde del que parten los negativos."""
    columnas = ", ".join(
        f"{nombre} text" for nombre in check_rules_access.EXPECTED_RESULT_COLUMNS
    )
    return FunctionFacts(
        name=check_rules_access.RULE_FUNCTION_NAME,
        is_security_definer=True,
        config=("search_path=pg_catalog, public",),
        arguments="p_product text, p_market_stream_key text",
        result=f"TABLE({columnas})",
        body="SELECT r.rule_id FROM rule_definition r WHERE r.status = 'active'",
        execute_for_public=False,
        execute_for_app=False,
        execute_for_ingestion=False,
        execute_for_operator=False,
    )


def _outbox() -> dict[str, str]:
    return dict.fromkeys(check_rules_access._RULES_OUTBOX_POLICIES, _OUTBOX_OK)  # noqa: SLF001


def _check(privileges: dict[tuple[str, str, str], bool] | None = None) -> list[str]:
    return check_rules(
        _funcion(),
        True,  # el motor SI tiene EXECUTE sobre su ventanilla.
        {(RULES, "market_candle", "SELECT"): True} | (privileges or {}),
        _outbox(),
    )


def test_el_caso_conforme_no_produce_violaciones() -> None:
    # Sin falsos rojos: con la rendija de lectura de velas y nada mas, verde.
    assert _check() == []


def test_sin_select_sobre_market_candle_el_motor_no_puede_evaluar() -> None:
    # El POSITIVO de D1: si el grant desaparece en un refactor, el motor se queda mudo
    # en produccion. Aqui rompe el build.
    problems = check_rules(_funcion(), True, {}, _outbox())
    assert any("NO tiene SELECT" in p for p in problems)


def test_el_motor_no_escribe_el_libro_l2() -> None:
    # CSA item 22: el motor de reglas LEE mercado, no lo ingiere. Si pudiera insertar un
    # snapshot del libro o una discontinuidad, estaria fabricando el dato de orderflow
    # que alimenta sus PROPIAS reglas -- y, en M5, sus ordenes.
    for tabla in check_rules_access.MARKET_ORDERBOOK_TABLES:
        for privilegio in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            problems = _check({(RULES, tabla, privilegio): True})
            assert any(tabla in p for p in problems), (tabla, privilegio)
            assert any("no ingiere market data" in p for p in problems), (
                tabla,
                privilegio,
            )


def test_el_motor_no_escribe_el_historico_de_velas() -> None:
    problems = _check({(RULES, "market_candle", "INSERT"): True})
    assert any("no lo escribe" in p for p in problems)


def test_el_motor_no_toca_la_autoria_fila_a_fila() -> None:
    problems = _check({(RULES, "rule_definition", "SELECT"): True})
    assert any("su UNICO acceso a rule_definition" in p for p in problems)
