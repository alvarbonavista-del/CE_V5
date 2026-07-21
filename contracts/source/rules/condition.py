"""Condicion atomica: una comparacion entre dos terminos (INFORME 6 sec 10.5).

Es el ATOMO del lenguaje: exactamente UNA comparacion (izquierda operador derecha).
La combinacion Y/O/NO NO vive dentro de una condicion; se expresa por los modos de
combinacion de la feature/grupo y por la estructura (INFORME 6 sec 10.5). Cada lado
es un Term (una constante o un acceso a fuente); comparar fuente con constante o
fuente con fuente es valido. Que los tipos comparados sean coherentes (no comparar
texto con numero) y que las fuentes existan lo valida el Bloque 3 contra el catalogo.

NODE_ID ESTABLE. Cada nodo del arbol de una regla lleva un id ESTABLE que sobrevive a
las ediciones (INFORME 6 sec 10.5): sirve para referenciar el nodo desde el
EvaluationResult y el historial (explicabilidad) y para la composicion futura
(subarboles referenciables, sec 10.8). Es un identificador ASIGNADO (UUID), no
derivado del contenido: por eso sobrevive a que el usuario edite otras partes. NO
forma parte del hash canonico: el hash (micro-paso del normalizador) se calcula sobre
la ESTRUCTURA y la SEMANTICA -- operador, terminos, modos, orden -- para que dos
reglas estructuralmente identicas den el MISMO hash aunque sus node_id asignados
difieran (la propiedad que habilita dedup y comparacion).
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from source.rules.term import Term
from source.rules.vocab import ComparisonOperator


class Condition(BaseModel):
    """Una comparacion atomica entre dos terminos, con id de nodo estable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: UUID
    left: Term
    operator: ComparisonOperator
    right: Term
