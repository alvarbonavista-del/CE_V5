"""Lenguaje de reglas de plataforma (ADR-015/016/017): la Rule como dato.

Paquete de contratos de la ENTIDAD Rule (raiz neutral y especializaciones,
estructura, forma canonica). Las FAMILIAS de evento rule.*/signal.*/alert.*
que el motor emite viven en source.families, junto a las demas familias.
Importable como 'source.rules' (raiz de importacion en contracts/).
"""

from source.rules.budget import (
    MAX_CONDITIONS_PER_FEATURE,
    MAX_FEATURES_PER_GROUP,
    MAX_GROUPS_PER_RULE,
    MAX_SOURCES_PER_FEATURE,
)
from source.rules.vocab import (
    CombineMode,
    ComparisonOperator,
    RuleCombineMode,
    TriggerPolicy,
    VetoMode,
)

__all__ = [
    "MAX_CONDITIONS_PER_FEATURE",
    "MAX_FEATURES_PER_GROUP",
    "MAX_GROUPS_PER_RULE",
    "MAX_SOURCES_PER_FEATURE",
    "CombineMode",
    "ComparisonOperator",
    "RuleCombineMode",
    "TriggerPolicy",
    "VetoMode",
]
