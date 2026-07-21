"""Termino: un lado de una condicion (INFORME 6 sec 3, 10.5, 10.9).

Una condicion compara dos terminos. Un termino es UNA de dos cosas:
- una CONSTANTE (un ScalarValue: > 0.7, == 'no_trade', == true), o
- un ACCESO A FUENTE: una referencia a DataSource, opcionalmente envuelta en una
  funcion canonica (value_at/average/change sobre fuentes continuas;
  is_active/elapsed_since sobre esporadicas). Sin funcion, el termino es el VALOR
  ACTUAL de la fuente.

El contrato fija la FORMA (la arity de cada funcion: cuales toman offset N y cuales
no). La SEMANTICA dirigida por catalogo -- que la fuente exista, que sea continua o
esporadica segun la funcion, que la unidad de historia este soportada, que el offset
este en rango -- la valida el Bloque 3 (INFORME 6 sec 12.4). Aqui solo se garantiza
que la estructura del termino es coherente.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from source.rules.reference import DataSourceRef
from source.rules.scalar import ScalarValue
from source.rules.vocab import OFFSET_FUNCTIONS, CanonicalFunction


class TermKind(StrEnum):
    """Que es un termino: una constante o un acceso a fuente."""

    CONSTANT = "constant"
    SOURCE = "source"


class SourceTerm(BaseModel):
    """Acceso a una fuente, con funcion canonica opcional (INFORME 6 sec 10.9).

    function None = valor ACTUAL de la fuente (acceso directo). Con funcion:
    value_at/previous_value/average/change EXIGEN un offset N (en la unidad de
    historia que declara la fuente); is_active/elapsed_since NO admiten offset
    (operan sobre la vigencia de una fuente esporadica). El contrato exige la arity
    correcta; que la funcion CASE con el tipo de fuente lo valida el Bloque 3.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: DataSourceRef
    function: CanonicalFunction | None = None
    offset: int | None = None

    @model_validator(mode="after")
    def _arity_coherente(self) -> "SourceTerm":
        if self.function is None:
            if self.offset is not None:
                msg = "acceso directo (sin funcion) no admite offset."
                raise ValueError(msg)
            return self
        if self.function in OFFSET_FUNCTIONS:
            if self.offset is None:
                msg = (
                    f"la funcion {self.function.value} exige un offset N "
                    "(en la unidad de historia de la fuente)."
                )
                raise ValueError(msg)
            if self.offset < 0:
                msg = f"offset negativo no valido: {self.offset}."
                raise ValueError(msg)
            return self
        # vocab.py garantiza en import que OFFSET_FUNCTIONS y NO_OFFSET_FUNCTIONS
        # particionan CanonicalFunction: si la funcion no toma offset, NO lo admite.
        if self.offset is not None:
            msg = f"la funcion {self.function.value} no admite offset."
            raise ValueError(msg)
        return self


class Term(BaseModel):
    """Un lado de una condicion: constante o acceso a fuente (exactamente uno)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    term_kind: TermKind
    constant: ScalarValue | None = None
    source: SourceTerm | None = None

    @model_validator(mode="after")
    def _un_solo_termino_segun_tipo(self) -> "Term":
        by_kind = {
            TermKind.CONSTANT: self.constant,
            TermKind.SOURCE: self.source,
        }
        present = [k for k, v in by_kind.items() if v is not None]
        if present != [self.term_kind]:
            shown = [k.value for k in present]
            msg = (
                f"Term incoherente: term_kind={self.term_kind.value} exige "
                f"exactamente su campo presente y el otro ausente; presentes={shown}."
            )
            raise ValueError(msg)
        return self
