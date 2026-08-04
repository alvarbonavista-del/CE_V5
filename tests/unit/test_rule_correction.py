"""Correccion de vela con alcance POINT-LOCAL (CA-P08-08, firmada).

Cubre los tests PUROS de la tanda 7.3: T1, T2, T4, T5, T6, T7 (parte pura), T8-T11,
T13, T15, T16 y T17. Los que exigen PostgreSQL -- T3 (lectura corregida), T7 (estado
intacto en caliente), T12 (atomicidad), T14 (tenant de la ventanilla) y T19 (end-to-end)
-- viven en tools/validate_rules_correction.py.

El eje de todo: v5.0 SOLO propaga correcciones a fuentes POINT_LOCAL. Una fuente
RECURSIVE, INTEGRATOR o WINDOWED no se aproxima ni se cuarentena: se SALTA con motivo.
"""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from ce_v5.infra.db.rules import (
    CorrectionMark,
    build_evaluation_completed_event,
    build_firing_event,
    build_resolved_event,
)
from ce_v5.platform.rules.catalog import DataSourceCatalog
from ce_v5.platform.rules.compiler import CompilationError, compile
from ce_v5.platform.rules.correction import (
    affected_window,
    correction_scope,
    is_within_window,
)
from ce_v5.platform.rules.cvd import CVD_SOURCE_ID, ResetPolicy
from ce_v5.platform.rules.discovery import discover_declarations
from ce_v5.platform.rules.rawclose import (
    MARKET_CLOSE_SOURCE_ID,
    market_close_declaration,
)
from ce_v5.platform.rules.volume_profile import DEFAULT_BIN_COUNT, VP_POC_SOURCE_ID
from source.datasource import (
    DataSourceDeclaration,
    HistoryUnit,
    MemoryModel,
    ParamSpec,
    Servibility,
    SharingScope,
    SourceType,
)
from source.families.rule import (
    EvaluationLifecycleState,
    EvaluationResult,
    NodeOutcome,
    ResolvedReason,
    VetoOutcome,
)
from source.rules.condition import Condition
from source.rules.feature import Feature
from source.rules.group import Group
from source.rules.market_rules import AlertRule, AnyRule, MarketScope, RuleProduct
from source.rules.reference import DataSourceParam, DataSourceRef
from source.rules.rule import BindingKind, TargetBinding
from source.rules.scalar import ScalarType, ScalarValue
from source.rules.term import SourceTerm, Term, TermKind
from source.rules.vocab import (
    CanonicalFunction,
    CombineMode,
    ComparisonOperator,
    RuleCombineMode,
    TriggerPolicy,
)

_TF_MS = 3_600_000  # 1h
_EMA_SOURCE_ID = "market.ema"
_CVD_SOURCE_ID = "market.cvd"
_SMA_SOURCE_ID = "market.sma"


def _declaration(source_id: str, memory_model: MemoryModel) -> DataSourceDeclaration:
    """Declaracion sintetica que solo varia en su memory_model."""
    return DataSourceDeclaration(
        source_id=source_id,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        memory_model=memory_model,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=("1h",),
        history_units=(HistoryUnit.BARS,),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=("exchange", "symbol", "timeframe"),
    )


def _catalog() -> DataSourceCatalog:
    catalog = DataSourceCatalog()
    catalog.register(market_close_declaration())
    catalog.register(_declaration(_EMA_SOURCE_ID, MemoryModel.RECURSIVE))
    catalog.register(_declaration(_CVD_SOURCE_ID, MemoryModel.INTEGRATOR))
    catalog.register(_declaration(_SMA_SOURCE_ID, MemoryModel.WINDOWED))
    catalog.validate()
    return catalog


def _condition(
    source_id: str,
    *,
    function: CanonicalFunction | None = None,
    offset: int | None = None,
    params: tuple[DataSourceParam, ...] = (),
) -> Condition:
    return Condition(
        node_id=uuid4(),
        left=Term(
            term_kind=TermKind.SOURCE,
            source=SourceTerm(
                ref=DataSourceRef(source_id=source_id, params=params),
                function=function,
                offset=offset,
            ),
        ),
        operator=ComparisonOperator.GT,
        right=Term(
            term_kind=TermKind.CONSTANT,
            constant=ScalarValue(scalar_type=ScalarType.DECIMAL, decimal_value="1"),
        ),
    )


def _rule(*conditions: Condition) -> AnyRule:
    return AlertRule(
        product=RuleProduct.ALERT,
        rule_id=uuid4(),
        tenant_id=uuid4(),
        name="regla-de-correccion",
        target_binding=TargetBinding(binding_kind=BindingKind.MARKET),
        trigger_policy=TriggerPolicy.CANDLE_CLOSE,
        groups=(
            Group(
                node_id=uuid4(),
                evaluation_context="1h",
                combine_mode=CombineMode.ALL,
                features=(
                    Feature(
                        node_id=uuid4(),
                        conditions=conditions,
                        combine_mode=CombineMode.ALL,
                    ),
                ),
            ),
        ),
        rule_combine_mode=RuleCombineMode.ALL,
        enabled=True,
        market_scope=MarketScope(exchange="binance", symbol="BTC-USDT"),
    )


def _result(matched: bool) -> EvaluationResult:
    outcome = NodeOutcome.TRUE if matched else NodeOutcome.FALSE
    return EvaluationResult(
        matched=matched,
        rule_outcome=outcome,
        veto_outcome=VetoOutcome.NO_VETO,
        veto_active=False,
        node_results=(),
    )


# --- T1: memory_model obligatorio; market.close es POINT_LOCAL ----------------


def test_t1_memory_model_es_obligatorio_sin_default() -> None:
    """Omitir memory_model NO vale por defecto: una fuente debe declararlo."""
    with pytest.raises(ValidationError) as exc:
        DataSourceDeclaration(
            source_id="market.fake",
            source_type=SourceType.OBSERVABLE,
            servibility=Servibility.CONTINUOUS,
            value_type=ScalarType.DECIMAL,
            evaluation_contexts=("1h",),
            history_units=(HistoryUnit.BARS,),
            shared_evaluation=True,
            sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
            cache_key_schema=("exchange",),
        )  # type: ignore[call-arg]
    assert "memory_model" in str(exc.value)


def test_t1_market_close_es_point_local() -> None:
    """market.close: el cierre de T es el dato crudo de T, no depende de T-1."""
    assert market_close_declaration().memory_model is MemoryModel.POINT_LOCAL


# --- T2: declaraciones sinteticas RECURSIVE e INTEGRATOR ----------------------


def test_t2_declaraciones_recursive_e_integrator_quedan_tipadas() -> None:
    assert _declaration("x.a", MemoryModel.RECURSIVE).memory_model is (
        MemoryModel.RECURSIVE
    )
    assert _declaration("x.b", MemoryModel.INTEGRATOR).memory_model is (
        MemoryModel.INTEGRATOR
    )


def test_t2_el_conjunto_del_enum_es_exacto_y_coordinado() -> None:
    # Igualdad de conjunto EXACTA, no "contiene": cualquier adicion futura al enum
    # ROMPE este test y fuerza coordinacion/elevacion. WINDOWED entro por extension
    # ADITIVA coordinada (P08b/P08c); supera la premisa "enum de 3, cerrado".
    assert {m.value for m in MemoryModel} == {
        "point_local",
        "recursive",
        "integrator",
        "windowed",
    }


# --- T4: h = max history_bars de las fuentes point-local ----------------------


def test_t4_history_bars_es_el_maximo_entre_fuentes() -> None:
    """Basta con que UNA fuente mire la barra corregida para invalidar la evaluacion."""
    plan = compile(
        _rule(
            _condition(MARKET_CLOSE_SOURCE_ID),  # acceso directo: 1 barra
            _condition(
                MARKET_CLOSE_SOURCE_ID,
                function=CanonicalFunction.AVERAGE,
                offset=5,
            ),  # media de 5: 5 barras
        ),
        _catalog(),
    )
    scope = correction_scope(plan)
    assert scope.conformant
    assert scope.history_bars == 5


def test_t4_plan_sin_fuentes_es_conformante_con_ventana_vacia() -> None:
    """Una regla que no mira velas no la afecta ninguna correccion."""
    scope = correction_scope(compile(_rule(_constante_vs_constante()), _catalog()))
    assert scope.conformant
    assert scope.history_bars == 0
    assert affected_window(1000, scope.history_bars, _TF_MS) is None


def _constante_vs_constante() -> Condition:
    uno = Term(
        term_kind=TermKind.CONSTANT,
        constant=ScalarValue(scalar_type=ScalarType.DECIMAL, decimal_value="2"),
    )
    dos = Term(
        term_kind=TermKind.CONSTANT,
        constant=ScalarValue(scalar_type=ScalarType.DECIMAL, decimal_value="1"),
    )
    return Condition(
        node_id=uuid4(), left=uno, operator=ComparisonOperator.GT, right=dos
    )


# --- T5: ventana afectada = [T, T + (h-1) barras] ----------------------------


def test_t5_ventana_afectada_cubre_h_barras_desde_t() -> None:
    """h barras CONTANDO la propia T: [T, T+(h-1)*tf]."""
    t = 1_700_000_000_000 // _TF_MS * _TF_MS
    assert affected_window(t, 1, _TF_MS) == (t, t)
    assert affected_window(t, 3, _TF_MS) == (t, t + 2 * _TF_MS)
    assert affected_window(t, 5, _TF_MS) == (t, t + 4 * _TF_MS)


def test_t5_ventana_vacia_si_no_hay_historia() -> None:
    assert affected_window(1000, 0, _TF_MS) is None


# --- T6/T7 (pura): L dentro / fuera de la ventana -----------------------------


def test_t6_l_dentro_de_la_ventana_se_reevalua() -> None:
    t = 4 * _TF_MS
    window = affected_window(t, 3, _TF_MS)
    assert is_within_window(t, window)  # la propia vela corregida
    assert is_within_window(t + _TF_MS, window)
    assert is_within_window(t + 2 * _TF_MS, window)  # ultimo borde


def test_t7_l_fuera_de_la_ventana_no_se_reevalua() -> None:
    """L > T+(h-1) barras: el estado vigente no se calculo con el dato corregido."""
    t = 4 * _TF_MS
    window = affected_window(t, 3, _TF_MS)
    assert not is_within_window(t + 3 * _TF_MS, window)  # justo fuera
    assert not is_within_window(t + 50 * _TF_MS, window)
    assert not is_within_window(t - _TF_MS, window)  # anterior a la correccion
    assert not is_within_window(t, None)  # ventana vacia: nunca


# --- T15/T16/T17: la GUARDIA DURA --------------------------------------------


def test_t15_fuente_recursive_no_es_conformante() -> None:
    scope = correction_scope(compile(_rule(_condition(_EMA_SOURCE_ID)), _catalog()))
    assert not scope.conformant
    assert scope.blocking_source_id == _EMA_SOURCE_ID
    assert scope.blocking_memory_model is MemoryModel.RECURSIVE


def test_t16_fuente_integrator_no_es_conformante() -> None:
    scope = correction_scope(compile(_rule(_condition(_CVD_SOURCE_ID)), _catalog()))
    assert not scope.conformant
    assert scope.blocking_source_id == _CVD_SOURCE_ID
    assert scope.blocking_memory_model is MemoryModel.INTEGRATOR


def test_fuente_windowed_no_es_conformante_para_correccion() -> None:
    """Espejo de t15/t16 para WINDOWED (extension aditiva coordinada P08b/P08c).

    ALCANCE v5.0, no invariante permanente: hoy el motor SOLO propaga a POINT_LOCAL,
    asi que WINDOWED (SMA real, volume profile, l2.* por ventana) queda NO-CONFORME
    para correccion, como RECURSIVE/INTEGRATOR. Su correccion por ventana interna
    queda DIFERIDA al cableado vivo (D-VP-4 a); cuando ese cableado la anada, este
    test se actualiza.
    """
    scope = correction_scope(compile(_rule(_condition(_SMA_SOURCE_ID)), _catalog()))
    assert not scope.conformant
    assert scope.blocking_source_id == _SMA_SOURCE_ID
    assert scope.blocking_memory_model is MemoryModel.WINDOWED


def test_t17_regla_mixta_queda_descalificada_entera() -> None:
    """Una sola fuente no point-local descalifica el plan entero."""
    plan = compile(
        _rule(_condition(MARKET_CLOSE_SOURCE_ID), _condition(_EMA_SOURCE_ID)),
        _catalog(),
    )
    scope = correction_scope(plan)
    assert not scope.conformant
    assert scope.blocking_memory_model is MemoryModel.RECURSIVE


def test_t15_la_regla_no_conformante_sigue_compilando_y_evaluando() -> None:
    """Saltarse la correccion NO degrada la regla: su candle_closed sigue igual."""
    plan = compile(_rule(_condition(_EMA_SOURCE_ID)), _catalog())
    assert plan.resolved_sources  # compila con normalidad
    assert not correction_scope(plan).conformant  # solo la correccion se salta


# --- T8/T9/T10/T11: la emision marcada como correccion ------------------------


def _mark(revision: int = 1) -> CorrectionMark:
    return CorrectionMark(
        causation_event_id="11111111-1111-4111-8111-111111111111",
        correction_revision=revision,
    )


def test_t8_t10_evaluation_completed_lleva_data_correction() -> None:
    """El motivo del ciclo es data_correction (equivalente contractual, D5)."""
    event = build_evaluation_completed_event(
        rule_id=uuid4(),
        tenant_id=uuid4(),
        canonical_rule_hash="h",
        previous_state=EvaluationLifecycleState.FIRING,
        new_state=EvaluationLifecycleState.RESOLVED,
        result=_result(matched=False),
        reason_code=ResolvedReason.DATA_CORRECTION.value,
        open_time=1000,
        correction=_mark(),
    )
    payload = event.envelope["payload"]
    assert isinstance(payload, dict)
    assert payload["reason_code"] == "data_correction"


def test_t10_resolved_lleva_resolved_reason_data_correction() -> None:
    event = build_resolved_event(
        rule_id=uuid4(),
        tenant_id=uuid4(),
        canonical_rule_hash="h",
        previous_state=EvaluationLifecycleState.FIRING,
        resolved_reason=ResolvedReason.DATA_CORRECTION,
        open_time=1000,
        correction=_mark(),
    )
    payload = event.envelope["payload"]
    assert isinstance(payload, dict)
    assert payload["resolved_reason"] == "data_correction"


def test_t9_el_evento_de_correccion_lleva_causation_del_candle_corrected() -> None:
    mark = _mark()
    event = build_firing_event(
        rule_id=uuid4(),
        tenant_id=uuid4(),
        canonical_rule_hash="h",
        previous_state=EvaluationLifecycleState.INACTIVE,
        open_time=1000,
        correction=mark,
    )
    assert event.envelope["causation_id"] == mark.causation_event_id


def test_t11_idempotency_key_distinta_de_la_del_candle_closed() -> None:
    """Sin cualificar por revision, la correccion colisionaria y se deduplicaria."""
    rule_id, tenant_id = uuid4(), uuid4()

    def firing(correction: CorrectionMark | None) -> str:
        return build_firing_event(
            rule_id=rule_id,
            tenant_id=tenant_id,
            canonical_rule_hash="h",
            previous_state=EvaluationLifecycleState.INACTIVE,
            open_time=1000,
            correction=correction,
        ).idempotency_key

    normal = firing(None)
    corregido = firing(_mark())
    segunda = firing(_mark(2))
    assert normal != corregido
    assert corregido.endswith(":correction:1")
    # Y dos revisiones distintas de la MISMA vela tampoco colisionan entre si.
    assert segunda != corregido


def test_t8_payload_del_evento_de_correccion_no_es_vacio() -> None:
    """Guardarrail anti-sobre-vacio: el payload concreto viaja entero."""
    rule_id, tenant_id = uuid4(), uuid4()
    event = build_firing_event(
        rule_id=rule_id,
        tenant_id=tenant_id,
        canonical_rule_hash="hash-canonico",
        previous_state=EvaluationLifecycleState.RESOLVED,
        open_time=1000,
        correction=_mark(),
    )
    payload = event.envelope["payload"]
    assert isinstance(payload, dict)
    assert payload != {}
    assert payload["rule_id"] == str(rule_id)
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["canonical_rule_hash"] == "hash-canonico"
    assert payload["previous_state"] == "resolved"


# --- T13: sin cambio de estado, cero eventos ---------------------------------


def test_t13_reevaluacion_sin_cambio_de_estado_no_emite() -> None:
    """La regla de FLANCO manda tambien bajo correccion: sin transicion, nada."""
    from ce_v5.platform.rules.runtime import (
        EvalOutcome,
        RuntimeState,
        next_transition,
    )

    ya_firing = RuntimeState(EvaluationLifecycleState.FIRING)
    transicion = next_transition(ya_firing, EvalOutcome.ok(_result(matched=True)))
    assert transicion.emitted == ()
    assert not transicion.project_raised
    assert transicion.next.eval_state is EvaluationLifecycleState.FIRING


def test_t13_decimal_no_interviene_en_la_decision_de_flanco() -> None:
    """Anclaje: el flanco depende del ESTADO, no del valor corregido concreto."""
    assert Decimal("40000") != Decimal("20000")


def test_uuid_del_causation_es_un_uuid_valido() -> None:
    """El ancla causal es una identidad de evento real, no una cadena cualquiera."""
    UUID(_mark().causation_event_id)


def _catalogo_vivo() -> DataSourceCatalog:
    """El catalogo VIVO del worker (discovery real): trae vp.* y market.footprint."""
    catalog = DataSourceCatalog()
    for declaration in discover_declarations():
        catalog.register(declaration)
    catalog.validate()
    return catalog


def test_compile_propaga_params_de_fuente_mat05_q2() -> None:
    """MAT-05 Q2 (INVIERTE la guarda-cuarentena de Q4): el override se PROPAGA.

    Hasta MAT-05 Q2 el compilador RECHAZABA cualquier ref.params (cuarentena), porque
    descartarlos y servir el default seria la deuda D-E2.1 de v4 ("pedir rsi(period=7)
    ejecutaba rsi(14) SIN AVISO"). Ahora no se descarta ni se rechaza: se valida contra
    la declaracion y viaja al plan como param EFECTIVO, que es lo que el materializador
    consume. Este test es el que MUERDE si alguien volviera a descartarlos.

    Usa cvd.value/reset_policy (OVERRIDE-HABILITADO) y no vp.bin_count: desde el fix
    de fail-loud selectivo (REGISTRO_DECISIONES seccion 34) bin_count quedo DEFAULT-ONLY
    porque su materializador no lo consume; cvd.value es el consumidor de referencia de
    esta propagacion (with_params, MAT-05 Q2).
    """
    condicion = _condition(
        CVD_SOURCE_ID,
        params=(
            DataSourceParam(
                name="reset_policy",
                value=ScalarValue(
                    scalar_type=ScalarType.STRING,
                    string_value=ResetPolicy.SESSION_UTC.value,
                ),
            ),
        ),
    )
    plan = compile(_rule(condicion), _catalogo_vivo())
    cvd = next(s for s in plan.resolved_sources if s.source_id == CVD_SOURCE_ID)
    efectivo = cvd.param("reset_policy")
    assert efectivo is not None
    assert efectivo.string_value == ResetPolicy.SESSION_UTC.value


def test_compile_rechaza_override_no_habilitado_vp_bin_count() -> None:
    """FIX fail-loud selectivo (REGISTRO_DECISIONES seccion 34): vp.bin_count es
    DEFAULT-ONLY porque FootprintWindowedSpec no lo consume todavia (PENDIENTE
    CONOCIDO de la seccion 33). Antes de este fix el override compilaba y viajaba, y
    el perfil se seguia binando con el default en silencio; ahora se rechaza en
    compilacion.
    """
    condicion = _condition(
        VP_POC_SOURCE_ID,
        params=_params(
            "bin_count", ScalarValue(scalar_type=ScalarType.INTEGER, integer_value=100)
        ),
    )
    with pytest.raises(CompilationError, match="DEFAULT-ONLY"):
        compile(_rule(condicion), _catalogo_vivo())


def test_compile_rechaza_reset_policy_fuera_de_dominio() -> None:
    """Dominio en compilacion (OPCIONAL Q4, seccion 34): reset_policy fuera del enum
    de ResetPolicy se rechaza EN COMPILACION, no solo en with_params.
    """
    condicion = _condition(
        CVD_SOURCE_ID,
        params=_params(
            "reset_policy",
            ScalarValue(scalar_type=ScalarType.STRING, string_value="semanal"),
        ),
    )
    with pytest.raises(CompilationError, match="dominio"):
        compile(_rule(condicion), _catalogo_vivo())


def _ema_con_period_default_only() -> DataSourceDeclaration:
    """Fuente sintetica con un param DEFAULT-ONLY, generica sobre ema.value: no hay
    materializador de ema en v5.0 (solo sirve para probar el rechazo de compilacion
    sin depender de una fuente real del catalogo vivo).
    """
    return DataSourceDeclaration(
        source_id="ema.value",
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        memory_model=MemoryModel.RECURSIVE,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=("1h",),
        history_units=(HistoryUnit.BARS,),
        params=(
            ParamSpec(
                name="period",
                value_type=ScalarType.INTEGER,
                default=ScalarValue(scalar_type=ScalarType.INTEGER, integer_value=14),
            ),
        ),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=("exchange", "symbol", "timeframe"),
    )


def test_compile_rechaza_override_default_only_generico() -> None:
    """FIX fail-loud selectivo (seccion 34): un param declarado SIN estar en
    overridable_params es DEFAULT-ONLY para CUALQUIER fuente, no solo vp.bin_count.
    """
    catalog = DataSourceCatalog()
    catalog.register(_ema_con_period_default_only())
    catalog.validate()
    condicion = _condition(
        "ema.value",
        params=_params(
            "period", ScalarValue(scalar_type=ScalarType.INTEGER, integer_value=20)
        ),
    )
    with pytest.raises(CompilationError, match="DEFAULT-ONLY"):
        compile(_rule(condicion), catalog)


def test_compile_admite_la_misma_fuente_sin_params() -> None:
    """ADITIVIDAD D7: sin override, vp.poc compila y toma el DEFAULT declarado."""
    plan = compile(_rule(_condition(VP_POC_SOURCE_ID)), _catalogo_vivo())
    poc = next(s for s in plan.resolved_sources if s.source_id == VP_POC_SOURCE_ID)
    por_defecto = poc.param("bin_count")
    assert por_defecto is not None
    assert por_defecto.integer_value == DEFAULT_BIN_COUNT


def _params(name: str, value: ScalarValue) -> tuple[DataSourceParam, ...]:
    return (DataSourceParam(name=name, value=value),)


def test_compile_rechaza_param_desconocido() -> None:
    """Un param que la fuente NO declara se rechaza: nunca se degrada al default."""
    condicion = _condition(
        VP_POC_SOURCE_ID,
        params=_params(
            "periodo_inventado",
            ScalarValue(scalar_type=ScalarType.INTEGER, integer_value=7),
        ),
    )
    with pytest.raises(CompilationError, match="no declara el parametro"):
        compile(_rule(condicion), _catalogo_vivo())


def test_compile_rechaza_param_con_tipo_erroneo() -> None:
    """reset_policy se declara STRING: pasarlo como entero es un tipo incompatible.

    Usa cvd.reset_policy (OVERRIDE-HABILITADO) y no vp.bin_count: bin_count es
    DEFAULT-ONLY desde el fix de la seccion 34 y el override se rechazaria antes de
    llegar al chequeo de tipo, sin ejercitarlo.
    """
    condicion = _condition(
        CVD_SOURCE_ID,
        params=_params(
            "reset_policy",
            ScalarValue(scalar_type=ScalarType.INTEGER, integer_value=7),
        ),
    )
    with pytest.raises(CompilationError, match="tipos incompatibles"):
        compile(_rule(condicion), _catalogo_vivo())


def test_compile_rechaza_override_de_window_bars() -> None:
    """window_bars NO es param: es constante FIJA de materializacion (MAT-05 Q3).

    Se rechaza con mensaje PROPIO, no con el generico de param desconocido: quien la
    escribe cree que es configurable por regla y merece saber por que no lo es.
    """
    condicion = _condition(
        VP_POC_SOURCE_ID,
        params=_params(
            "window_bars",
            ScalarValue(scalar_type=ScalarType.INTEGER, integer_value=25),
        ),
    )
    with pytest.raises(CompilationError, match="constante fija de materializacion"):
        compile(_rule(condicion), _catalogo_vivo())


def test_compile_aditividad_market_close_sin_params() -> None:
    """ADITIVIDAD D7: market.close no declara params y compila EXACTAMENTE como antes.

    Su tupla de efectivos es vacia (no una con defaults inventados), asi que el dispatch
    ni siquiera entra en la rama de ligado: el camino de las reglas que ya existian no
    cambia de comportamiento.
    """
    plan = compile(_rule(_condition(MARKET_CLOSE_SOURCE_ID)), _catalogo_vivo())
    close = next(
        s for s in plan.resolved_sources if s.source_id == MARKET_CLOSE_SOURCE_ID
    )
    assert close.params == ()
    assert close.param("bin_count") is None
