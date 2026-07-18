"""Feature: un grupo de condiciones combinadas (INFORME 6 sec 10.3, 10.5).

Una feature agrupa 1..K condiciones atomicas y declara COMO se combinan
(combine_mode: all = todas, any = alguna). Es el nivel donde el complexity budget
aplica el tope de K condiciones y el maximo de 3 fuentes distintas por feature
(ADR-015): esos HARD CAPS de plataforma los valida el Bloque 3 ANTES de
persistir/compilar, NO el contrato, para que vivan en un unico sitio (el validador) y
no se dupliquen. El contrato solo exige lo ESTRUCTURAL: al menos una condicion.

ORDEN CANONICO. El orden estable de las condiciones (INFORME 6 sec 10.5) lo fija el
normalizador (micro-paso 1.11) sobre el arbol completo por CONTENIDO, no el contrato:
ordenar por contenido exige la clave canonica que construye el normalizador. El
contrato acepta el orden que reciba; nada se persiste sin pasar por el normalizador.

MODO EXPLICITO Y OBLIGATORIO. Sin ANDs implicitos (el error de v4): combine_mode es un
campo OBLIGATORIO del contrato, SIN default. Asi la forma persistida SIEMPRE lo declara
y ninguna opcion de serializacion (exclude_defaults / exclude_unset) puede dejarlo
tacito -- una trampa real que la revision de construccion detecto. El valor por defecto
conceptual 'all' (INFORME 6 sec 10.5) lo aplica el NORMALIZADOR cuando la superficie
(chatbot/UI) omite el modo; el contrato canonico nunca lo asume. Misma politica en
todos los niveles con modo (grupo, regla, veto).
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from source.rules.condition import Condition
from source.rules.vocab import CombineMode


class Feature(BaseModel):
    """1..K condiciones con su modo de combinacion (obligatorio) y su id de nodo."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: UUID
    conditions: tuple[Condition, ...] = Field(min_length=1)
    combine_mode: CombineMode
