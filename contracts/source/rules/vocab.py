"""Vocabulario canonico del lenguaje de reglas (ADR-015, ADR-016, INFORME 6 sec 10).

El lenguaje de reglas tiene su CANON en ingles como IDENTIFICADORES internos
estables (ADR-016): estos enum son la gramatica objetivo del validador, el
normalizador y el compilador; NO son texto de interfaz (la UI los traduce por
i18n). Aqui vive el vocabulario canonico del lenguaje: como se combina y cuando se
dispara una regla, los operadores de comparacion, y las FUNCIONES canonicas que se
aplican a una fuente. Las UNIDADES de historia (bars/events/time/ticks) NO viven
aqui: las declara cada DataSource en su catalogo (marco del Bloque 2), porque
dependen de la fuente, no del lenguaje.

Cada enum es un conjunto CERRADO y ampliable (ADR-005): anadir un valor es
compatible, quitarlo no. Solo se declaran los valores con USO REAL en v5.0; los
demas entran con la pieza que los produce (regla 5.11), como market.py declaro solo
spot/candles.
"""

from enum import StrEnum


class TriggerPolicy(StrEnum):
    """Que dispara la evaluacion de una regla (ADR-015, INFORME 6 sec 10.4).

    v5.0 implementa candle_close (dominante) y manual (evaluacion bajo demanda y
    pruebas). event_arrival, schedule y mixed estan fijados por ADR-015 pero NO se
    declaran aqui hasta que exista su productor/manejador (regla 5.11; ADR-017 fija
    la implementacion v5.0 minima viable: solo candle_close activo). El cierre de
    vela es UN trigger, no EL modelo: por eso es un campo declarado, no un supuesto
    cableado.
    """

    CANDLE_CLOSE = "candle_close"
    MANUAL = "manual"


class CombineMode(StrEnum):
    """Como se combinan los hijos de un nivel: features en un grupo, condiciones en
    una feature (INFORME 6 sec 10.5).

    Sin ANDs implicitos (el error de v4): el modo se ESCRIBE en la forma canonica,
    con default explicito 'all'. 'all' = todos deben cumplirse; 'any' = basta uno.
    """

    ALL = "all"
    ANY = "any"


class RuleCombineMode(StrEnum):
    """Como se combinan los grupos de una regla (INFORME 6 sec 10.5, INFORME 4).

    'all'/'any' como CombineMode; 'all_within_window' exige que todos los grupos
    disparen dentro de una ventana. El tamano N de la ventana es un CAMPO del
    modelo, no un valor de enum (un enum no se parametriza); la ventana se ancla al
    evaluation_context declarado del grupo.
    """

    ALL = "all"
    ANY = "any"
    ALL_WITHIN_WINDOW = "all_within_window"


class VetoMode(StrEnum):
    """Semantica del bloque veto guardian (ADR-015, INFORME 6 sec 10.5).

    v5.0 declara SOLO 'any_blocks' (default y unico): cualquier condicion del veto
    activa BLOQUEA la transicion a FIRING; el veto no dispara por si mismo. Es la
    semantica guardian de v4, ahora DECLARADA en el dato en vez de cableada.
    """

    ANY_BLOCKS = "any_blocks"


class ComparisonOperator(StrEnum):
    """Operador de una condicion atomica (INFORME 6 sec 3).

    Una condicion en forma canonica es UNA comparacion. Conjunto cerrado: sin
    operadores logicos aqui (la combinacion Y/O/NO se expresa por los modos de
    combinacion y la estructura, no dentro de una condicion atomica).
    """

    GT = ">"
    GE = ">="
    LT = "<"
    LE = "<="
    EQ = "=="
    NE = "!="


class CanonicalFunction(StrEnum):
    """Funciones canonicas neutrales del lenguaje (ADR-015, INFORME 6 sec 10.9).

    Mercado-agnosticas: se aplican a CUALQUIER DataSource, no solo a velas. Las de v4
    (hace/media/cambio/activa/velas_desde) quedan como referencia conceptual; el
    canon v5 es neutral. Dos grupos por ARITY:
    - con offset N (miran hacia atras en una fuente CONTINUA): value_at,
      previous_value, average, change. La unidad de N (bars/events/time/ticks) la
      declara la fuente, no el lenguaje.
    - sin offset (operan sobre la VIGENCIA de una fuente ESPORADICA): is_active,
      elapsed_since.
    Que una funcion CASE con el tipo de fuente lo valida el Bloque 3 contra el
    catalogo (INFORME 6 sec 12.4); aqui solo se declara la arity. Las matematicas
    neutrales (abs/round/max/min) se modelan con la expresion, no aqui.
    """

    VALUE_AT = "value_at"
    PREVIOUS_VALUE = "previous_value"
    AVERAGE = "average"
    CHANGE = "change"
    IS_ACTIVE = "is_active"
    ELAPSED_SINCE = "elapsed_since"


# Arity de cada funcion canonica (INFORME 6 sec 10.9). PARTICION de CanonicalFunction:
# toda funcion esta EXACTAMENTE en uno de los dos conjuntos. El termino (term.py) usa
# esta clasificacion para exigir o prohibir el offset; si se anade una funcion nueva
# sin clasificarla, el termino falla fuerte (no la trata en silencio).
OFFSET_FUNCTIONS: frozenset[CanonicalFunction] = frozenset(
    {
        CanonicalFunction.VALUE_AT,
        CanonicalFunction.PREVIOUS_VALUE,
        CanonicalFunction.AVERAGE,
        CanonicalFunction.CHANGE,
    }
)
NO_OFFSET_FUNCTIONS: frozenset[CanonicalFunction] = frozenset(
    {
        CanonicalFunction.IS_ACTIVE,
        CanonicalFunction.ELAPSED_SINCE,
    }
)


# Guardia de arranque (adoptada de la revision de Claude Code): la clasificacion de
# arity DEBE particionar CanonicalFunction. Si se anade una funcion y no se clasifica,
# esto FALLA AL IMPORTAR el modulo -- en cualquier test y en CI -- y no solo cuando
# alguien construya un termino con ella. No es un assert (que -O podria eliminar): es
# una comprobacion explicita que rompe fuerte.
if (OFFSET_FUNCTIONS | NO_OFFSET_FUNCTIONS) != set(CanonicalFunction) or (
    OFFSET_FUNCTIONS & NO_OFFSET_FUNCTIONS
):
    _sin_clasificar = sorted(
        f.value for f in set(CanonicalFunction) - OFFSET_FUNCTIONS - NO_OFFSET_FUNCTIONS
    )
    _en_ambos = sorted(f.value for f in OFFSET_FUNCTIONS & NO_OFFSET_FUNCTIONS)
    msg = (
        "clasificacion de arity de CanonicalFunction rota: toda funcion debe estar en "
        "OFFSET_FUNCTIONS o NO_OFFSET_FUNCTIONS, y en uno solo. "
        f"Sin clasificar: {_sin_clasificar}; en ambos: {_en_ambos}."
    )
    raise RuntimeError(msg)
