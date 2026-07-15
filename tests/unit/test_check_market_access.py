"""Unit tests de la logica pura del check market (regla 5.20, CA-P07-D/G).

Construyen FunctionFacts y los mapas de privilegios/policies a mano y ejercitan
check_market SIN PostgreSQL: un test por control que demuestra que la violacion se
detecta, mas un caso verde que demuestra que no hay falsos rojos.

Las pruebas contra el MOTOR real (que PostgreSQL RECHAZA de verdad) viven en
tests/integration/test_market_access.py: aqui solo se prueba la logica del check.
"""

from __future__ import annotations

from dataclasses import replace

import check_market_access
from check_identity_access import FunctionFacts

_VENTANILLA = "market_public_demand"

_OUTBOX_OK = (
    "(event_type = ANY (ARRAY['market.candle_updated'::text, "
    "'market.candle_closed'::text, 'market.candle_corrected'::text]))"
)


def _funcion(**overrides: object) -> FunctionFacts:
    """La ventanilla CONFORME; cada test sobreescribe el campo que quiere romper."""
    base = FunctionFacts(
        name=_VENTANILLA,
        is_security_definer=True,
        config=("search_path=pg_catalog, public",),
        arguments="",
        result="TABLE(out_market_stream_key text, out_intent_count bigint)",
        body=(
            "SELECT i.market_stream_key, count(*) FROM market_subscription_intent i "
            "WHERE i.stream_scope = 'public_market' GROUP BY i.market_stream_key"
        ),
        execute_for_public=False,
        execute_for_app=False,
        execute_for_ingestion=True,
        execute_for_operator=False,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def _outbox(**overrides: str) -> dict[str, str]:
    policies = dict.fromkeys(check_market_access._INGESTION_OUTBOX_POLICIES, _OUTBOX_OK)
    policies.update(overrides)
    return policies


def _rls_ok() -> dict[str, tuple[bool, bool]]:
    return {"market_subscription_intent": (True, True)}


def _check(
    function: FunctionFacts | None = None,
    privileges: dict[tuple[str, str, str], bool] | None = None,
    outbox: dict[str, str] | None = None,
    rls: dict[str, tuple[bool, bool]] | None = None,
) -> list[str]:
    return check_market_access.check_market(
        {_VENTANILLA: _funcion() if function is None else function},
        {} if privileges is None else privileges,
        _outbox() if outbox is None else outbox,
        _rls_ok() if rls is None else rls,
    )


def test_ventanilla_conforme_no_produce_violaciones() -> None:
    # Sin falsos rojos: el caso bueno pasa limpio.
    assert _check() == []


class TestVentanillaCiega:
    def test_p8_columna_de_mas_en_el_retorno_es_violacion(self) -> None:
        # P8: la ventanilla solo puede revelar CUANTOS piden un stream, jamas QUIENES.
        for columna, tipo in (
            ("out_tenant_id", "uuid"),
            ("out_user_id", "uuid"),
            ("out_intent_id", "uuid"),
            ("out_source_ref", "text"),
        ):
            fn = _funcion(
                result=(
                    "TABLE(out_market_stream_key text, out_intent_count bigint, "
                    f"{columna} {tipo})"
                )
            )
            violations = _check(fn)
            assert any("columnas de salida" in v for v in violations), columna
            assert any("QUIEN" in v for v in violations), columna

    def test_p11_ventanilla_con_parametros_es_violacion(self) -> None:
        # P11: una ventanilla SIN parametros no puede ser interrogada por tenant. Con
        # parametros, alguien podria pedirle "dime los intereses del tenant X".
        fn = _funcion(arguments="p_tenant_id uuid")
        violations = _check(fn)
        assert any("ACEPTA PARAMETROS" in v for v in violations)
        assert any("interrogada" in v for v in violations)

    def test_sin_security_definer_es_violacion(self) -> None:
        violations = _check(_funcion(is_security_definer=False))
        assert any("SECURITY DEFINER" in v for v in violations)

    def test_sin_search_path_fijado_es_violacion(self) -> None:
        violations = _check(_funcion(config=()))
        assert any("search_path" in v for v in violations)

    def test_con_sql_dinamico_es_violacion(self) -> None:
        fn = _funcion(body="EXECUTE format('SELECT * FROM %I', p_tabla)")
        violations = _check(fn)
        assert any("SQL dinamico" in v for v in violations)

    def test_execute_para_public_es_violacion(self) -> None:
        violations = _check(_funcion(execute_for_public=True))
        assert any("PUBLIC" in v for v in violations)

    def test_sin_execute_para_el_ingestor_es_violacion(self) -> None:
        violations = _check(_funcion(execute_for_ingestion=False))
        assert any("no tiene EXECUTE" in v for v in violations)

    def test_execute_para_la_api_es_violacion(self) -> None:
        # Nadie mas necesita esta ventanilla: la API no agrega demanda cross-tenant.
        violations = _check(_funcion(execute_for_app=True))
        assert any("ce_v5_app" in v for v in violations)

    def test_ventanilla_ausente_es_violacion(self) -> None:
        violations = check_market_access.check_market({}, {}, _outbox(), _rls_ok())
        assert any("no existe" in v for v in violations)


class TestPrivilegios520:
    def test_la_api_no_puede_escribir_velas(self) -> None:
        # La mitad (a) de la prueba bidireccional: la API esta expuesta a internet.
        for privilege in ("INSERT", "UPDATE", "DELETE"):
            violations = _check(
                privileges={("ce_v5_app", "market_candle", privilege): True}
            )
            assert any("regla 5.20" in v for v in violations), privilege
            assert any("FABRICAR" in v or "APPEND-ONLY" in v for v in violations)

    def test_la_api_no_puede_escribir_el_catalogo(self) -> None:
        violations = _check(
            privileges={("ce_v5_app", "market_instrument", "INSERT"): True}
        )
        assert any("market_instrument" in v for v in violations)

    def test_historico_append_only_para_todos_incluido_el_ingestor(self) -> None:
        # Nadie reescribe la historia del mercado, ni siquiera quien la escribe.
        for role in ("ce_v5_app", "ce_v5_ingestion", "ce_v5_operator"):
            for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                violations = _check(
                    privileges={(role, "market_candle", privilege): True}
                )
                assert any("APPEND-ONLY" in v for v in violations), (role, privilege)

    def test_el_ingestor_no_lee_la_demanda_fila_a_fila(self) -> None:
        # Su UNICO acceso a la demanda es la ventanilla agregada: si pudiera hacer
        # SELECT sobre la tabla base, sabria QUIEN pide que.
        violations = _check(
            privileges={
                ("ce_v5_ingestion", "market_subscription_intent", "SELECT"): True
            }
        )
        assert any("ventanilla" in v for v in violations)

    def test_el_ingestor_no_toca_politica_ni_auditoria(self) -> None:
        # La mitad (b) de la prueba bidireccional.
        for table in (
            "kill_switch",
            "policy_rule",
            "operator_audit",
            "sensitive_action_audit",
        ):
            violations = _check(privileges={("ce_v5_ingestion", table, "SELECT"): True})
            assert any(table in v for v in violations), table
            assert any("no toca politica ni auditoria" in v for v in violations)

    def test_el_operador_no_toca_market_data(self) -> None:
        violations = _check(
            privileges={("ce_v5_operator", "market_candle", "SELECT"): True}
        )
        assert any("ce_v5_operator" in v for v in violations)


class TestOutboxAcotadaPorElMotor:
    def test_policy_que_menciona_otra_familia_es_violacion(self) -> None:
        # Un ingestor comprometido no puede fabricar un execution.* falso.
        for prohibido in ("execution.order_placed", "policy.kill_switch_activated"):
            outbox = _outbox(
                outbox_ingestion_insert=(
                    "(event_type = ANY (ARRAY['market.candle_updated'::text, "
                    "'market.candle_closed'::text, 'market.candle_corrected'::text, "
                    f"'{prohibido}'::text]))"
                )
            )
            violations = _check(outbox=outbox)
            assert any("no puede fabricar" in v for v in violations), prohibido

    def test_policy_que_no_acota_a_los_tres_market_es_violacion(self) -> None:
        outbox = _outbox(outbox_ingestion_insert="true")
        violations = _check(outbox=outbox)
        assert any("no menciona" in v for v in violations)

    def test_policy_ausente_es_violacion(self) -> None:
        outbox = _outbox()
        del outbox["outbox_ingestion_insert"]
        violations = _check(outbox=outbox)
        assert any("falta la policy" in v for v in violations)

    def test_ingestor_con_delete_en_outbox_es_violacion(self) -> None:
        violations = _check(privileges={("ce_v5_ingestion", "outbox", "DELETE"): True})
        assert any("no borra la outbox" in v for v in violations)


class TestRlsDeLaTablaBase:
    def test_sin_force_rls_la_excepcion_no_se_sostiene(self) -> None:
        violations = _check(rls={"market_subscription_intent": (True, False)})
        assert any("CA-P07-G" in v for v in violations)
        assert any("la excepcion no vale nada" in v for v in violations)
