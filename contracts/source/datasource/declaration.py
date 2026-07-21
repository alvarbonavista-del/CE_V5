"""Marco declarativo de DataSource (ADR-008, INFORME 6 sec 12).

Una Rule NO conoce observables: conoce FUENTES declarativas por id (ADR-008). Este es
el MARCO GENERAL de declaracion de una DataSource, disenado para sostener el catalogo
completo que disena I-02, aunque P08 solo lo demuestre con el precio de cierre crudo.
Soporta POR DISENO:
- fuentes CONTINUAS (value_at/average/change) y ESPORADICAS (is_active/elapsed_since):
  campo servibility.
- unidad de historia declarada por la PROPIA fuente (bars/events/time/ticks), no fijada
  a velas: campo history_units.
- shared_evaluation / sharing_scope / cache_key_schema declarados por la fuente; el
  motor consume la clave SIN conocer el tipo (INFORME 6 sec 12.2).
- fuentes DERIVADAS (grafo/DAG): campo consumes con los source_id de sus insumos
  (ADR-008; el reproceso aguas abajo es ADR-007). El marco puede DECLARARLAS y
  encadenarlas; construir las fuentes derivadas concretas es catalogo posterior (I-02).

El catalogo CONCRETO (indicadores, footprint, orderflow...) NO se construye aqui: este
contrato solo fija la FORMA de una declaracion.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from source.rules.scalar import ScalarType, ScalarValue

# id canonico de DataSource: misma forma que DataSourceRef.source_id
# (source.rules.reference); dominio.campo en snake_case.
DATASOURCE_ID_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$"
# token de contexto: misma forma que Group.evaluation_context.
CONTEXT_TOKEN_PATTERN = r"^[a-z0-9][a-z0-9_]{0,31}$"
PARAM_NAME_PATTERN = r"^[a-z][a-z0-9_]*$"

SourceId = Annotated[str, Field(pattern=DATASOURCE_ID_PATTERN)]
ContextToken = Annotated[str, Field(pattern=CONTEXT_TOKEN_PATTERN)]


class SourceType(StrEnum):
    """Tipo de fuente (INFORME 6 sec 12.1). v5.0 solo OBSERVABLE."""

    OBSERVABLE = "observable"


class Servibility(StrEnum):
    """Como se sirve una fuente (INFORME 6 sec 12.2/12.4).

    CONTINUOUS: value_at/previous_value/average/change. SPORADIC: is_active/
    elapsed_since (vigencia de eventos). NON_SERVIBLE: se calcula pero NO se combina en
    reglas (el validador del Bloque 3 la rechaza como termino).
    """

    CONTINUOUS = "continuous"
    SPORADIC = "sporadic"
    NON_SERVIBLE = "non_servible"


class MemoryModel(StrEnum):
    """Como depende el valor de una fuente de la HISTORIA (CA-P08-08, firmada).

    Es lo que decide si una CORRECCION de vela se puede propagar por VENTANA acotada o
    no. No es una etiqueta descriptiva: es el discriminante de correccion.

    POINT_LOCAL: el valor de la barra T depende SOLO del dato de esa barra
    (market.close y demas campos crudos de la vela). Corregir T invalida un numero
    ACOTADO de evaluaciones -- las que miran T dentro de su ventana de history_bars --,
    asi que recalcular esa ventana basta y el reproceso es finito y barato.

    RECURSIVE: el valor de la barra T depende de su propio valor en T-1 (EMA, RSI, MACD,
    y toda media suavizada). Corregir T contamina TODOS los valores posteriores hasta el
    presente: no hay ventana acotada que valga, y recalcular "unas cuantas barras" daria
    un numero SILENCIOSAMENTE INCORRECTO, que es peor que no recalcular.

    INTEGRATOR: el valor acumula desde un origen (CVD y demas integradores). Misma
    conclusion que RECURSIVE por otra razon: el acumulado arrastra el error sin fin.

    En v5.0 el motor SOLO propaga correcciones a fuentes POINT_LOCAL; RECURSIVE e
    INTEGRATOR quedan declaradas y NO-CONFORMES para correccion (a P08b/P08c). El enum
    se cierra entero AHORA -- no solo el valor que v5.0 usa -- porque una fuente
    recursiva declarada point-local por omision propagaria correcciones mal: el valor
    tiene que ser EXPLICITO y por eso el campo no lleva default.
    """

    POINT_LOCAL = "point_local"
    RECURSIVE = "recursive"
    INTEGRATOR = "integrator"


class HistoryUnit(StrEnum):
    """Unidad de historia que declara la fuente (INFORME 6 sec 10.9).

    No fijada a velas: bars / events / time / ticks.
    """

    BARS = "bars"
    EVENTS = "events"
    TIME = "time"
    TICKS = "ticks"


class SharingScope(StrEnum):
    """Con quien se comparte la evaluacion de una fuente (INFORME 6 sec 12.2)."""

    PUBLIC_CROSS_TENANT = "public_cross_tenant"
    TENANT_PRIVATE = "tenant_private"
    USER_PRIVATE = "user_private"
    COMPONENT_PRIVATE = "component_private"


class ParamSpec(BaseModel):
    """Parametro declarado de una fuente: nombre, tipo, default (INFORME 6 sec 12.2)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=PARAM_NAME_PATTERN)
    value_type: ScalarType
    default: ScalarValue | None = None


class DataSourceDeclaration(BaseModel):
    """Declaracion GENERAL de una DataSource (ADR-008, INFORME 6 sec 12.2).

    source_type/servibility/value_type describen QUE es y como se sirve.

    memory_model dice como depende de la HISTORIA y es OBLIGATORIO sin default
    (CA-P08-08): de el depende si una correccion de vela se puede propagar por ventana
    acotada o si el motor debe abstenerse.

    history_units dice en que unidad se mira su historia. shared_evaluation,
    sharing_scope y cache_key_schema gobiernan la evaluacion compartida (el motor usa
    la clave sin conocer el tipo). consumes lista los source_id de los que DERIVA (DAG);
    vacio si es base. Los HARD CAPS y la validacion semantica contra la Rule son del
    Bloque 3.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: SourceId
    source_type: SourceType
    servibility: Servibility
    memory_model: MemoryModel
    value_type: ScalarType
    evaluation_contexts: tuple[ContextToken, ...] = Field(min_length=1)
    history_units: tuple[HistoryUnit, ...] = Field(min_length=1)
    params: tuple[ParamSpec, ...] = ()
    shared_evaluation: bool
    sharing_scope: SharingScope
    cache_key_schema: tuple[str, ...] = Field(min_length=1)
    consumes: tuple[SourceId, ...] = ()
    version: int = Field(default=1, ge=1)
    display_name_key: str | None = None

    @model_validator(mode="after")
    def _sin_autoconsumo(self) -> "DataSourceDeclaration":
        if self.source_id in self.consumes:
            msg = "una fuente no puede consumirse a si misma (grafo sin ciclos)."
            raise ValueError(msg)
        return self
