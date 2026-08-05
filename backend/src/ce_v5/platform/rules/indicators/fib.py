"""fib.* -- nucleo PURO de niveles Fibonacci (parametrizado por rango explicito).

Re-expresion pura en v5 del FibonacciEngine de v4 (paridad de RESULTADO/SEMANTICA,
sin engines). Dictamen Central P08b-13:
  - Nucleo PURO de 4 fuentes: fib.levels (grid de 17 niveles), fib.nearest_level,
    fib.level_pct, fib.direction. Parametrizado por (pivot_high, pivot_low)
    EXPLICITOS (D1).
  - DE DONDE SALE EL RANGO se decide luego (DEC-FIB-RANGO-DIFERIDO): stateless
    (swing.*) vs recursive (grid con histeresis L2). La funcion pura es identica
    para ambos: f(pivot_high, pivot_low, price) -> niveles + nearest + pct + dir.
  - near/bounce NO son fuentes: touch_pct (0.3%) es umbral de decision -> Rule;
    bounce = cercania + candle.shadow_signal -> Rule (D3, DEC-UMBRAL-LOCUS).
  - Pivotes = swing.* (D4); el DrawingStore (UI) queda fuera (D5).
  - Ratios/pcts Fibonacci son constantes DEFINITORIAS (viven en la fuente),
    Decimal EXACTO; los round() de v4 eran presentacion (D6). Sin AHP.

Niveles (paridad v4):
  Retrazados dentro (7):  0, 23.6, 38.2, 50, 61.8, 78.6, 100 (% del rango).
  Extensiones arriba (5): 127.2, 141.4, 161.8, 200, 261.8.
  Extensiones abajo (5): -27.2, -41.4, -61.8, -100, -161.8.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import Enum

from ce_v5.platform.rules.indicators.swing import (
    SWING_HIGH_SOURCE_ID,
    SWING_LOW_SOURCE_ID,
    SWING_STRENGTH_DEFAULT,
)
from ce_v5.platform.rules.rawclose import MARKET_CLOSE_SOURCE_ID
from source.datasource import (
    DataSourceDeclaration,
    HistoryUnit,
    MemoryModel,
    ParamSpec,
    Servibility,
    SharingScope,
    SourceType,
)
from source.families.market import Timeframe
from source.rules.scalar import ScalarType, ScalarValue

FIB_FORMULA_VERSION = 1

_PREC = 34
_HUNDRED = Decimal(100)

# Ratios dentro del rango (retrazados) y sus porcentajes (0=low, 100=high).
_INSIDE_RATIOS = (
    Decimal("0"),
    Decimal("0.236"),
    Decimal("0.382"),
    Decimal("0.5"),
    Decimal("0.618"),
    Decimal("0.786"),
    Decimal("1"),
)
_INSIDE_PCTS = (
    Decimal("0"),
    Decimal("23.6"),
    Decimal("38.2"),
    Decimal("50"),
    Decimal("61.8"),
    Decimal("78.6"),
    Decimal("100"),
)

# Ratios de extension fuera del rango (relativos al tamano del rango).
_EXT_RATIOS = (
    Decimal("0.272"),
    Decimal("0.414"),
    Decimal("0.618"),
    Decimal("1"),
    Decimal("1.618"),
)
_EXT_ABOVE_PCTS = (
    Decimal("127.2"),
    Decimal("141.4"),
    Decimal("161.8"),
    Decimal("200"),
    Decimal("261.8"),
)
_EXT_BELOW_PCTS = (
    Decimal("-27.2"),
    Decimal("-41.4"),
    Decimal("-61.8"),
    Decimal("-100"),
    Decimal("-161.8"),
)


class FibDirection(Enum):
    ABOVE = "above"
    BELOW = "below"


@dataclass(frozen=True)
class FibLevels:
    """Grid Fibonacci de 17 niveles para un rango [pivot_low, pivot_high].

    ordered_* recorren de mas abajo a mas arriba (abajo -> dentro -> arriba),
    emparejando cada nivel con su porcentaje (0=pivot_low, 100=pivot_high).
    """

    inside: tuple[Decimal, ...]  # 7 retrazados (pivot_low -> pivot_high)
    above: tuple[Decimal, ...]  # 5 extensiones por encima
    below: tuple[Decimal, ...]  # 5 extensiones por debajo
    ordered_levels: tuple[Decimal, ...]  # 17 niveles, abajo -> arriba
    ordered_pcts: tuple[Decimal, ...]  # 17 porcentajes, mismos indices


def _check_range(pivot_high: Decimal, pivot_low: Decimal) -> None:
    if pivot_high <= pivot_low:
        raise ValueError("pivot_high debe ser > pivot_low (rango > 0)")


def fib_levels(pivot_high: Decimal, pivot_low: Decimal) -> FibLevels:
    """Los 17 niveles Fibonacci del rango [pivot_low, pivot_high] (Decimal exacto)."""
    _check_range(pivot_high, pivot_low)
    with localcontext() as ctx:
        ctx.prec = _PREC
        ctx.rounding = ROUND_HALF_EVEN
        rng = pivot_high - pivot_low
        inside = tuple(pivot_low + r * rng for r in _INSIDE_RATIOS)
        above = tuple(pivot_high + r * rng for r in _EXT_RATIOS)
        below = tuple(pivot_low - r * rng for r in _EXT_RATIOS)
    ordered_levels = tuple(reversed(below)) + inside + above
    ordered_pcts = tuple(reversed(_EXT_BELOW_PCTS)) + _INSIDE_PCTS + _EXT_ABOVE_PCTS
    return FibLevels(
        inside=inside,
        above=above,
        below=below,
        ordered_levels=ordered_levels,
        ordered_pcts=ordered_pcts,
    )


def _nearest(levels: FibLevels, price: Decimal) -> tuple[Decimal, Decimal]:
    """(nivel_mas_cercano, pct) al precio. En empate, gana el de indice menor
    en el orden abajo->arriba (primer minimo, como v4)."""
    best_level = levels.ordered_levels[0]
    best_pct = levels.ordered_pcts[0]
    best_dist = abs(price - best_level)
    for lv, pct in zip(
        levels.ordered_levels[1:], levels.ordered_pcts[1:], strict=False
    ):
        dist = abs(price - lv)
        if dist < best_dist:
            best_dist = dist
            best_level = lv
            best_pct = pct
    return best_level, best_pct


def nearest_level(pivot_high: Decimal, pivot_low: Decimal, price: Decimal) -> Decimal:
    """Nivel Fibonacci mas cercano al precio."""
    levels = fib_levels(pivot_high, pivot_low)
    return _nearest(levels, price)[0]


def level_pct(pivot_high: Decimal, pivot_low: Decimal, price: Decimal) -> Decimal:
    """Porcentaje Fibonacci del nivel mas cercano al precio (0=low, 100=high;
    puede ser <0 o >100 en extensiones)."""
    levels = fib_levels(pivot_high, pivot_low)
    return _nearest(levels, price)[1]


def direction(pivot_high: Decimal, pivot_low: Decimal, price: Decimal) -> FibDirection:
    """ABOVE si price >= nivel mas cercano, BELOW si no (empate -> ABOVE)."""
    levels = fib_levels(pivot_high, pivot_low)
    near = _nearest(levels, price)[0]
    return FibDirection.ABOVE if price >= near else FibDirection.BELOW


# --- Rango con HISTERESIS (resuelve DEC-FIB-RANGO-DIFERIDO por la via recursive) ---
#
# El grid no se tiende sobre el swing de CADA barra: se tiende sobre un rango PEGAJOSO
# que solo se mueve cuando el swing lo supera con holgura. Sin esa histeresis, un pivote
# que oscila un tick redibujaria el grid entero barra a barra y "nivel Fibonacci mas
# cercano" dejaria de significar nada. Es la paridad de _maybe_retrace de v4.
#
# EL UMBRAL ES UN RATIO FIBONACCI, NO UN PARAMETRO. min_dist = 0.414 * rango, y 0.414 es
# _EXT_RATIOS[1] -- la MISMA constante que ya define la extension L2 del grid,
# reutilizada aqui en vez de reescrita. Es DEFINITORIO (D6): no viaja en la cache_key ni
# se puede override; si dejara de ser 0.414 seria otro indicador, no otra
# parametrizacion.


def fib_range_seed(swing_high: Decimal, swing_low: Decimal) -> tuple[Decimal, Decimal]:
    """El rango inicial: el primer par de pivotes tal cual, sin histeresis que aplicar.

    Devuelve (range_high, range_low). No valida que el rango sea positivo: un mercado
    plano da un rango degenerado (high == low) que el siguiente paso RESETEA y que la
    capa de salida trata con su fallback. Rechazarlo aqui obligaria al materializador a
    inventarse un rango, que es peor.
    """
    return (swing_high, swing_low)


def fib_range_step(
    range_high: Decimal,
    range_low: Decimal,
    swing_high: Decimal,
    swing_low: Decimal,
) -> tuple[Decimal, Decimal]:
    """Un paso de la histeresis: del rango en T-1 y los swing de T, al rango en T.

    Cada extremo se mueve SOLO si el swing lo supera en su direccion Y la supera por al
    menos min_dist = 0.414 * (tamano del rango vigente). Los dos extremos se deciden por
    separado y contra el MISMO min_dist, el del rango de entrada: usar un min_dist
    recalculado tras mover el primero haria que el resultado dependiera del orden en que
    se evaluan, y el fold dejaria de ser determinista.

    RESET si el rango de entrada es degenerado (rng <= 0): se adopta el swing de la
    barra tal cual. Sin esto, min_dist seria 0 o negativo y la histeresis dejaria de
    frenar nada -- o peor, un rango invertido se perpetuaria.

    Determinista y bajo el contexto pinneado del modulo, como fib_levels: replayar desde
    cualquier rango valido reproduce la cola identica bit a bit (ADR-007).
    """
    with localcontext() as ctx:
        ctx.prec = _PREC
        ctx.rounding = ROUND_HALF_EVEN
        rng = range_high - range_low
        if rng <= 0:
            return (swing_high, swing_low)
        min_dist = _EXT_RATIOS[1] * rng
        nuevo_high = (
            swing_high
            if swing_high > range_high and swing_high - range_high >= min_dist
            else range_high
        )
        nuevo_low = (
            swing_low
            if swing_low < range_low and range_low - swing_low >= min_dist
            else range_low
        )
    return (nuevo_high, nuevo_low)


class FibOutput(Enum):
    """Cual de las TRES salidas servibles del grid emite una fuente.

    Las tres comparten el MISMO rango con histeresis y el mismo grid; lo unico que las
    distingue es que proyeccion publican. Por eso el enum vive aqui, junto a la funcion
    pura, y no en el cableado: es una propiedad del indicador.

    DIRECTION entra en el LOTE 5 (P08b-D1-02 Q3) porque D1 ya cerro el carrier de serie
    no-Decimal: es CATEGORICA (STRING), no escalar numerica, y antes de D1 no habia
    forma de servirla por el mismo borde. fib.levels (la lista de 17) sigue DIFERIDA:
    es un VECTOR por barra, y eso no lo representa ningun ScalarType.
    """

    NEAREST_LEVEL = "nearest_level"
    LEVEL_PCT = "level_pct"
    DIRECTION = "direction"


def fib_output(
    range_high: Decimal,
    range_low: Decimal,
    price: Decimal,
    output: FibOutput,
) -> Decimal:
    """La salida `output` del grid tendido sobre [range_low, range_high] para `price`.

    BORDE DEL RANGO DEGENERADO (range_high <= range_low, mercado plano o rango aun sin
    decantar): fib_levels lo rechazaria con ValueError, pero un materializador no puede
    dejar un hueco a media serie. Se emite un fallback DEFINIDO -- nearest_level = el
    propio rango colapsado, level_pct = 0 -- que es la lectura honesta de la situacion:
    con un rango de tamano cero todos los niveles coinciden con el, y el precio esta al
    0% de un rango que no existe. Misma politica de degenerado que el fallback HLC3 del
    VWAP (P08b-INT-04): la funcion pura del grid se queda fiel (lanza), y la POLITICA
    vive en el borde que tiene que servir una serie.
    """
    if range_high <= range_low:
        return range_high if output is FibOutput.NEAREST_LEVEL else Decimal(0)
    if output is FibOutput.NEAREST_LEVEL:
        return nearest_level(range_high, range_low, price)
    return level_pct(range_high, range_low, price)


def fib_direction_token(range_high: Decimal, range_low: Decimal, price: Decimal) -> str:
    """La direccion del precio respecto del grid, como TOKEN de texto servible.

    Es la proyeccion CATEGORICA del mismo rango que alimenta a nearest_level/level_pct.
    El token NO se inventa aqui: es el .value del FibDirection que devuelve direction(),
    la funcion pura que ya existia -- "above" / "below". Un vocabulario nuevo seria un
    segundo sitio donde mantener los mismos dos nombres.

    BORDE DEL RANGO DEGENERADO (range_high <= range_low): mismo criterio que fib_output.
    Con el rango colapsado los 17 niveles coinciden en range_high, asi que el nivel mas
    cercano ES range_high y la direccion sale de compararlo con el precio -- exactamente
    lo que direction() calcularia si fib_levels admitiera un rango cero. No es una regla
    aparte: es la MISMA regla aplicada al grid degenerado que ya sirve nearest_level.
    """
    if range_high <= range_low:
        return (
            FibDirection.ABOVE.value
            if price >= range_high
            else FibDirection.BELOW.value
        )
    return direction(range_high, range_low, price).value


FIB_NEAREST_LEVEL_SOURCE_ID = "fib.nearest_level"
FIB_LEVEL_PCT_SOURCE_ID = "fib.level_pct"
FIB_DIRECTION_SOURCE_ID = "fib.direction"


def _fib_declaration(
    source_id: str, value_type: ScalarType = ScalarType.DECIMAL
) -> DataSourceDeclaration:
    """Declaracion comun de fib.nearest_level/level_pct/direction (P08b-FIB-01, LOTE 5).

    Las tres tienen la MISMA forma a proposito: salen del mismo rango con histeresis,
    con el mismo param y la misma cache_key. Lo unico que cambia es el source_id, el
    value_type -- DECIMAL las dos numericas, STRING la direccion -- y, en el cableado,
    que proyeccion emite su materializador.

    RECURSIVE: el rango de la barra T depende del de T-1 (histeresis), y como una barra
    que no lo mueve lo CONSERVA, la dependencia se remonta sin cota. Es la razon de ser
    del snapshot de la 0027.

    consumes las TRES series de las que se alimenta: swing.high y swing.low aportan los
    pivotes candidatos y market.close el precio que se situa en el grid. Es el primer
    DataSource del catalogo que consume dos fuentes DERIVADAS a la vez.

    strength es el UNICO parametro y no es propio: se HEREDA de swing.* y se propaga tal
    cual, porque el rango que decanta la histeresis depende de que pivotes le lleguen.
    Los ratios Fibonacci (el 0.414 de la histeresis incluido) NO son parametros: son
    definitorios (D6) y por eso no estan ni en params ni en la cache_key.
    """
    return DataSourceDeclaration(
        source_id=source_id,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        memory_model=MemoryModel.RECURSIVE,
        value_type=value_type,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        history_units=(HistoryUnit.BARS,),
        params=(
            ParamSpec(
                name="strength",
                value_type=ScalarType.INTEGER,
                default=ScalarValue(
                    scalar_type=ScalarType.INTEGER,
                    integer_value=SWING_STRENGTH_DEFAULT,
                ),
            ),
        ),
        overridable_params=("strength",),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=("exchange", "symbol", "timeframe", "strength"),
        consumes=(
            SWING_HIGH_SOURCE_ID,
            SWING_LOW_SOURCE_ID,
            MARKET_CLOSE_SOURCE_ID,
        ),
    )


def fib_nearest_level_declaration() -> DataSourceDeclaration:
    """fib.nearest_level: el nivel del grid mas cercano al cierre."""
    return _fib_declaration(FIB_NEAREST_LEVEL_SOURCE_ID)


def fib_level_pct_declaration() -> DataSourceDeclaration:
    """fib.level_pct: el porcentaje del nivel mas cercano (0=low, 100=high)."""
    return _fib_declaration(FIB_LEVEL_PCT_SOURCE_ID)


def fib_direction_declaration() -> DataSourceDeclaration:
    """fib.direction: si el cierre esta por encima o debajo del nivel mas cercano.

    CATEGORICA (STRING, tokens "above"/"below"): la primera fuente no-Decimal del
    catalogo vivo. RECURSIVE como sus dos hermanas -- se tiende sobre el MISMO rango con
    histeresis y comparte su snapshot (0027), no anade estado propio.
    """
    return _fib_declaration(FIB_DIRECTION_SOURCE_ID, ScalarType.STRING)


def declarations() -> tuple[DataSourceDeclaration, ...]:
    """Declaraciones que este modulo publica al catalogo vivo (discovery, MAT-02).

    Las dos escalares numericas mas fib.direction (categorica), que entra en el LOTE 5
    porque D1 ya cerro el carrier de serie no-Decimal (P08b-D1-02 Q3). fib.levels (lista
    de 17) sigue DIFERIDA: es un VECTOR por barra y ningun ScalarType lo representa; no
    exponerla es lo que la mantiene fuera del catalogo (aditividad), no un if.
    """
    return (
        fib_nearest_level_declaration(),
        fib_level_pct_declaration(),
        fib_direction_declaration(),
    )
