"""Valor escalar canonico del lenguaje de reglas (INFORME 6 sec 3 y 10.5).

Un valor escalar aparece en dos sitios: como PARAMETRO de una referencia a fuente
(period=14) y como CONSTANTE en un lado de una condicion (> 0.7, == 'no_trade',
== true). En ambos el valor debe tener un TIPO ESTABLE: la forma canonica y el hash
dependen de el, y '14' (texto) y 14 (entero) NO son el mismo valor.

POR QUE UN TIPO DECLARADO Y NO UNA UNION bool|int|Decimal|str. Con una union,
Pydantic elige el miembro por coercion y esa eleccion NO es estable: en construccion
se vio 14 convertirse en '14', y un decimal y un texto que "parece numero" son
indistinguibles al recargar desde JSON. Un valor cuyo tipo cambia en silencio es el
equivalente silencioso que INFORME 6 sec 10.5 prohibe. Por eso el tipo se DECLARA
(scalar_type) y el valor vive en su campo tipado; recargar desde JSON reconstruye el
MISMO tipo. Defecto hallado en construccion por Claude Code al verificar reference.py.

FLOAT PROHIBIDO. Los decimales van en Decimal o en su texto decimal, nunca float (un
float binario no representa 0.1 exacto; en la cadena de ejecucion, M5, es dinero). Un
bool no es un entero y un entero no es un bool: se rechazan cruzados.
"""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class ScalarType(StrEnum):
    """Tipo declarado de un valor escalar canonico."""

    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    STRING = "string"


class ScalarValue(BaseModel):
    """Valor escalar con su tipo DECLARADO y su dato en el campo tipado.

    Exactamente un campo de valor esta presente, y es el que corresponde a
    scalar_type; cualquier otra combinacion se rechaza en el borde. frozen para que
    sea hashable (estabilidad del hash canonico).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scalar_type: ScalarType
    integer_value: int | None = None
    decimal_value: Decimal | None = None
    boolean_value: bool | None = None
    string_value: str | None = None

    @field_validator("integer_value", mode="before")
    @classmethod
    def _entero_sin_coercion(cls, v: object) -> object:
        if isinstance(v, bool):
            msg = "integer_value: un bool no es un entero."
            raise ValueError(msg)
        if isinstance(v, float):
            msg = "integer_value: float prohibido; usa un entero."
            raise ValueError(msg)
        if isinstance(v, str):
            msg = "integer_value: se esperaba un entero, no texto."
            raise ValueError(msg)
        return v

    @field_validator("decimal_value", mode="before")
    @classmethod
    def _decimal_sin_float(cls, v: object) -> object:
        if isinstance(v, bool):
            msg = "decimal_value: un bool no es un decimal."
            raise ValueError(msg)
        if isinstance(v, float):
            msg = (
                "decimal_value: float prohibido; usa Decimal o su texto decimal "
                "('0.1'). Un float binario no representa 0.1 de forma exacta."
            )
            raise ValueError(msg)
        return v

    @field_validator("boolean_value", mode="before")
    @classmethod
    def _bool_estricto(cls, v: object) -> object:
        if v is not None and not isinstance(v, bool):
            msg = f"boolean_value: se esperaba un bool, no {type(v).__name__}."
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _un_solo_valor_segun_tipo(self) -> "ScalarValue":
        by_type = {
            ScalarType.INTEGER: self.integer_value,
            ScalarType.DECIMAL: self.decimal_value,
            ScalarType.BOOLEAN: self.boolean_value,
            ScalarType.STRING: self.string_value,
        }
        present = [t for t, value in by_type.items() if value is not None]
        if present != [self.scalar_type]:
            shown = [t.value for t in present]
            msg = (
                f"ScalarValue incoherente: scalar_type={self.scalar_type.value} exige "
                "exactamente su campo tipado presente y los demas ausentes; "
                f"presentes={shown}."
            )
            raise ValueError(msg)
        return self
