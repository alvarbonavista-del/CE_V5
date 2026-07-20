"""Evaluador puro de tres valores (Kleene K3) del arbol de una regla (CA-P08-04 D1).

Codigo PURO de plataforma (sin DB, sin infra, sin reloj). Toma una Rule YA ADMITIDA (su
arbol grupos->features->condiciones y su veto guardian) y los datos ya materializados
(la serie de cierres por source_id, oldest->newest) y produce el EvaluationResult
granular. NO calcula transiciones de la FSM, ni dedup, ni proyeccion signal.*/alert.*:
eso es el runtime del Bloque 6/7. Aqui solo se resuelve la LOGICA de tres valores.

POR QUE Rule Y NO ExecutionPlan. El evaluador es NEUTRAL (no conoce mercado): opera
sobre el ARBOL booleano, que vive en la Rule, no en el plan. El ExecutionPlan (Bloque 5)
resuelve fuentes e historia para el fetch del runtime; no lleva el arbol. La logica pura
K3 depende del arbol y de los datos, asi que recibe la Rule (base neutral: acepta Alert
y TradingSignalRule por herencia) y un Mapping de series por source_id.

LOGICA DE TRES VALORES (CA-P08-04 D1, INFORME 6 sec 9.1). NOT_EVALUABLE por dato ausente
NO es FALSE. La combinacion es Kleene K3, igual en feature, grupo y regla:
- ALL:  FALSE si alguna FALSE; TRUE si todas TRUE; NOT_EVALUABLE en el resto.
- ANY:  TRUE si alguna TRUE; FALSE si todas FALSE; NOT_EVALUABLE en el resto.

VETO FAIL-SAFE (CA-P08-04 D1, vinculante). El veto (any_blocks) bloquea si su ANY es
TRUE *o* NOT_EVALUABLE: un veto que no se puede evaluar cuenta como ACTIVO (nunca se
deja pasar lo que no se sabe). La asimetria queda VISIBLE en el EvaluationResult:
veto_active, el NodeResult del veto con su outcome K3 crudo y su motivo, y un
diagnostico cuando bloquea por fail-safe (no por una condicion realmente TRUE).

RESULTADO K3 DE LA REGLA. matched = el arbol (SIN veto) dio TRUE. La distincion
FALSE vs NOT_EVALUABLE a nivel de regla -que el Bloque 6 necesita para decidir la
transicion (D2: NOT_EVALUABLE no transiciona)- se emite como diagnostico
"rule_outcome:<valor>", sin obligar al runtime a recomponer el arbol.

FRONTERAS DE CAPA. platform: importa solo de contracts (source.*) y de su misma capa
(platform.rules.functions); NUNCA de infra (check 7.1).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from ce_v5.platform.rules.functions import (
    FunctionValue,
    SporadicFunctionUnsupportedError,
    average,
    change,
    history_bars_needed,
    previous_value,
    value_at,
)
from source.families.rule import (
    EvaluationResult,
    NodeOutcome,
    NodeResult,
    VetoOutcome,
)
from source.rules.condition import Condition
from source.rules.feature import Feature
from source.rules.group import Group
from source.rules.rule import Rule
from source.rules.scalar import ScalarType, ScalarValue
from source.rules.term import SourceTerm, Term, TermKind
from source.rules.vocab import (
    CanonicalFunction,
    CombineMode,
    ComparisonOperator,
    RuleCombineMode,
)

# Codigos de diagnostico (ADR-016). Estables: el runtime los consume sin reparsear.
RULE_OUTCOME_PREFIX = "rule_outcome:"
DIAG_WINDOW_DEFERRED = "all_within_window:temporal_deferred_to_runtime"
DIAG_VETO_FAILSAFE = "veto_active:fail_safe_not_evaluable"

Series = Mapping[str, tuple[Decimal, ...]]


@dataclass(frozen=True, slots=True)
class _TermValue:
    """Valor de un termino: un Decimal, o NO EVALUABLE con su motivo."""

    evaluable: bool
    value: Decimal | None = None
    reason: str | None = None


def evaluate(rule: Rule, data: Series) -> EvaluationResult:
    """Evalua el arbol de una regla en logica K3 y devuelve el EvaluationResult.

    data mapea source_id -> serie de cierres (oldest->newest); en v5.0 market.close no
    tiene params, asi que la clave es el source_id. Determinista: mismos datos -> mismo
    resultado. No decide transicion ni proyeccion (Bloque 6/7).
    """
    node_results: list[NodeResult] = []
    group_outcomes = [_eval_group(group, data, node_results) for group in rule.groups]
    rule_outcome = _combine(_rule_mode(rule.rule_combine_mode), group_outcomes)

    diagnostics = [f"{RULE_OUTCOME_PREFIX}{rule_outcome.value}"]
    if rule.rule_combine_mode is RuleCombineMode.ALL_WITHIN_WINDOW:
        # 6.1 evalua el arbol booleano como ALL; la ventana temporal fina (que los
        # grupos disparen dentro de N) la aplica el runtime sobre el contexto firmado.
        diagnostics.append(DIAG_WINDOW_DEFERRED)

    veto_outcome = _eval_veto(rule, data, node_results, diagnostics)
    veto_active = veto_outcome in {VetoOutcome.TRUE, VetoOutcome.NOT_EVALUABLE}
    return EvaluationResult(
        matched=rule_outcome is NodeOutcome.TRUE,
        rule_outcome=rule_outcome,
        veto_outcome=veto_outcome,
        veto_active=veto_active,
        node_results=tuple(node_results),
        diagnostics=tuple(diagnostics),
    )


def _eval_veto(
    rule: Rule, data: Series, sink: list[NodeResult], diagnostics: list[str]
) -> VetoOutcome:
    """Veto guardian any_blocks con fail-safe (D1): NOT_EVALUABLE cuenta como ACTIVO.

    Devuelve el eje V de la tabla (CA-P08-05): NO_VETO si la regla no tiene veto, o el
    outcome K3 crudo del veto (TRUE/FALSE/NOT_EVALUABLE). El campo tipado es
    autoritativo; la conveniencia veto_active la deriva quien la necesite.
    """
    if rule.veto is None:
        return VetoOutcome.NO_VETO
    outcomes = []
    for condition in rule.veto.conditions:
        result = _eval_condition(condition, data)
        sink.append(result)
        outcomes.append(result.outcome)
    veto_k3 = _combine(CombineMode.ANY, outcomes)  # any_blocks == semantica ANY
    reason = None
    if veto_k3 is NodeOutcome.NOT_EVALUABLE:
        reason = "veto no evaluable -> se cuenta como ACTIVO (fail-safe D1)."
        diagnostics.append(DIAG_VETO_FAILSAFE)
    sink.append(
        NodeResult(
            node_id=rule.veto.node_id,
            outcome=veto_k3,
            observed=None,
            not_evaluable_reason=reason,
        )
    )
    # NodeOutcome y VetoOutcome comparten los tres valores true/false/not_evaluable.
    return VetoOutcome(veto_k3.value)


def _eval_group(group: Group, data: Series, sink: list[NodeResult]) -> NodeOutcome:
    """Combina las features del grupo por su combine_mode y anota el nodo del grupo."""
    feature_outcomes = [
        _eval_feature(feature, data, sink) for feature in group.features
    ]
    outcome = _combine(group.combine_mode, feature_outcomes)
    sink.append(_aggregate_node(group.node_id, outcome, "grupo", group.combine_mode))
    return outcome


def _eval_feature(
    feature: Feature, data: Series, sink: list[NodeResult]
) -> NodeOutcome:
    """Combina las condiciones de la feature por su combine_mode y anota su nodo."""
    condition_outcomes = []
    for condition in feature.conditions:
        result = _eval_condition(condition, data)
        sink.append(result)
        condition_outcomes.append(result.outcome)
    outcome = _combine(feature.combine_mode, condition_outcomes)
    sink.append(
        _aggregate_node(feature.node_id, outcome, "feature", feature.combine_mode)
    )
    return outcome


def _eval_condition(condition: Condition, data: Series) -> NodeResult:
    """Comparacion atomica K3: un termino no evaluable deja la condicion indecidible."""
    left = _eval_term(condition.left, data)
    right = _eval_term(condition.right, data)
    if not left.evaluable or not right.evaluable:
        parts = []
        if not left.evaluable:
            parts.append(f"izquierda: {left.reason}")
        if not right.evaluable:
            parts.append(f"derecha: {right.reason}")
        return NodeResult(
            node_id=condition.node_id,
            outcome=NodeOutcome.NOT_EVALUABLE,
            observed=None,
            not_evaluable_reason="; ".join(parts),
        )
    # value no es None cuando evaluable (invariante de _TermValue); mypy lo estrecha.
    assert left.value is not None
    assert right.value is not None
    holds = _compare(left.value, condition.operator, right.value)
    observed = f"{left.value} {condition.operator.value} {right.value}"
    return NodeResult(
        node_id=condition.node_id,
        outcome=NodeOutcome.TRUE if holds else NodeOutcome.FALSE,
        observed=observed,
    )


def _eval_term(term: Term, data: Series) -> _TermValue:
    """Un termino: constante (siempre evaluable) o acceso a fuente (puede faltar)."""
    if term.term_kind is TermKind.CONSTANT:
        assert term.constant is not None  # coherencia garantizada por el contrato Term
        return _TermValue(evaluable=True, value=_scalar_to_decimal(term.constant))
    assert term.source is not None
    return _eval_source(term.source, data)


def _eval_source(source: SourceTerm, data: Series) -> _TermValue:
    """Aplica la funcion del termino; NO EVALUABLE si falta serie o historia."""
    series = data.get(source.ref.source_id)
    if series is None:
        return _TermValue(
            evaluable=False,
            reason=f"dato ausente: no hay serie para {source.ref.source_id!r}",
        )
    result = _apply_function(source.function, source.offset, series)
    if not result.evaluable:
        needed = history_bars_needed(source.function, source.offset)
        return _TermValue(
            evaluable=False,
            reason=(
                f"historia insuficiente en {source.ref.source_id!r}: "
                f"{_function_label(source)} requiere {needed} barras, hay {len(series)}"
            ),
        )
    return _TermValue(evaluable=True, value=result.value)


def _apply_function(
    function: CanonicalFunction | None, offset: int | None, series: tuple[Decimal, ...]
) -> FunctionValue:
    """Despacha la funcion canonica continua sobre la serie (oldest->newest)."""
    if function is None:
        return value_at(series, 0)  # acceso directo = valor actual
    if offset is None:
        # Regla admitida garantiza offset para funciones con offset; fail-loud si no.
        msg = f"la funcion {function.value} exige offset y no lo tiene."
        raise ValueError(msg)
    if function is CanonicalFunction.VALUE_AT:
        return value_at(series, offset)
    if function is CanonicalFunction.PREVIOUS_VALUE:
        return previous_value(series, offset)
    if function is CanonicalFunction.AVERAGE:
        return average(series, offset)  # el offset del termino es el count de average
    if function is CanonicalFunction.CHANGE:
        return change(series, offset)
    # is_active / elapsed_since son esporadicas y no tienen fuente en v5.0 (Bloque 2).
    msg = f"funcion esporadica {function.value} sin fuente en v5.0."
    raise SporadicFunctionUnsupportedError(msg)


def _scalar_to_decimal(scalar: ScalarValue) -> Decimal:
    """Constante numerica -> Decimal (v5.0 compara Decimal vs Decimal)."""
    if scalar.scalar_type is ScalarType.DECIMAL:
        assert scalar.decimal_value is not None
        return scalar.decimal_value
    if scalar.scalar_type is ScalarType.INTEGER:
        assert scalar.integer_value is not None
        return Decimal(scalar.integer_value)
    # boolean/string: la comparacion de tipos rica esta diferida (el Bloque 3 impide
    # comparar close decimal con un no-numerico); fail-loud si llega.
    msg = (
        f"comparacion de tipo {scalar.scalar_type.value} no soportada en v5.0 "
        "(coherencia de tipos rica diferida)."
    )
    raise ValueError(msg)


_COMPARATORS: dict[ComparisonOperator, Callable[[Decimal, Decimal], bool]] = {
    ComparisonOperator.GT: lambda a, b: a > b,
    ComparisonOperator.GE: lambda a, b: a >= b,
    ComparisonOperator.LT: lambda a, b: a < b,
    ComparisonOperator.LE: lambda a, b: a <= b,
    ComparisonOperator.EQ: lambda a, b: a == b,
    ComparisonOperator.NE: lambda a, b: a != b,
}


def _compare(left: Decimal, operator: ComparisonOperator, right: Decimal) -> bool:
    """Aplica el operador de comparacion sobre dos Decimales (comparacion exacta)."""
    return _COMPARATORS[operator](left, right)


def _combine(mode: CombineMode, outcomes: Sequence[NodeOutcome]) -> NodeOutcome:
    """Combinacion Kleene K3 de un nivel (CA-P08-04 D1).

    ALL: FALSE si alguna FALSE; TRUE si todas TRUE; NOT_EVALUABLE en el resto.
    ANY: TRUE si alguna TRUE; FALSE si todas FALSE; NOT_EVALUABLE en el resto.
    """
    if mode is CombineMode.ALL:
        if any(o is NodeOutcome.FALSE for o in outcomes):
            return NodeOutcome.FALSE
        if all(o is NodeOutcome.TRUE for o in outcomes):
            return NodeOutcome.TRUE
        return NodeOutcome.NOT_EVALUABLE
    if any(o is NodeOutcome.TRUE for o in outcomes):
        return NodeOutcome.TRUE
    if all(o is NodeOutcome.FALSE for o in outcomes):
        return NodeOutcome.FALSE
    return NodeOutcome.NOT_EVALUABLE


def _rule_mode(mode: RuleCombineMode) -> CombineMode:
    """Proyecta el modo de regla al modo de nivel (ALL_WITHIN_WINDOW == ALL)."""
    return CombineMode.ANY if mode is RuleCombineMode.ANY else CombineMode.ALL


def _aggregate_node(
    node_id: UUID, outcome: NodeOutcome, kind: str, mode: CombineMode
) -> NodeResult:
    """NodeResult de un nodo de agregacion (feature/grupo): sin valor concreto."""
    reason = None
    if outcome is NodeOutcome.NOT_EVALUABLE:
        reason = f"{kind} {mode.value}: nodos hijos no evaluables no permiten decidir"
    return NodeResult(
        node_id=node_id,
        outcome=outcome,
        observed=None,
        not_evaluable_reason=reason,
    )


def _function_label(source: SourceTerm) -> str:
    """Etiqueta legible de la funcion de un termino para los motivos."""
    if source.function is None:
        return "acceso directo"
    if source.offset is None:
        return source.function.value
    return f"{source.function.value}({source.offset})"
