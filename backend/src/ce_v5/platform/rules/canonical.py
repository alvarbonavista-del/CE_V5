"""Forma canonica y hash estable de una Rule (ADR-015, ADR-017, INFORME 6 sec 10.5).

Primer codigo de plataforma de P08. Da dos cosas:

- canonicalize(rule): ordena grupos, features y condiciones por CONTENIDO (orden
  estable), preservando node_id y el resto de campos. Dos reglas equivalentes que solo
  difieren en el orden dan la MISMA forma. La persistencia guarda esta forma.

- canonical_rule_hash(rule): SHA-256 estable sobre el contenido de evaluacion. Es el
  canonical_rule_hash que alimenta el PlanFingerprint de ADR-017 y ancla dedup e
  historial. Cumple el DoD "misma regla -> mismo hash".

QUE ENTRA Y QUE NO (decision de construccion, revisable por Central). No usa el
__hash__/== de Pydantic (que incluye node_id): es una funcion DEDICADA sobre una
proyeccion canonica. Se EXCLUYEN (identidad, cosmetica, estado o proyeccion; no afectan
a la evaluacion, recursivo donde aplique): node_id (id de nodo asignado; no identifica
la logica), rule_id y tenant_id (claves subrogadas; excluir tenant_id deja abierta la
evaluacion compartida cross-tenant de ADR-017), name y domain_label (cosmeticos),
enabled (estado operativo) y product (solo afecta a la PROYECCION alert/trading_signal,
no a la evaluacion; no forma parte de la identidad de evaluacion). ENTRAN schema_version
y canonicalizer_version, y todo el contenido de evaluacion: el AST canonico, operadores,
terminos/constantes normalizadas, DataSources y sus parametros, funcion/offset, el
evaluation_context de cada grupo, los combine_modes y el veto (condiciones y veto_mode)
si existe. La version de DataSource y la de operador/indicador se resuelven en el
PlanFingerprint (Bloque 5, ADR-017), no aqui.

ORDEN CONMUTATIVO. grupos/features/condiciones se combinan por modos conmutativos
(all/any/any_blocks): su orden no cambia la semantica, asi que el hash los ordena y
canonicalize los reordena. Los dos lados de una condicion (left/right) NO se tocan:
'a < b' no es 'b < a'.
"""

import hashlib
import json
from typing import Any

from pydantic import BaseModel

from source.rules.feature import Feature
from source.rules.group import Group
from source.rules.rule import Rule
from source.rules.veto import Veto

# Version del algoritmo de canonicalizacion (CA-P08-02): si el algoritmo cambia, se
# sube y el hash cambia (invalida caches, dedup e historial calculados con el viejo).
CANONICALIZER_VERSION = 1

# Campos de identidad/cosmetica/estado/proyeccion que NO definen la evaluacion: fuera
# del hash. product solo afecta a la PROYECCION (alert/trading_signal), no a la
# evaluacion: no forma parte de la identidad de evaluacion. schema_version SI entra en
# el hash (CA-P08-02).
_EXCLUDED_TOP: frozenset[str] = frozenset(
    {"rule_id", "tenant_id", "name", "enabled", "product"}
)
# Claves excluidas en CUALQUIER nivel del arbol.
_EXCLUDED_ANY: frozenset[str] = frozenset({"node_id", "domain_label"})


def _strip_and_sort(obj: Any) -> Any:
    """Proyeccion canonica: quita _EXCLUDED_ANY en todo nivel y ORDENA cada lista.

    Todas las listas del modelo se combinan de forma conmutativa, asi que se ordenan
    por su contenido serializado. Dos arboles equivalentes colapsan a la misma forma.
    """
    if isinstance(obj, dict):
        return {
            key: _strip_and_sort(value)
            for key, value in obj.items()
            if key not in _EXCLUDED_ANY
        }
    if isinstance(obj, list):
        items = [_strip_and_sort(x) for x in obj]
        return sorted(
            items, key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=True)
        )
    return obj


def _content_key(model: BaseModel) -> str:
    """Clave canonica de un sub-modelo (para ordenar por contenido)."""
    canon = _strip_and_sort(model.model_dump(mode="json"))
    return json.dumps(canon, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


# QUE ENTRA EN EL HASH (CA-P08-02): schema_version, CANONICALIZER_VERSION y todo el
# contenido de evaluacion -- el AST canonico, operadores, terminos/constantes
# normalizadas, DataSources y sus parametros, funcion/offset, el evaluation_context de
# cada grupo, los combine_modes, y el veto (sus condiciones y veto_mode) si existe.
# QUE QUEDA EXCLUIDO (identidad, cosmetica, estado o proyeccion; no afecta a la
# evaluacion): node_id, rule_id, tenant_id, name, domain_label, enabled, y product (solo
# afecta a la PROYECCION alert/trading_signal, no a la evaluacion; no forma parte de la
# identidad de evaluacion). La version de DataSource y la de operador/indicador se
# resuelven en el PlanFingerprint (Bloque 5, ADR-017), no aqui.
def canonical_rule_hash(rule: Rule) -> str:
    """SHA-256 hex estable del contenido de evaluacion de la regla (ADR-017)."""
    data = rule.model_dump(mode="json")
    for key in _EXCLUDED_TOP:
        data.pop(key, None)
    data["canonicalizer_version"] = CANONICALIZER_VERSION
    canon = _strip_and_sort(data)
    payload = json.dumps(
        canon, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canon_feature(feature: Feature) -> Feature:
    conditions = tuple(sorted(feature.conditions, key=_content_key))
    return feature.model_copy(update={"conditions": conditions})


def _canon_group(group: Group) -> Group:
    features = tuple(
        sorted((_canon_feature(f) for f in group.features), key=_content_key)
    )
    return group.model_copy(update={"features": features})


def _canon_veto(veto: Veto | None) -> Veto | None:
    if veto is None:
        return None
    conditions = tuple(sorted(veto.conditions, key=_content_key))
    return veto.model_copy(update={"conditions": conditions})


def canonicalize(rule: Rule) -> Rule:
    """Reordena grupos/features/condiciones/veto por contenido; preserva node_id."""
    groups = tuple(sorted((_canon_group(g) for g in rule.groups), key=_content_key))
    return rule.model_copy(update={"groups": groups, "veto": _canon_veto(rule.veto)})
