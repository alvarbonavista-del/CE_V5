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
    """Una fuente de la regla, resuelta en el catalogo, con su historia dimensionada."""

    source_id: str
    declaration: DataSourceDeclaration
    history_bars: int


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
    catalogo (fail-loud si falta o es NON_SERVIBLE), dimensiona su historia como el
    MAXIMO de velas que pide entre todos sus usos, arma las claves de disparo (una por
    evaluation_context de grupo; en v5.0 el unico trigger cableado es candle_close) y
    calcula el PlanFingerprint. Mismo rule + mismo catalogo -> mismo plan.
    """
    history_by_source: dict[str, int] = {}
    declaration_by_source: dict[str, DataSourceDeclaration] = {}
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
        bars = history_bars_needed(source_term.function, source_term.offset)
        history_by_source[source_id] = max(history_by_source.get(source_id, 0), bars)
        declaration_by_source[source_id] = declaration

    resolved_sources = tuple(
        ResolvedSource(
            source_id=source_id,
            declaration=declaration_by_source[source_id],
            history_bars=history_by_source[source_id],
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
