"""Validador semantico de reglas dirigido por catalogo (INFORME 6 sec 12.4, ADR-016).

El contrato ya garantiza la ESTRUCTURA (Bloque 1). Aqui se valida la SEMANTICA contra el
catalogo de DataSources: que cada fuente EXISTA y sea SERVIBLE, que el contexto del
grupo este soportado por la fuente, que la funcion CASE con la servibilidad (continua vs
esporadica) y que los parametros existan y casen de tipo. Los diagnosticos son
code+params (ADR-016): la UI los traduce; no hay texto de interfaz hardcodeado.

admit_rule es la PUERTA DE ADMISION: valida y devuelve la forma canonica. Nada se
persiste ni compila sin pasar por aqui (INFORME 6 sec 10.5). La normalizacion de una
entrada de SUPERFICIE laxa (asignar node_id, rellenar modos) es del chatbot (pieza
posterior): aqui las reglas llegan estrictas y solo se reordenan a canonica.

ALCANCE (minimo viable; flagged para Central): las condiciones del VETO no declaran
evaluation_context en el modelo actual, asi que se validan salvo el soporte de contexto.
Las reglas no_combinable/doble_conteo (que exigen campos de catalogo que anade I-02)
quedan como refinaciones posteriores.

COHERENCIA DE TIPOS DE UNA COMPARACION (D1, dictamen P08b-D1-03, OPCION 1 fail-loud).
Antes esto quedaba diferido; ahora se rechaza en ADMISION, no en runtime: (a) los
operadores de ORDEN (>,>=,<,<=) exigen un value_type NUMERICO (decimal/integer) en
AMBOS lados -- "mayor que" sobre un categorico (STRING/BOOLEAN) no significa nada; (b)
CUALQUIER operador, EQ/NE incluidos, exige que los DOS lados tengan el MISMO
value_type -- comparar un STRING contra una constante DECIMAL es tan incoherente como
comparar contra un BOOLEAN, y el error se atrapa UNA VEZ al admitir la regla, no en cada
tick de cada barra. El evaluador (evaluator.py) YA NO revalida esto: confia en que toda
Rule que llega a evaluate() paso por aqui (docstring de evaluate: "Rule YA ADMITIDA").
"""

from dataclasses import dataclass

from ce_v5.platform.rules.canonical import canonicalize
from ce_v5.platform.rules.catalog import DataSourceCatalog, UnknownDataSourceError
from source.datasource import DataSourceDeclaration, Servibility
from source.rules.budget import (
    MAX_CONDITIONS_PER_FEATURE,
    MAX_FEATURES_PER_GROUP,
    MAX_GROUPS_PER_RULE,
    MAX_SOURCES_PER_FEATURE,
)
from source.rules.condition import Condition
from source.rules.reference import DataSourceRef
from source.rules.rule import Rule
from source.rules.scalar import ScalarType
from source.rules.term import SourceTerm, Term, TermKind
from source.rules.vocab import (
    NO_OFFSET_FUNCTIONS,
    OFFSET_FUNCTIONS,
    ComparisonOperator,
)

# codigos de diagnostico (ADR-016). La UI los traduce por i18n.
CODE_SOURCE_UNKNOWN = "rule.source.unknown"
CODE_SOURCE_NOT_SERVIBLE = "rule.source.not_servible"
CODE_CONTEXT_UNSUPPORTED = "rule.context.unsupported"
CODE_FUNCTION_REQUIRES_CONTINUOUS = "rule.function.requires_continuous"
CODE_FUNCTION_REQUIRES_SPORADIC = "rule.function.requires_sporadic"
CODE_PARAM_UNKNOWN = "rule.param.unknown"
CODE_PARAM_TYPE_MISMATCH = "rule.param.type_mismatch"
CODE_PARAM_MISSING = "rule.param.missing"
CODE_OPERATOR_REQUIRES_NUMERIC = "rule.operator.requires_numeric"
CODE_TERM_TYPE_MISMATCH = "rule.term.type_mismatch"

# NUMERICO: los unicos value_type que soportan orden total (>,>=,<,<=). CATEGORICO
# (STRING/BOOLEAN) solo soporta EQ/NE (D1, P08b-D1-02 Q2).
_NUMERIC_TYPES = frozenset({ScalarType.DECIMAL, ScalarType.INTEGER})
_ORDERING_OPERATORS = frozenset(
    {
        ComparisonOperator.GT,
        ComparisonOperator.GE,
        ComparisonOperator.LT,
        ComparisonOperator.LE,
    }
)


@dataclass
class Diagnostic:
    """Diagnostico de validacion como code + params (ADR-016)."""

    code: str
    params: dict[str, str]


class RuleValidationError(RuntimeError):
    """La regla no paso la validacion semantica; lleva los diagnosticos."""

    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        self.diagnostics = diagnostics
        codes = ", ".join(d.code for d in diagnostics)
        super().__init__(f"regla invalida ({len(diagnostics)}): {codes}")


def validate_rule(rule: Rule, catalog: DataSourceCatalog) -> list[Diagnostic]:
    """Valida una regla contra el catalogo. Devuelve diagnosticos (vacio = valida)."""
    diagnostics: list[Diagnostic] = []
    for group in rule.groups:
        for feature in group.features:
            for condition in feature.conditions:
                _validate_condition(
                    condition, group.evaluation_context, catalog, diagnostics
                )
    if rule.veto is not None:
        for condition in rule.veto.conditions:
            _validate_condition(condition, None, catalog, diagnostics)
    return diagnostics


# --- Complexity budget: hard caps de plataforma (DOC_ARQ_V5; ADR-015) ---
# Los VALORES viven en source.rules.budget (contracts): los comparte el borde de infra
# que escribe la autoria y abre suscripciones, y contracts es la unica base que platform
# e infra pueden importar sin cruzar la frontera de capas (ADR-002). Aqui solo se
# APLICAN. Los limites por plan (nodos booleanos totales, cuota comercial) son concern
# de P11 + el gate y no se declaran ni aqui ni alli.
CODE_TOO_MANY_GROUPS = "rule.complexity.too_many_groups"
CODE_TOO_MANY_FEATURES = "rule.complexity.too_many_features"
CODE_TOO_MANY_CONDITIONS = "rule.complexity.too_many_conditions"
CODE_TOO_MANY_SOURCES = "rule.complexity.too_many_sources"


def _source_ids(conditions: tuple[Condition, ...]) -> set[str]:
    ids: set[str] = set()
    for condition in conditions:
        for term in (condition.left, condition.right):
            if term.term_kind is TermKind.SOURCE and term.source is not None:
                ids.add(term.source.ref.source_id)
    return ids


def validate_complexity(rule: Rule) -> list[Diagnostic]:
    """Aplica los hard caps de plataforma; diagnosticos code+params."""
    diagnostics: list[Diagnostic] = []
    if len(rule.groups) > MAX_GROUPS_PER_RULE:
        diagnostics.append(
            Diagnostic(
                code=CODE_TOO_MANY_GROUPS,
                params={
                    "max": str(MAX_GROUPS_PER_RULE),
                    "actual": str(len(rule.groups)),
                },
            )
        )
    for group in rule.groups:
        if len(group.features) > MAX_FEATURES_PER_GROUP:
            diagnostics.append(
                Diagnostic(
                    code=CODE_TOO_MANY_FEATURES,
                    params={
                        "max": str(MAX_FEATURES_PER_GROUP),
                        "actual": str(len(group.features)),
                        "context": group.evaluation_context,
                    },
                )
            )
        for feature in group.features:
            if len(feature.conditions) > MAX_CONDITIONS_PER_FEATURE:
                diagnostics.append(
                    Diagnostic(
                        code=CODE_TOO_MANY_CONDITIONS,
                        params={
                            "max": str(MAX_CONDITIONS_PER_FEATURE),
                            "actual": str(len(feature.conditions)),
                        },
                    )
                )
            source_count = len(_source_ids(feature.conditions))
            if source_count > MAX_SOURCES_PER_FEATURE:
                diagnostics.append(
                    Diagnostic(
                        code=CODE_TOO_MANY_SOURCES,
                        params={
                            "max": str(MAX_SOURCES_PER_FEATURE),
                            "actual": str(source_count),
                        },
                    )
                )
    if rule.veto is not None:
        if len(rule.veto.conditions) > MAX_CONDITIONS_PER_FEATURE:
            diagnostics.append(
                Diagnostic(
                    code=CODE_TOO_MANY_CONDITIONS,
                    params={
                        "max": str(MAX_CONDITIONS_PER_FEATURE),
                        "actual": str(len(rule.veto.conditions)),
                        "scope": "veto",
                    },
                )
            )
        veto_sources = len(_source_ids(rule.veto.conditions))
        if veto_sources > MAX_SOURCES_PER_FEATURE:
            diagnostics.append(
                Diagnostic(
                    code=CODE_TOO_MANY_SOURCES,
                    params={
                        "max": str(MAX_SOURCES_PER_FEATURE),
                        "actual": str(veto_sources),
                        "scope": "veto",
                    },
                )
            )
    return diagnostics


def admit_rule(rule: Rule, catalog: DataSourceCatalog) -> Rule:
    """Puerta de admision.

    Valida la regla y devuelve su forma canonica; lanza
    RuleValidationError si hay diagnosticos.
    """
    diagnostics = validate_rule(rule, catalog) + validate_complexity(rule)
    if diagnostics:
        raise RuleValidationError(diagnostics)
    return canonicalize(rule)


def _validate_condition(
    condition: Condition,
    context: str | None,
    catalog: DataSourceCatalog,
    diagnostics: list[Diagnostic],
) -> None:
    for term in (condition.left, condition.right):
        if term.term_kind is TermKind.SOURCE and term.source is not None:
            _validate_source_term(
                term.source, str(condition.node_id), context, catalog, diagnostics
            )
    _validate_type_coherence(condition, catalog, diagnostics)


def _term_value_type(term: Term, catalog: DataSourceCatalog) -> ScalarType | None:
    """El value_type EFECTIVO de un termino, o None si no se puede determinar.

    Constante: su propio scalar_type, siempre presente (garantia del contrato
    ScalarValue). Fuente: el value_type DECLARADO en el catalogo; None si la fuente es
    desconocida -- ese hecho YA lo diagnostica CODE_SOURCE_UNKNOWN por su cuenta, y
    aqui no hay tipo del que hablar.
    """
    if term.term_kind is TermKind.CONSTANT:
        assert term.constant is not None
        return term.constant.scalar_type
    assert term.source is not None
    try:
        declaration = catalog.resolve(term.source.ref.source_id)
    except UnknownDataSourceError:
        return None
    return declaration.value_type


def _validate_type_coherence(
    condition: Condition,
    catalog: DataSourceCatalog,
    diagnostics: list[Diagnostic],
) -> None:
    """Coherencia de tipos de una comparacion (D1, dictamen P08b-D1-03, OPCION 1).

    Dos hechos INDEPENDIENTES, los dos fail-loud en admision:
    (a) un operador de ORDEN (>,>=,<,<=) sobre un lado CATEGORICO (STRING/BOOLEAN) no
        tiene semantica -- "mayor que" sobre 'above'/'below' o true/false no es nada.
    (b) los DOS lados de CUALQUIER operador (EQ/NE incluidos) deben tener el MISMO
        value_type -- comparar un STRING contra una constante DECIMAL es tan
        incoherente como contra un BOOLEAN.
    Si un lado no se puede tipar (fuente desconocida, ya diagnosticada aparte) se omite
    la comprobacion de ESE lado: no se apila un segundo diagnostico sobre un primer
    error ya reportado.
    """
    node_id = str(condition.node_id)
    left_type = _term_value_type(condition.left, catalog)
    right_type = _term_value_type(condition.right, catalog)
    for side, value_type in (("left", left_type), ("right", right_type)):
        if (
            value_type is not None
            and condition.operator in _ORDERING_OPERATORS
            and value_type not in _NUMERIC_TYPES
        ):
            diagnostics.append(
                Diagnostic(
                    CODE_OPERATOR_REQUIRES_NUMERIC,
                    {
                        "operator": condition.operator.value,
                        "value_type": value_type.value,
                        "side": side,
                        "node_id": node_id,
                    },
                )
            )
    if left_type is not None and right_type is not None and left_type is not right_type:
        diagnostics.append(
            Diagnostic(
                CODE_TERM_TYPE_MISMATCH,
                {
                    "operator": condition.operator.value,
                    "left_type": left_type.value,
                    "right_type": right_type.value,
                    "node_id": node_id,
                },
            )
        )


def _validate_source_term(
    source_term: SourceTerm,
    node_id: str,
    context: str | None,
    catalog: DataSourceCatalog,
    diagnostics: list[Diagnostic],
) -> None:
    source_id = source_term.ref.source_id
    try:
        declaration = catalog.resolve(source_id)
    except UnknownDataSourceError:
        diagnostics.append(
            Diagnostic(
                CODE_SOURCE_UNKNOWN, {"source_id": source_id, "node_id": node_id}
            )
        )
        return
    if declaration.servibility is Servibility.NON_SERVIBLE:
        diagnostics.append(
            Diagnostic(
                CODE_SOURCE_NOT_SERVIBLE, {"source_id": source_id, "node_id": node_id}
            )
        )
    if context is not None and context not in declaration.evaluation_contexts:
        diagnostics.append(
            Diagnostic(
                CODE_CONTEXT_UNSUPPORTED,
                {"source_id": source_id, "context": context, "node_id": node_id},
            )
        )
    function = source_term.function
    if function is not None:
        if (
            function in OFFSET_FUNCTIONS
            and declaration.servibility is not Servibility.CONTINUOUS
        ):
            diagnostics.append(
                Diagnostic(
                    CODE_FUNCTION_REQUIRES_CONTINUOUS,
                    {
                        "source_id": source_id,
                        "function": function.value,
                        "node_id": node_id,
                    },
                )
            )
        elif (
            function in NO_OFFSET_FUNCTIONS
            and declaration.servibility is not Servibility.SPORADIC
        ):
            diagnostics.append(
                Diagnostic(
                    CODE_FUNCTION_REQUIRES_SPORADIC,
                    {
                        "source_id": source_id,
                        "function": function.value,
                        "node_id": node_id,
                    },
                )
            )
    _validate_params(source_term.ref, declaration, node_id, diagnostics)


def _validate_params(
    ref: DataSourceRef,
    declaration: DataSourceDeclaration,
    node_id: str,
    diagnostics: list[Diagnostic],
) -> None:
    source_id = ref.source_id
    specs = {spec.name: spec for spec in declaration.params}
    given = {param.name: param for param in ref.params}
    for name, param in given.items():
        spec = specs.get(name)
        if spec is None:
            diagnostics.append(
                Diagnostic(
                    CODE_PARAM_UNKNOWN,
                    {"source_id": source_id, "param": name, "node_id": node_id},
                )
            )
            continue
        if param.value.scalar_type is not spec.value_type:
            diagnostics.append(
                Diagnostic(
                    CODE_PARAM_TYPE_MISMATCH,
                    {
                        "source_id": source_id,
                        "param": name,
                        "expected": spec.value_type.value,
                        "got": param.value.scalar_type.value,
                        "node_id": node_id,
                    },
                )
            )
    for name, spec in specs.items():
        if spec.default is None and name not in given:
            diagnostics.append(
                Diagnostic(
                    CODE_PARAM_MISSING,
                    {"source_id": source_id, "param": name, "node_id": node_id},
                )
            )
