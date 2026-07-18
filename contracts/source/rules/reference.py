"""Referencia declarativa a un DataSource (ADR-008, INFORME 6 sec 12.1).

La Rule NO conoce observables directos: conoce FUENTES declarativas referenciadas
por su id canonico del catalogo (ADR-008). DataSourceRef es el atomo de
acoplamiento entre la estructura de la regla (P08) y el marco/catalogo de
DataSources (Bloque 2 y el catalogo posterior de I-02). El id es un identificador
tecnico estable; el display-name y su traduccion viven en el catalogo del manifest,
no aqui (ADR-016).

PARAMETROS NOMBRADOS (INFORME 6 sec 9.3; deuda D-E2.1 de v4). En v4, pedir
rsi(period=7) ejecutaba rsi(14) por defecto SIN AVISO. En v5 los parametros van
NOMBRADOS y explicitos, con valor escalar de tipo DECLARADO (ScalarValue). Que el
parametro EXISTA y este EN RANGO lo comprueba el validador semantico contra el
catalogo (Bloque 3); aqui el contrato fija la FORMA para que la forma canonica y el
hash sean estables (INFORME 6 sec 10.5): nombres canonicos, valores escalares
deterministas y ORDEN ESTABLE.

ORDEN CANONICO EN EL CONTRATO. La persistencia no acepta equivalentes (INFORME 6
sec 10.5): dos referencias a la misma fuente con los mismos parametros en distinto
orden son la MISMA referencia y deben dar el MISMO hash. Por eso los parametros se
guardan como una tupla ORDENADA por nombre y sin nombres repetidos, y el contrato
lo EXIGE en el borde: una referencia con parametros desordenados o repetidos se
rechaza (el normalizador del Bloque 3 los ordena antes de construir la referencia).
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from source.rules.scalar import ScalarValue

# id canonico del catalogo: segmentos snake_case separados por punto, minimo dos
# (dominio.campo). Ejemplos: market.close, rsi.value, footprint.absorption_ratio.
SOURCE_ID_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$"

# nombre de parametro: snake_case. Ejemplos: period, length.
PARAM_NAME_PATTERN = r"^[a-z][a-z0-9_]*$"


class DataSourceParam(BaseModel):
    """Un parametro nombrado de una referencia a fuente (INFORME 6 sec 9.3).

    El valor es un ScalarValue: tipo declarado y dato en su campo tipado, para que el
    tipo no cambie en silencio y el hash canonico sea estable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=PARAM_NAME_PATTERN)
    value: ScalarValue


class DataSourceRef(BaseModel):
    """Referencia a un DataSource por id canonico + parametros nombrados (ADR-008).

    Forma canonica: los parametros van ORDENADOS por nombre y sin repetir. El
    contrato lo exige para que la misma referencia produzca siempre el mismo hash.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(pattern=SOURCE_ID_PATTERN)
    params: tuple[DataSourceParam, ...] = ()

    @model_validator(mode="after")
    def _parametros_en_forma_canonica(self) -> "DataSourceRef":
        names = [p.name for p in self.params]
        if len(names) != len(set(names)):
            msg = f"parametros con nombre repetido en {self.source_id!r}: {names}."
            raise ValueError(msg)
        if names != sorted(names):
            msg = (
                f"parametros no canonicos en {self.source_id!r}: deben ir ordenados "
                f"por nombre. Recibido {names}, esperado {sorted(names)}. El "
                "normalizador (Bloque 3) los ordena antes de persistir."
            )
            raise ValueError(msg)
        return self
