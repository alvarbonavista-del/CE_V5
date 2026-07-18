"""Veto: bloque guardian que bloquea el paso a FIRING (ADR-015, INFORME 6 sec 10.5).

El veto es un bloque OPCIONAL de la regla con semantica GUARDIAN: sus condiciones se
combinan con veto_mode = any_blocks (cualquier condicion activa BLOQUEA la transicion
a FIRING). El veto NO dispara por si mismo y, mientras esta activo, IMPIDE proyectar
signal.*/alert.* -- ese comportamiento en RUNTIME lo implementa el Bloque 6; aqui se
declara la estructura y la semantica. Es la semantica guardian de v4, ahora DECLARADA
en el dato (veto_mode) en vez de cableada.

veto_mode es OBLIGATORIO y explicito (misma politica que los demas modos); v5.0 declara
solo 'any_blocks'. El contrato exige lo ESTRUCTURAL: al menos una condicion. Un veto
AUSENTE se modela NO poniendo el bloque en la regla (campo opcional de la raiz), nunca
con un veto de cero condiciones.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from source.rules.condition import Condition
from source.rules.vocab import VetoMode


class Veto(BaseModel):
    """Bloque guardian: 1..K condiciones combinadas con any_blocks, con id de nodo."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: UUID
    conditions: tuple[Condition, ...] = Field(min_length=1)
    veto_mode: VetoMode
