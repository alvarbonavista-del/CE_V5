"""Tests focalizados de la coherencia de tipos del Bloque 3 (D1, dictamen P08b-D1-03).

OPCION 1 fail-loud: se rechaza en ADMISION, no en runtime. Dos hechos independientes:
(a) un operador de ORDEN (>,>=,<,<=) sobre un lado CATEGORICO (STRING/BOOLEAN); (b)
mezcla de value_type entre los dos lados de CUALQUIER operador, EQ/NE incluidos. Antes
de D1 esto era estructuralmente imposible de construir (solo existian fuentes DECIMAL),
asi que esta regla es codigo NUEVO y necesita su propio candado (regla 5.11).
"""

from __future__ import annotations

from uuid import uuid4

from ce_v5.platform.rules.catalog import DataSourceCatalog
from ce_v5.platform.rules.indicators.fib import (
    FIB_LEVELS_SOURCE_ID,
    fib_levels_declaration,
)
from ce_v5.platform.rules.validator import (
    CODE_OPERATOR_REQUIRES_NUMERIC,
    CODE_SOURCE_NOT_SERVIBLE,
    CODE_TERM_TYPE_MISMATCH,
    validate_rule,
)
from source.datasource import (
    DataSourceDeclaration,
    HistoryUnit,
    MemoryModel,
    Servibility,
    SharingScope,
    SourceType,
)
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
_DECIMAL_SOURCE_ID = "test.numerico_decimal"
_TF = "1h"


def _declaration(source_id: str, value_type: ScalarType) -> DataSourceDeclaration:
    """Declaracion sintetica que solo varia en su value_type (POINT_LOCAL,
    CONTINUOUS)."""
    return DataSourceDeclaration(
        source_id=source_id,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        memory_model=MemoryModel.POINT_LOCAL,
        value_type=value_type,
        evaluation_contexts=(_TF,),
        history_units=(HistoryUnit.BARS,),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=("exchange", "symbol", "timeframe"),
    )


def _catalog() -> DataSourceCatalog:
    catalog = DataSourceCatalog()
    catalog.register(_declaration(_STRING_SOURCE_ID, ScalarType.STRING))
    catalog.register(_declaration(_BOOLEAN_SOURCE_ID, ScalarType.BOOLEAN))
    catalog.register(_declaration(_DECIMAL_SOURCE_ID, ScalarType.DECIMAL))
    catalog.validate()
    return catalog


def _source_term(source_id: str) -> Term:
    return Term(
        term_kind=TermKind.SOURCE,
        source=SourceTerm(ref=DataSourceRef(source_id=source_id)),
    )


def _decimal_constant(value: str) -> Term:
    return Term(
        term_kind=TermKind.CONSTANT,
        constant=ScalarValue(scalar_type=ScalarType.DECIMAL, decimal_value=value),
    )


def _string_constant(value: str) -> Term:
    return Term(
        term_kind=TermKind.CONSTANT,
        constant=ScalarValue(scalar_type=ScalarType.STRING, string_value=value),
    )


def _rule(left: Term, operator: ComparisonOperator, right: Term) -> AlertRule:
    condition = Condition(node_id=uuid4(), left=left, operator=operator, right=right)
    return AlertRule(
        product=RuleProduct.ALERT,
        rule_id=uuid4(),
        tenant_id=uuid4(),
        name="regla-de-coherencia-de-tipos",
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


class TestOperadorDeOrdenSobreCategorico:
    """(a): GT/GE/LT/LE sobre un termino STRING/BOOLEAN se rechaza fail-loud."""

    def test_gt_sobre_string_se_rechaza(self) -> None:
        rule = _rule(
            _source_term(_STRING_SOURCE_ID),
            ComparisonOperator.GT,
            _string_constant("above"),
        )
        diagnostics = validate_rule(rule, _catalog())
        assert any(d.code == CODE_OPERATOR_REQUIRES_NUMERIC for d in diagnostics)

    def test_ge_le_lt_sobre_boolean_se_rechazan(self) -> None:
        boolean_constant = Term(
            term_kind=TermKind.CONSTANT,
            constant=ScalarValue(scalar_type=ScalarType.BOOLEAN, boolean_value=True),
        )
        for operator in (
            ComparisonOperator.GE,
            ComparisonOperator.LT,
            ComparisonOperator.LE,
        ):
            rule = _rule(_source_term(_BOOLEAN_SOURCE_ID), operator, boolean_constant)
            diagnostics = validate_rule(rule, _catalog())
            assert any(d.code == CODE_OPERATOR_REQUIRES_NUMERIC for d in diagnostics), (
                f"operador {operator.value} deberia rechazarse sobre BOOLEAN"
            )

    def test_gt_sobre_decimal_no_se_rechaza_por_esta_regla(self) -> None:
        # Control: el mismo operador SI vale sobre un termino numerico -- no es que GT
        # este prohibido en general, es que exige un value_type con orden.
        rule = _rule(
            _source_term(_DECIMAL_SOURCE_ID),
            ComparisonOperator.GT,
            _decimal_constant("100"),
        )
        diagnostics = validate_rule(rule, _catalog())
        assert not any(d.code == CODE_OPERATOR_REQUIRES_NUMERIC for d in diagnostics)


class TestMezclaDeTiposEntreLosDosLados:
    """(b): los DOS lados de CUALQUIER operador deben tener el MISMO value_type."""

    def test_eq_entre_string_y_constante_decimal_se_rechaza(self) -> None:
        rule = _rule(
            _source_term(_STRING_SOURCE_ID),
            ComparisonOperator.EQ,
            _decimal_constant("1"),
        )
        diagnostics = validate_rule(rule, _catalog())
        assert any(d.code == CODE_TERM_TYPE_MISMATCH for d in diagnostics)

    def test_ne_tambien_exige_el_mismo_tipo(self) -> None:
        # EQ/NE no quedan exentos: la mezcla de tipos es incoherente con cualquier
        # operador, no solo con los de orden.
        rule = _rule(
            _source_term(_STRING_SOURCE_ID),
            ComparisonOperator.NE,
            _decimal_constant("1"),
        )
        diagnostics = validate_rule(rule, _catalog())
        assert any(d.code == CODE_TERM_TYPE_MISMATCH for d in diagnostics)

    def test_eq_entre_string_y_boolean_se_rechaza(self) -> None:
        boolean_constant = Term(
            term_kind=TermKind.CONSTANT,
            constant=ScalarValue(scalar_type=ScalarType.BOOLEAN, boolean_value=True),
        )
        rule = _rule(
            _source_term(_STRING_SOURCE_ID), ComparisonOperator.EQ, boolean_constant
        )
        diagnostics = validate_rule(rule, _catalog())
        assert any(d.code == CODE_TERM_TYPE_MISMATCH for d in diagnostics)

    def test_eq_entre_string_y_string_no_se_rechaza_por_esta_regla(self) -> None:
        # Control: el MISMO tipo a los dos lados es exactamente el caso que D1-02
        # autorizo (comparacion categorica feliz); no debe dispararse el mismatch.
        rule = _rule(
            _source_term(_STRING_SOURCE_ID),
            ComparisonOperator.EQ,
            _string_constant("above"),
        )
        diagnostics = validate_rule(rule, _catalog())
        assert not any(d.code == CODE_TERM_TYPE_MISMATCH for d in diagnostics)


class TestFibLevelsSeRechazaComoTermino:
    """fib.levels (P08b-D1-04, LOTE 5): NON_SERVIBLE calcada de vp.hvn/vp.lvn.

    No necesita un chequeo propio: el Bloque 3 ya rechaza CUALQUIER NON_SERVIBLE como
    termino de Rule (CODE_SOURCE_NOT_SERVIBLE, el mismo camino que market.footprint y
    vp.hvn/vp.lvn). Este test usa la declaracion REAL del modulo fib, no una sintetica,
    para que una futura desalineacion entre fib.py y este candado se note aqui.
    """

    def _catalogo_con_fib_levels(self) -> DataSourceCatalog:
        catalog = DataSourceCatalog()
        catalog.register(fib_levels_declaration())
        return catalog

    def test_referenciar_fib_levels_como_termino_se_rechaza(self) -> None:
        rule = _rule(
            _source_term(FIB_LEVELS_SOURCE_ID),
            ComparisonOperator.EQ,
            _decimal_constant("100"),
        )
        diagnostics = validate_rule(rule, self._catalogo_con_fib_levels())
        assert any(d.code == CODE_SOURCE_NOT_SERVIBLE for d in diagnostics)
