"""Tests de la logica pura del check rules (P08, CA-P08-03 / D1). Sin PostgreSQL.

El check ya existia y el CI lo corre; lo que no existia era esta suite en frio. Se
anade con la remediacion G2 de P07c porque la superficie que se acaba de cerrar -- el
motor de reglas no ESCRIBE el libro L2 (CSA item 22) -- necesitaba una prueba que
mordiera sin base de datos: si manana alguien concede un INSERT sobre
market_orderbook_snapshot al rol de reglas, esto se pone rojo aqui, no en produccion.

Las pruebas contra el MOTOR real viven en tests/integration/test_rules_access.py.
"""

from __future__ import annotations

import pytest

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


def _conforme() -> dict[tuple[str, str, str], bool]:
    """La linea base CONFORME: exactamente los privilegios que la postura autoriza.

    Dos rendijas de LECTURA de mercado (velas 0016, footprint 0021) y, sobre el estado
    PROPIO del motor (cvd_snapshot, 0022), lectura Y escritura acotada a INSERT.
    """
    conforme = {
        (RULES, "market_candle", "SELECT"): True,
        (RULES, "market_footprint", "SELECT"): True,
    }
    for tabla in check_rules_access.RULES_STATE_TABLES:
        conforme[(RULES, tabla, "SELECT")] = True
        conforme[(RULES, tabla, "INSERT")] = True
    return conforme


def _check(privileges: dict[tuple[str, str, str], bool] | None = None) -> list[str]:
    return check_rules(
        _funcion(),
        True,  # el motor SI tiene EXECUTE sobre su ventanilla.
        _conforme() | (privileges or {}),
        _outbox(),
    )


def test_el_caso_conforme_no_produce_violaciones() -> None:
    # Sin falsos rojos: con las dos rendijas de lectura de mercado (0016, 0021) y el
    # SELECT+INSERT sobre el estado propio del motor (0022) -- y nada mas -- verde.
    assert _check() == []


def test_sin_select_sobre_market_candle_el_motor_no_puede_evaluar() -> None:
    # El POSITIVO de D1: si el grant desaparece en un refactor, el motor se queda mudo
    # en produccion. Aqui rompe el build.
    problems = check_rules(_funcion(), True, {}, _outbox())
    assert any("NO tiene SELECT" in p for p in problems)


def test_sin_select_sobre_market_footprint_el_motor_no_puede_materializar() -> None:
    # El POSITIVO de footprint (P08c CE-14, 0021): la materializacion de
    # vp.*/orderflow/cvd lee la VENTANA de footprints; si ese grant desaparece, el motor
    # no puede materializar. Se deja el SELECT de velas puesto a proposito, para que lo
    # que muerda sea el positivo de FOOTPRINT y no el de market_candle.
    problems = check_rules(
        _funcion(), True, {(RULES, "market_candle", "SELECT"): True}, _outbox()
    )
    assert any(
        p.startswith(f"{check_rules_access.MARKET_FOOTPRINT_TABLE}: ")
        and "NO tiene SELECT" in p
        for p in problems
    )


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


# --- Estado PROPIO del motor (cvd_snapshot, 0022 / MAT-07) --------------------
# La unica familia donde el motor ESCRIBE algo que no es su outbox. Los dos sentidos:
# sin SELECT+INSERT no puede hacer replay del CVD; con UPDATE/DELETE/TRUNCATE podria
# reescribir un ancla ya tomada y el replay dejaria de ser reproducible.


@pytest.mark.parametrize("privilegio", ["SELECT", "INSERT"])
def test_sin_los_privilegios_de_su_estado_el_motor_no_puede_replay(
    privilegio: str,
) -> None:
    # POSITIVO: se quita UNO de los dos privilegios necesarios y el check debe morder
    # nombrando la tabla. Si un refactor se llevara el grant de la 0022, el motor
    # recorreria la historia entera en cada tick (o fallaria) en produccion; aqui rompe
    # el build.
    for tabla in check_rules_access.RULES_STATE_TABLES:
        problems = _check({(RULES, tabla, privilegio): False})
        assert any(
            p.startswith(f"{tabla}: ") and f"NO tiene {privilegio}" in p
            for p in problems
        ), (tabla, privilegio)


@pytest.mark.parametrize("privilegio", ["UPDATE", "DELETE", "TRUNCATE"])
def test_el_motor_no_muta_ni_borra_su_estado_de_replay(privilegio: str) -> None:
    # NEGATIVO: el estado de replay es APPEND-ONLY. Una correccion es un snapshot NUEVO
    # en su barra, no una mutacion: si el motor pudiera reescribir el ancla, el mismo
    # tick daria valores distintos segun quien la hubiera tocado (ADR-007).
    for tabla in check_rules_access.RULES_STATE_TABLES:
        problems = _check({(RULES, tabla, privilegio): True})
        assert any(
            p.startswith(f"{tabla}: ") and f"tiene {privilegio}" in p for p in problems
        ), (tabla, privilegio)
        assert any("APPEND-ONLY" in p for p in problems), (tabla, privilegio)


def test_el_insert_de_su_estado_no_se_confunde_con_escribir_mercado() -> None:
    # La frontera que separa las dos categorias: que el motor pueda INSERTAR en su
    # estado propio NO le da ningun poder sobre el mercado. El caso conforme (que YA
    # lleva el INSERT de cvd_snapshot) no produce ni una violacion de mercado.
    assert _check() == []
    problems = _check({(RULES, "market_footprint", "INSERT"): True})
    assert any("no lo escribe" in p for p in problems)
