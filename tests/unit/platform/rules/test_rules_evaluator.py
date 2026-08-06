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

from decimal import Decimal
from uuid import uuid4

from ce_v5.platform.rules.catalog import DataSourceCatalog
from ce_v5.platform.rules.evaluator import evaluate
from ce_v5.platform.rules.indicators.divergence import (
    DIVERGENCE_KIND_NONE,
    DIVERGENCE_KIND_SOURCE_ID,
    DIVERGENCE_REGULAR_BULL_SOURCE_ID,
    divergence_kind_declaration,
    divergence_regular_bull_declaration,
)
from ce_v5.platform.rules.indicators.fib import (
    FIB_DIRECTION_SOURCE_ID,
    fib_direction_declaration,
)
from ce_v5.platform.rules.validator import (
    CODE_OPERATOR_REQUIRES_NUMERIC,
    CODE_TERM_TYPE_MISMATCH,
    validate_rule,
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


class TestFibDirectionExtremoAExtremo:
    """fib.direction end-to-end: la PRIMERA fuente STRING real que recorre el pipeline.

    Los tests de arriba usan fuentes categoricas SINTETICAS; este usa la declaracion
    REAL del catalogo (fib_direction_declaration) y sus tokens reales, que es lo que
    demuestra que D1 sirve para algo mas que un caso de laboratorio.
    """

    def _catalogo(self) -> DataSourceCatalog:
        catalog = DataSourceCatalog()
        catalog.register(fib_direction_declaration())
        return catalog

    def _regla_direction(
        self, operator: ComparisonOperator, constante: Term
    ) -> AlertRule:
        return _rule(
            _condition(_source_term(FIB_DIRECTION_SOURCE_ID), operator, constante)
        )

    def test_eq_contra_above_dispara_cuando_el_grid_dice_above(self) -> None:
        regla = self._regla_direction(ComparisonOperator.EQ, _string_constant("above"))
        serie = {FIB_DIRECTION_SOURCE_ID: _string_series("above")}
        assert evaluate(regla, serie).matched is True

    def test_eq_contra_above_no_dispara_cuando_el_grid_dice_below(self) -> None:
        regla = self._regla_direction(ComparisonOperator.EQ, _string_constant("above"))
        serie = {FIB_DIRECTION_SOURCE_ID: _string_series("below")}
        assert evaluate(regla, serie).matched is False

    def test_ne_contra_above_dispara_con_below(self) -> None:
        regla = self._regla_direction(ComparisonOperator.NE, _string_constant("above"))
        serie = {FIB_DIRECTION_SOURCE_ID: _string_series("below")}
        assert evaluate(regla, serie).matched is True

    def test_el_bloque_3_admite_eq_contra_una_constante_string(self) -> None:
        # Control positivo: la regla que SI tiene sentido pasa la admision. Sin esto,
        # los dos rechazos de abajo podrian estar pasando por el motivo equivocado.
        regla = self._regla_direction(ComparisonOperator.EQ, _string_constant("above"))
        diagnostics = validate_rule(regla, self._catalogo())
        assert diagnostics == []

    def test_el_bloque_3_rechaza_un_operador_de_orden(self) -> None:
        # "fib.direction > 'above'" no significa nada: se rechaza en ADMISION.
        regla = self._regla_direction(ComparisonOperator.GT, _string_constant("above"))
        diagnostics = validate_rule(regla, self._catalogo())
        assert any(d.code == CODE_OPERATOR_REQUIRES_NUMERIC for d in diagnostics)

    def test_el_bloque_3_rechaza_una_constante_decimal(self) -> None:
        # "fib.direction == 1" mezcla tipos: tambien se rechaza en ADMISION, no en
        # runtime -- el evaluador ya no revalida tipos (5A, P08b-D1-03).
        decimal_constant = Term(
            term_kind=TermKind.CONSTANT,
            constant=ScalarValue(
                scalar_type=ScalarType.DECIMAL, decimal_value=Decimal("1")
            ),
        )
        regla = self._regla_direction(ComparisonOperator.EQ, decimal_constant)
        diagnostics = validate_rule(regla, self._catalogo())
        assert any(d.code == CODE_TERM_TYPE_MISMATCH for d in diagnostics)


class TestDivergenceExtremoAExtremo:
    """divergence.*: la primera fuente BOOLEAN real que recorre el pipeline (LOTE 5).

    fib.direction ya demostro el camino STRING; lo que divergence anade es el otro
    categorico -- BOOLEAN --, que hasta ahora solo se habia ejercitado con una fuente
    SINTETICA. Aqui se usan las declaraciones REALES del catalogo y sus tokens reales.

    Y una fuente STRING cuyo vocabulario incluye la AUSENCIA ('none'): una regla que
    pregunte "divergence.kind != 'none'" es la forma natural de decir "hubo divergencia,
    la que sea", asi que ese token tiene que comportarse como un valor de pleno derecho.
    """

    def _catalogo(self) -> DataSourceCatalog:
        catalog = DataSourceCatalog()
        catalog.register(divergence_kind_declaration())
        catalog.register(divergence_regular_bull_declaration())
        return catalog

    def _regla_kind(self, operator: ComparisonOperator, constante: Term) -> AlertRule:
        return _rule(
            _condition(_source_term(DIVERGENCE_KIND_SOURCE_ID), operator, constante)
        )

    def _regla_flag(self, operator: ComparisonOperator, constante: Term) -> AlertRule:
        return _rule(
            _condition(
                _source_term(DIVERGENCE_REGULAR_BULL_SOURCE_ID), operator, constante
            )
        )

    def test_kind_eq_contra_un_token_dispara_en_la_barra_del_evento(self) -> None:
        regla = self._regla_kind(
            ComparisonOperator.EQ, _string_constant("regular_bear")
        )
        serie = {DIVERGENCE_KIND_SOURCE_ID: _string_series("regular_bear")}
        assert evaluate(regla, serie).matched is True

    def test_kind_eq_no_dispara_en_una_barra_sin_evento(self) -> None:
        regla = self._regla_kind(
            ComparisonOperator.EQ, _string_constant("regular_bear")
        )
        serie = {DIVERGENCE_KIND_SOURCE_ID: _string_series(DIVERGENCE_KIND_NONE)}
        assert evaluate(regla, serie).matched is False

    def test_kind_ne_none_es_hubo_divergencia_la_que_sea(self) -> None:
        # El modismo que hace util el token de ausencia. Con evento dispara; sin evento,
        # no. Si 'none' se sirviera como un hueco en vez de como un valor, esta regla no
        # se podria escribir.
        regla = self._regla_kind(
            ComparisonOperator.NE, _string_constant(DIVERGENCE_KIND_NONE)
        )
        con_evento = {DIVERGENCE_KIND_SOURCE_ID: _string_series("hidden_bull")}
        sin_evento = {DIVERGENCE_KIND_SOURCE_ID: _string_series(DIVERGENCE_KIND_NONE)}
        assert evaluate(regla, con_evento).matched is True
        assert evaluate(regla, sin_evento).matched is False

    def test_un_flag_eq_true_dispara_cuando_el_flag_esta_puesto(self) -> None:
        regla = self._regla_flag(ComparisonOperator.EQ, _boolean_constant(True))
        assert (
            evaluate(
                regla, {DIVERGENCE_REGULAR_BULL_SOURCE_ID: _boolean_series(True)}
            ).matched
            is True
        )

    def test_un_flag_eq_true_no_dispara_cuando_no_lo_esta(self) -> None:
        regla = self._regla_flag(ComparisonOperator.EQ, _boolean_constant(True))
        assert (
            evaluate(
                regla, {DIVERGENCE_REGULAR_BULL_SOURCE_ID: _boolean_series(False)}
            ).matched
            is False
        )

    def test_el_bloque_3_admite_las_dos_reglas_que_tienen_sentido(self) -> None:
        # Control positivo: sin esto, los rechazos de abajo podrian estar pasando por el
        # motivo equivocado.
        catalogo = self._catalogo()
        assert (
            validate_rule(
                self._regla_kind(
                    ComparisonOperator.NE, _string_constant(DIVERGENCE_KIND_NONE)
                ),
                catalogo,
            )
            == []
        )
        assert (
            validate_rule(
                self._regla_flag(ComparisonOperator.EQ, _boolean_constant(True)),
                catalogo,
            )
            == []
        )

    def test_el_bloque_3_rechaza_un_operador_de_orden_sobre_kind(self) -> None:
        # "divergence.kind > 'none'" no significa nada: los tokens no tienen orden.
        diagnostics = validate_rule(
            self._regla_kind(ComparisonOperator.GT, _string_constant("regular_bull")),
            self._catalogo(),
        )
        assert any(d.code == CODE_OPERATOR_REQUIRES_NUMERIC for d in diagnostics)

    def test_el_bloque_3_rechaza_un_operador_de_orden_sobre_un_flag(self) -> None:
        # "divergence.regular_bull > true" tampoco: true no es mayor que nada.
        diagnostics = validate_rule(
            self._regla_flag(ComparisonOperator.GT, _boolean_constant(False)),
            self._catalogo(),
        )
        assert any(d.code == CODE_OPERATOR_REQUIRES_NUMERIC for d in diagnostics)

    def test_el_bloque_3_rechaza_comparar_kind_contra_un_booleano(self) -> None:
        # STRING vs BOOLEAN: los dos son categoricos y aun asi NO son comparables. Es el
        # error facil ahora que la misma fuente tiene una cara de cada tipo.
        diagnostics = validate_rule(
            self._regla_kind(ComparisonOperator.EQ, _boolean_constant(True)),
            self._catalogo(),
        )
        assert any(d.code == CODE_TERM_TYPE_MISMATCH for d in diagnostics)

    def test_el_bloque_3_rechaza_comparar_un_flag_contra_un_string(self) -> None:
        diagnostics = validate_rule(
            self._regla_flag(ComparisonOperator.EQ, _string_constant("true")),
            self._catalogo(),
        )
        assert any(d.code == CODE_TERM_TYPE_MISMATCH for d in diagnostics)

    def test_el_bloque_3_rechaza_comparar_un_flag_contra_un_decimal(self) -> None:
        # "divergence.regular_bull == 1": un bool NO es un entero (contracts/scalar.py
        # lo
        # rechaza incluso al construir el valor) y el Bloque 3 lo atrapa en ADMISION.
        decimal_constant = Term(
            term_kind=TermKind.CONSTANT,
            constant=ScalarValue(
                scalar_type=ScalarType.DECIMAL, decimal_value=Decimal("1")
            ),
        )
        diagnostics = validate_rule(
            self._regla_flag(ComparisonOperator.EQ, decimal_constant), self._catalogo()
        )
        assert any(d.code == CODE_TERM_TYPE_MISMATCH for d in diagnostics)
