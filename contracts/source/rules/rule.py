"""Rule: la raiz NEUTRAL del motor de reglas (ADR-015, INFORME 6 sec 10.1-10.5).

Una maquinaria, dos productos: esta raiz es NEUTRAL -- NO conoce mercado ni trading
(criterio 4). Define la estructura comun (grupos -> features -> condiciones, veto,
modos de combinacion, trigger) que comparten AlertRule (avisar) y TradingSignalRule
(senalar); esas especializaciones viven en market_rules.py y son las UNICAS que anaden
mercado (market_scope). La trading-ness vive en la hoja y en la proyeccion, JAMAS en la
raiz.

TARGET_BINDING (neutralidad). La raiz no tiene exchange ni symbol: declara a QUE esta
ligada la regla mediante target_binding, una abstraccion NEUTRAL (INFORME 6 sec 10.3).
En v5.0 solo existe el binding de MERCADO, pero la raiz solo conoce su KIND, no sus
detalles: el exchange y el symbol concretos son market_scope, en la hoja. Asi un
producto futuro no de mercado encaja sin tocar la raiz. Decision de construccion
(revisable por Central): la raiz lleva target_binding.binding_kind; la hoja de mercado
lleva market_scope y valida la coherencia. target_binding y product son ejes
ORTOGONALES: binding_kind = a QUE se liga (el sujeto); product = QUE emite (alert vs
signal).

REGLA COMO DATO TENANT-SCOPED. rule_id + tenant_id: las reglas son datos por-tenant
bajo RLS (ADR-011). name es texto libre del usuario (ADR-016); su normalizacion
anti-colision (NFC + homoglifos) la aplica la capa de nombres, no este contrato.

MODOS Y VENTANA. rule_combine_mode dice como se combinan los GRUPOS (all/any/
all_within_window). Es OBLIGATORIO y explicito. La ventana N de all_within_window vive
en window (un enum no se parametriza): obligatoria si y solo si el modo es
all_within_window. ANCLAJE (decision de construccion, revisable por Central; ambiguedad
hallada por Claude Code): window es un N SIN unidad propia y se ancla al
evaluation_context de los grupos; para que N sea inequivoco, all_within_window EXIGE que
TODOS los grupos compartan un unico evaluation_context. Ventanas cross-contexto son una
extension futura con anclaje explicito (ADR-005); hoy se rechazan.

DEFAULTS. schema_version lleva default 1 A PROPOSITO, a diferencia de los modos: una
version ausente significa 'version 1' por el tolerant-reader / migrar-al-cargar de
ADR-005, que es el comportamiento CORRECTO, no un cambio semantico silencioso. enabled,
en cambio, es OBLIGATORIO: no se asume que una regla nazca activa (fail-safe). El HARD
CAP N<=5 grupos lo valida el Bloque 3; el contrato exige al menos un grupo.
"""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from source.rules.group import Group
from source.rules.veto import Veto
from source.rules.vocab import RuleCombineMode, TriggerPolicy


class BindingKind(StrEnum):
    """Clase de sujeto al que se liga una regla (neutral). v5.0 solo mercado."""

    MARKET = "market"


class TargetBinding(BaseModel):
    """Abstraccion NEUTRAL del sujeto de una regla (INFORME 6 sec 10.3).

    La raiz solo conoce el KIND del binding; los detalles concretos (exchange, symbol en
    mercado) viven en la especializacion (market_scope). Es la costura que mantiene la
    raiz neutral y permite productos futuros no de mercado sin tocarla.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_kind: BindingKind


class Rule(BaseModel):
    """Raiz NEUTRAL: estructura comun de toda regla, sin mercado (ADR-015)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: UUID
    tenant_id: UUID
    name: str = Field(min_length=1, max_length=200)
    target_binding: TargetBinding
    trigger_policy: TriggerPolicy
    groups: tuple[Group, ...] = Field(min_length=1)
    veto: Veto | None = None
    rule_combine_mode: RuleCombineMode
    window: int | None = None
    schema_version: int = Field(default=1, ge=1)
    enabled: bool

    @model_validator(mode="after")
    def _ventana_coherente(self) -> "Rule":
        if self.rule_combine_mode is RuleCombineMode.ALL_WITHIN_WINDOW:
            if self.window is None:
                msg = "rule_combine_mode all_within_window exige window N (>=1)."
                raise ValueError(msg)
            if self.window < 1:
                msg = f"window debe ser >=1: {self.window}."
                raise ValueError(msg)
            contexts = {g.evaluation_context for g in self.groups}
            if len(contexts) > 1:
                msg = (
                    "rule_combine_mode all_within_window exige que TODOS los "
                    "grupos compartan un unico evaluation_context (la ventana N "
                    "se ancla a el); contextos distintos: "
                    f"{sorted(contexts)}. Las ventanas cross-contexto son una "
                    "extension futura con anclaje explicito (ADR-005)."
                )
                raise ValueError(msg)
        elif self.window is not None:
            msg = "window solo aplica a rule_combine_mode all_within_window."
            raise ValueError(msg)
        return self
