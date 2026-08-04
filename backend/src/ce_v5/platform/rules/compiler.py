"""Compilador de reglas: forma canonica -> Execution Plan + PlanFingerprint (ADR-017).

Codigo PURO de plataforma (sin DB, sin infra). Toma una Rule YA ADMITIDA (el Bloque 3 ya
corrio el presupuesto y la validacion semantica) y produce el ExecutionPlan que el motor
del Bloque 6 ejecutara: que fuentes resolver, cuanta historia pide cada una, y con que
claves de disparo se activa.

FAIL-LOUD. Si una fuente no resuelve en el catalogo o no es servible, el plan NO es
recomputable: se lanza CompilationError. Esa es exactamente la senal que el runtime
convertira en CUARENTENA (Bloque 6); aqui se detecta, no se traga.

PLAN FINGERPRINT (ADR-017). El fingerprint reune TODAS las versiones de las que depende
la reproducibilidad de un plan (compilador, catalogo de funciones, version de cada
DataSource, politicas). Si cualquiera cambia, el fingerprint cambia y el runtime
sabe que debe recompilar. Se calcula DENTRO de compile y viaja en el plan.

FRONTERAS DE CAPA. Este modulo es platform y solo importa de su MISMA capa
(platform.rules.canonical/catalog/functions) y de contracts; NUNCA de infra (check 7.1).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

from ce_v5.platform.rules.canonical import canonical_rule_hash
from ce_v5.platform.rules.catalog import DataSourceCatalog, UnknownDataSourceError
from ce_v5.platform.rules.functions import FUNCTION_CATALOG_VERSION, history_bars_needed
from source.datasource import DataSourceDeclaration, Servibility
from source.rules.market_rules import AnyRule
from source.rules.reference import DataSourceParam
from source.rules.scalar import ScalarValue
from source.rules.term import SourceTerm, TermKind

# Versiones de compilacion (inputs del PlanFingerprint, ADR-017). Cada una se sube
# cuando cambia su subsistema: el algoritmo del compilador, el indice de disparo (como
# se forman las trigger_keys) o la politica de planificacion.
COMPILER_VERSION = 1
TRIGGER_INDEX_VERSION = 1
PLAN_POLICY_VERSION = 1


class CompilationError(RuntimeError):
    """La regla no se puede compilar a un plan recomputable (fail-loud)."""


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    """Una fuente de la regla, resuelta en el catalogo, con su historia dimensionada.

    params son los EFECTIVOS (defaults de la declaracion + overrides de la referencia,
    MAT-05 Q2), ORDENADOS por nombre para que el plan sea determinista. Vacio si la
    fuente no declara parametros. El materializador los consume en vez de hardcodear el
    default (dispatch, MAT-06).
    """

    source_id: str
    declaration: DataSourceDeclaration
    history_bars: int
    params: tuple[tuple[str, ScalarValue], ...] = ()

    def param(self, name: str) -> ScalarValue | None:
        """El valor EFECTIVO de un parametro, o None si la fuente no lo declara."""
        for param_name, value in self.params:
            if param_name == name:
                return value
        return None


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Plan de ejecucion de una regla admitida (ADR-017). Determinista."""

    rule_id: UUID
    tenant_id: UUID
    product: str
    exchange: str
    symbol: str
    trigger_keys: frozenset[tuple[str, str, str]]
    resolved_sources: tuple[ResolvedSource, ...]
    fingerprint: str


def _iter_source_terms(rule: AnyRule) -> Iterator[SourceTerm]:
    """Todos los SourceTerm de la regla: grupos->features->condiciones, mas el veto."""
    conditions = [
        condition
        for group in rule.groups
        for feature in group.features
        for condition in feature.conditions
    ]
    if rule.veto is not None:
        conditions.extend(rule.veto.conditions)
    for condition in conditions:
        for term in (condition.left, condition.right):
            if term.term_kind is TermKind.SOURCE and term.source is not None:
                yield term.source


# Nombres que NO son parametro de ninguna fuente: constantes FIJAS de materializacion
# (MAT-05 Q3). window_bars es la ventana rodante del perfil; dejarla configurar por
# regla cambiaria el HECHO que se sirve sin cambiar la declaracion. Se rechaza con
# mensaje propio, no con el generico de "param desconocido": quien la escribe cree que
# es configurable y merece saber por que no lo es.
_FIXED_MATERIALIZATION_NAMES = frozenset({"window_bars"})


def _effective_params(
    source_id: str,
    declaration: DataSourceDeclaration,
    overrides: tuple[DataSourceParam, ...],
) -> tuple[tuple[str, ScalarValue], ...]:
    """Params EFECTIVOS de una referencia: defaults de la declaracion + overrides.

    Valida cada override contra los params DECLARADOS (MAT-05 Q2): el nombre existe, el
    param esta OVERRIDE-HABILITADO (fix MAT-05 Q2: el materializador lo consume hoy,
    `declaration.overridable_params`) y el tipo casa; si la fuente declara un dominio
    (`ParamSpec.valid_values`), el valor debe pertenecer a el. FAIL-LOUD siempre: un
    override desconocido, DEFAULT-ONLY, mal tipado, fuera de dominio o sobre una
    constante fija se RECHAZA en compilacion, nunca se degrada al default en silencio
    -- servir 50 a quien pidio 7 es exactamente la deuda D-E2.1 de v4. Un param
    DECLARADO pero no override-habilitado es el mismo riesgo: compilaria, viajaria y el
    materializador que no lo lee lo ignoraria callado (ratificado por Central).

    Devuelve la tupla ORDENADA por nombre (forma canonica, como DataSourceRef.params):
    el mismo par (declaracion, referencia) produce SIEMPRE la misma tupla.

    DIFERIDO GATEADO: estos efectivos son los que la cache de evaluacion compartida
    debera codificar en el cache_key-VALOR cuando exista (hoy solo hay cache_key_SCHEMA;
    ver REGISTRO_DECISIONES, diferido gateado de MAT-05 Q2).
    """
    declared = {spec.name: spec for spec in declaration.params}
    overridden: dict[str, ScalarValue] = {}
    for override in overrides:
        if override.name in _FIXED_MATERIALIZATION_NAMES:
            msg = (
                f"el DataSource {source_id!r} recibe un override de {override.name!r}, "
                "que NO es parametro de fuente sino constante fija de materializacion: "
                "no se puede configurar por regla (MAT-05 Q3)."
            )
            raise CompilationError(msg)
        spec = declared.get(override.name)
        if spec is None:
            msg = (
                f"el DataSource {source_id!r} no declara el parametro "
                f"{override.name!r} (declara {sorted(declared)!r}): el plan no es "
                "recomputable (fail-loud)."
            )
            raise CompilationError(msg)
        if override.name not in declaration.overridable_params:
            msg = (
                f"el parametro {override.name!r} de {source_id!r} es DEFAULT-ONLY: "
                "esta declarado pero el materializador todavia no lo consume, asi "
                "que un override compilaria, viajaria al plan y se ignoraria en "
                "silencio (fail-loud, fix MAT-05 Q2)."
            )
            raise CompilationError(msg)
        if override.value.scalar_type is not spec.value_type:
            msg = (
                f"el parametro {override.name!r} de {source_id!r} se declara "
                f"{spec.value_type.value!r} y la regla lo pasa como "
                f"{override.value.scalar_type.value!r}: tipos incompatibles "
                "(fail-loud)."
            )
            raise CompilationError(msg)
        if spec.valid_values and override.value.string_value not in spec.valid_values:
            msg = (
                f"el parametro {override.name!r} de {source_id!r} recibe "
                f"{override.value.string_value!r}, fuera de su dominio valido "
                f"{spec.valid_values!r} (fail-loud)."
            )
            raise CompilationError(msg)
        overridden[override.name] = override.value

    effective: list[tuple[str, ScalarValue]] = []
    for spec in declaration.params:
        value = overridden.get(spec.name, spec.default)
        # Un param declarado SIN default y SIN override no tiene valor efectivo: no se
        # inventa uno. El materializador aplica el suyo, como hasta ahora.
        if value is not None:
            effective.append((spec.name, value))
    return tuple(sorted(effective, key=lambda item: item[0]))


def plan_fingerprint(
    rule: AnyRule, resolved_sources: tuple[ResolvedSource, ...]
) -> str:
    """PlanFingerprint (ADR-017): SHA-256 de los inputs de compilacion de v5.0.

    Reune lo que ADR-017 exige y existe en v5.0: la identidad y el hash de evaluacion
    de la regla, su schema_version, las versiones del compilador y del catalogo de
    funciones, la version de cada DataSource resuelta, y las versiones del indice de
    disparo y de la politica de plan. Diccionario canonico y ordenado -> mismo
    fingerprint.
    """
    # En v5.0 la version UNICA de cada DataSource (declaration.version) cubre a la vez
    # manifest + capability_schema + cache_key_schema: son un solo numero mientras no
    # diverjan. Cuando diverjan se separaran en claves distintas de este mismo
    # diccionario, cada una subiendo por su lado (ADR-017).
    datasource_manifest_versions = {
        source.source_id: source.declaration.version for source in resolved_sources
    }
    payload = {
        "rule_id": str(rule.rule_id),
        "canonical_rule_hash": canonical_rule_hash(rule),
        "rule_schema_version": rule.schema_version,
        "compiler_version": COMPILER_VERSION,
        "function_catalog_version": FUNCTION_CATALOG_VERSION,
        "datasource_manifest_versions": datasource_manifest_versions,
        "trigger_index_version": TRIGGER_INDEX_VERSION,
        "plan_policy_version": PLAN_POLICY_VERSION,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compile(rule: AnyRule, catalog: DataSourceCatalog) -> ExecutionPlan:
    """Compila una regla YA ADMITIDA a su ExecutionPlan (determinista, fail-loud).

    No re-corre el presupuesto (eso es del Bloque 3). Resuelve cada fuente contra el
    catalogo (fail-loud si falta o es NON_SERVIBLE), VALIDA Y PROPAGA sus parametros
    efectivos (MAT-05 Q2), dimensiona su historia como el MAXIMO de velas que pide entre
    todos sus usos, arma las claves de disparo (una por evaluation_context de grupo; en
    v5.0 el unico trigger cableado es candle_close) y calcula el PlanFingerprint. Mismo
    rule + mismo catalogo -> mismo plan.

    ADITIVIDAD (D7): una regla SIN params compila exactamente como antes y su fuente se
    materializa con los defaults de la declaracion.
    """
    history_by_source: dict[str, int] = {}
    declaration_by_source: dict[str, DataSourceDeclaration] = {}
    params_by_source: dict[str, tuple[tuple[str, ScalarValue], ...]] = {}
    for source_term in _iter_source_terms(rule):
        source_id = source_term.ref.source_id
        try:
            declaration = catalog.resolve(source_id)
        except UnknownDataSourceError as exc:
            msg = (
                f"la regla referencia el DataSource {source_id!r}, que no esta en el "
                "catalogo: el plan no es recomputable (fail-loud)."
            )
            raise CompilationError(msg) from exc
        if declaration.servibility is Servibility.NON_SERVIBLE:
            msg = (
                f"el DataSource {source_id!r} es NON_SERVIBLE: no puede ser termino "
                "de una regla, el plan no es recomputable (fail-loud)."
            )
            raise CompilationError(msg)
        # MAT-05 Q2: los params de la referencia se VALIDAN y se PROPAGAN (antes se
        # rechazaban en bloque). El default sigue aplicando donde no hay override.
        params = _effective_params(source_id, declaration, source_term.ref.params)
        previous_params = params_by_source.get(source_id)
        if previous_params is not None and previous_params != params:
            # La serie del plan se indexa por SOURCE_ID: dos referencias a la MISMA
            # fuente con params efectivos DISTINTOS pediran dos hechos distintos y solo
            # cabria uno. Servir uno de los dos a ambas seria la deuda D-E2.1 otra vez.
            msg = (
                f"la regla referencia {source_id!r} con dos juegos de parametros "
                f"efectivos distintos ({previous_params!r} y {params!r}): son hechos "
                "DISTINTOS y el plan solo puede servir uno (fail-loud)."
            )
            raise CompilationError(msg)
        params_by_source[source_id] = params
        bars = history_bars_needed(source_term.function, source_term.offset)
        history_by_source[source_id] = max(history_by_source.get(source_id, 0), bars)
        declaration_by_source[source_id] = declaration

    resolved_sources = tuple(
        ResolvedSource(
            source_id=source_id,
            declaration=declaration_by_source[source_id],
            history_bars=history_by_source[source_id],
            params=params_by_source[source_id],
        )
        for source_id in sorted(history_by_source)
    )
    trigger_keys = frozenset(
        (rule.market_scope.exchange, rule.market_scope.symbol, group.evaluation_context)
        for group in rule.groups
    )
    fingerprint = plan_fingerprint(rule, resolved_sources)
    return ExecutionPlan(
        rule_id=rule.rule_id,
        tenant_id=rule.tenant_id,
        product=rule.product.value,
        exchange=rule.market_scope.exchange,
        symbol=rule.market_scope.symbol,
        trigger_keys=trigger_keys,
        resolved_sources=resolved_sources,
        fingerprint=fingerprint,
    )
