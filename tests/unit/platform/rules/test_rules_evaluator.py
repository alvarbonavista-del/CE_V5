"""Tests focalizados del evaluador K3 para el carrier ScalarValue (D1, P08b-D1-01/02/03,
dictamenes de la elevacion tecnica).

Cubre el camino CATEGORICO (STRING/BOOLEAN) EQ/NE feliz: antes de D1 era
ESTRUCTURALMENTE imposible que una fuente sirviera esto (Series era
Mapping[str, tuple[Decimal, ...]]), asi que este es codigo NUEVO y necesita su propio
candado para no quedar sin ejercitar (regla 5.11).

NO prueba mismatch de tipos aqui: el Bloque 3 lo rechaza en ADMISION (P08b-D1-03) y ese
camino quedo retirado del evaluador (era codigo muerto por construccion, ya que una
Rule que llega a evaluate() esta documentada como YA ADMITIDA). El candado de esa regla
vive en tests/unit/platform/rules/test_validator.py.
"""

from __future__ import annotations

from uuid import uuid4

from ce_v5.platform.rules.evaluator import evaluate
from source.rules.condition import Condition
from source.rules.feature import Feature
from source.rules.group import Group
from source.rules.market_rules import AlertRule, MarketScope, RuleProduct
from source.rules.reference import DataSourceRef
from source.rules.rule import BindingKind, TargetBinding
from source.rules.scalar import ScalarType, ScalarValue
from source.rules.term import SourceTerm, Term, TermKind
from source.rules.vocab import (
    CombineMode,
    ComparisonOperator,
    RuleCombineMode,
    TriggerPolicy,
)

_STRING_SOURCE_ID = "test.categorico_string"
_BOOLEAN_SOURCE_ID = "test.categorico_boolean"
_TF = "1h"


def _source_term(source_id: str) -> Term:
    return Term(
        term_kind=TermKind.SOURCE,
        source=SourceTerm(ref=DataSourceRef(source_id=source_id)),
    )


def _string_constant(value: str) -> Term:
    return Term(
        term_kind=TermKind.CONSTANT,
        constant=ScalarValue(scalar_type=ScalarType.STRING, string_value=value),
    )


def _boolean_constant(value: bool) -> Term:
    return Term(
        term_kind=TermKind.CONSTANT,
        constant=ScalarValue(scalar_type=ScalarType.BOOLEAN, boolean_value=value),
    )


def _condition(left: Term, operator: ComparisonOperator, right: Term) -> Condition:
    return Condition(node_id=uuid4(), left=left, operator=operator, right=right)


def _rule(condition: Condition) -> AlertRule:
    return AlertRule(
        product=RuleProduct.ALERT,
        rule_id=uuid4(),
        tenant_id=uuid4(),
        name="regla-categorica-de-prueba",
        target_binding=TargetBinding(binding_kind=BindingKind.MARKET),
        trigger_policy=TriggerPolicy.CANDLE_CLOSE,
        groups=(
            Group(
                node_id=uuid4(),
                evaluation_context=_TF,
                combine_mode=CombineMode.ALL,
                features=(
                    Feature(
                        node_id=uuid4(),
                        conditions=(condition,),
                        combine_mode=CombineMode.ALL,
                    ),
                ),
            ),
        ),
        rule_combine_mode=RuleCombineMode.ALL,
        enabled=True,
        market_scope=MarketScope(exchange="binance", symbol="BTC-USDT"),
    )


def _string_series(value: str) -> tuple[ScalarValue, ...]:
    return (ScalarValue(scalar_type=ScalarType.STRING, string_value=value),)


def _boolean_series(value: bool) -> tuple[ScalarValue, ...]:
    return (ScalarValue(scalar_type=ScalarType.BOOLEAN, boolean_value=value),)


class TestComparacionCategoricaEqNe:
    """El camino CATEGORICO vivo: EQ/NE entre una fuente y una constante del MISMO
    scalar_type. Es el unico camino que D1 anadio a _holds (rama STRING/BOOLEAN)."""

    def test_string_eq_coincide_da_true(self) -> None:
        condicion = _condition(
            _source_term(_STRING_SOURCE_ID),
            ComparisonOperator.EQ,
            _string_constant("above"),
        )
        resultado = evaluate(
            _rule(condicion), {_STRING_SOURCE_ID: _string_series("above")}
        )
        assert resultado.matched is True

    def test_string_eq_no_coincide_da_false(self) -> None:
        condicion = _condition(
            _source_term(_STRING_SOURCE_ID),
            ComparisonOperator.EQ,
            _string_constant("above"),
        )
        resultado = evaluate(
            _rule(condicion), {_STRING_SOURCE_ID: _string_series("below")}
        )
        assert resultado.matched is False

    def test_string_ne_es_la_negacion_exacta_de_eq(self) -> None:
        # Si NE se implementara mal (p.ej. copiando EQ por error), este test lo caza:
        # con el MISMO dato, EQ y NE tienen que discrepar siempre.
        serie = {_STRING_SOURCE_ID: _string_series("below")}
        eq = evaluate(
            _rule(
                _condition(
                    _source_term(_STRING_SOURCE_ID),
                    ComparisonOperator.EQ,
                    _string_constant("above"),
                )
            ),
            serie,
        )
        ne = evaluate(
            _rule(
                _condition(
                    _source_term(_STRING_SOURCE_ID),
                    ComparisonOperator.NE,
                    _string_constant("above"),
                )
            ),
            serie,
        )
        assert eq.matched is not ne.matched

    def test_boolean_eq_coincide_da_true(self) -> None:
        condicion = _condition(
            _source_term(_BOOLEAN_SOURCE_ID),
            ComparisonOperator.EQ,
            _boolean_constant(True),
        )
        resultado = evaluate(
            _rule(condicion), {_BOOLEAN_SOURCE_ID: _boolean_series(True)}
        )
        assert resultado.matched is True

    def test_boolean_ne_detecta_la_diferencia(self) -> None:
        condicion = _condition(
            _source_term(_BOOLEAN_SOURCE_ID),
            ComparisonOperator.NE,
            _boolean_constant(True),
        )
        resultado = evaluate(
            _rule(condicion), {_BOOLEAN_SOURCE_ID: _boolean_series(False)}
        )
        assert resultado.matched is True

    def test_boolean_ne_no_da_falso_positivo_si_coinciden(self) -> None:
        condicion = _condition(
            _source_term(_BOOLEAN_SOURCE_ID),
            ComparisonOperator.NE,
            _boolean_constant(True),
        )
        resultado = evaluate(
            _rule(condicion), {_BOOLEAN_SOURCE_ID: _boolean_series(True)}
        )
        assert resultado.matched is False
