"""divergence.* -- deteccion de divergencias precio/RSI (fuente derivada de velas).

Re-expresion pura en v5 de la logica de divergencias de v4
(divergence_engine.py), como funcion pura sobre ventanas ya materializadas.
Paridad de RESULTADO/SEMANTICA con v4, NO de implementacion (v5 no tiene engines).

Convenciones (fieles a v4; go de Central tras el I-03 ADDENDUM):
  - Pivotes GEOMETRICOS de precio via swing.symmetric_pivots (DA-I03-9):
    maximos sobre la serie de HIGH, minimos sobre la serie de LOW.
  - RSI Wilder (rsi.wilder_rsi) leido EN la barra del pivote de precio
    (convencion 'i' de v4).
  - Se comparan pivotes consecutivos del mismo tipo (equivale a los
    "ultimos 2" de v4 aplicado sobre replay).
  - Desigualdad ESTRICTA en precio Y en RSI (si una empata, no hay divergencia).
  - Orden determinista de salida: (barra de confirmacion, prioridad de v4).

Esta fuente NO realiza aritmetica Decimal: solo COMPARA Decimals ya
producidos por fuentes bloqueadas (rsi.*, swing.*). La reproducibilidad
bit-a-bit la garantizan esas fuentes; aqui las comparaciones son exactas.

CINCO FUENTES SERVIBLES, RECURSIVE CON SNAPSHOT (LOTE 5, dictamen P08b-D1-05 OPCION A).
La deteccion es lo de arriba; lo de abajo es como se SIRVE. Dos piezas:

  - EL REPLAY (divergence_seed/divergence_replay). El emparejamiento mira el pivote
    ANTERIOR del mismo lado, y entre dos pivotes consecutivos pueden mediar cinco barras
    o quinientas: esa memoria NO tiene cota y por eso divergence.* es RECURSIVE y no
    WINDOWED. El estado que basta guardar es el ULTIMO PIVOTE DE CADA LADO (0028), y el
    replay desde el reproduce los mismos eventos que detectar sobre la historia entera
    (GATE ADR-007). detect_divergences NO es una segunda implementacion: es
    divergence_replay sembrado con el estado vacio.

  - LA PROYECCION DENSA (divergence_kind_token/divergence_flag). El fenomeno es DISPERSO
    y una fuente CONTINUOUS sirve un valor por barra: se publica 'none'/false donde no
    pasa nada y el evento se ancla en su barra `index` (la del pivote reciente del par).
    Dos eventos PUEDEN coincidir en una barra -- los maximos salen de HIGH y los minimos
    de LOW --; kind colapsa por la prioridad de v4, los cuatro flags no colapsan.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from ce_v5.platform.rules.indicators.rsi import RSI_SOURCE_ID, wilder_rsi
from ce_v5.platform.rules.indicators.swing import PivotKind, symmetric_pivots
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

DIVERGENCE_FORMULA_VERSION = 1

# Defaults de paridad v4.
_DEFAULT_STRENGTH = 2
_DEFAULT_RSI_PERIOD = 14


class DivergenceKind(Enum):
    REGULAR_BULL = "regular_bull"
    REGULAR_BEAR = "regular_bear"
    HIDDEN_BULL = "hidden_bull"
    HIDDEN_BEAR = "hidden_bear"


# Orden de prioridad de v4 (_DETECTION_ORDER): regular-bear, regular-bull,
# hidden-bear, hidden-bull. Se usa para ordenar la salida de forma estable.
_PRIORITY: dict[DivergenceKind, int] = {
    DivergenceKind.REGULAR_BEAR: 0,
    DivergenceKind.REGULAR_BULL: 1,
    DivergenceKind.HIDDEN_BEAR: 2,
    DivergenceKind.HIDDEN_BULL: 3,
}


@dataclass(frozen=True)
class Divergence:
    """Una divergencia confirmada entre un par de pivotes consecutivos."""

    kind: DivergenceKind
    index: int  # barra del pivote mas reciente (confirmacion)
    prev_index: int  # barra del pivote anterior del par
    price_prev: Decimal
    price_curr: Decimal
    rsi_prev: Decimal
    rsi_curr: Decimal


def _classify_pair(
    price_prev: Decimal,
    price_curr: Decimal,
    rsi_prev: Decimal,
    rsi_curr: Decimal,
    kind: PivotKind,
) -> DivergenceKind | None:
    """Clasifica un par de pivotes consecutivos del mismo tipo.

    kind == HIGH -> par de maximos -> divergencias BAJISTAS.
    kind == LOW  -> par de minimos -> divergencias ALCISTAS.
    Desigualdad estricta en precio Y en RSI; si no cumple, devuelve None.
    """
    if kind is PivotKind.HIGH:
        # Higher high + lower RSI high  -> regular bearish.
        if price_curr > price_prev and rsi_curr < rsi_prev:
            return DivergenceKind.REGULAR_BEAR
        # Lower high + higher RSI high  -> hidden bearish.
        if price_curr < price_prev and rsi_curr > rsi_prev:
            return DivergenceKind.HIDDEN_BEAR
        return None
    # kind is PivotKind.LOW
    # Lower low + higher RSI low  -> regular bullish.
    if price_curr < price_prev and rsi_curr > rsi_prev:
        return DivergenceKind.REGULAR_BULL
    # Higher low + lower RSI low  -> hidden bullish.
    if price_curr > price_prev and rsi_curr < rsi_prev:
        return DivergenceKind.HIDDEN_BULL
    return None


@dataclass(frozen=True, slots=True)
class PivotObservation:
    """Un pivote ya observado: su barra ancla, su precio y el RSI leido en ella.

    rsi es opcional PORQUE puede faltar sin que falte el pivote (warm-up de Wilder). El
    par que lo incluya no produce evento -- lo dice _fold_kind, igual que lo decia el
    `continue` de la version anterior --, pero la cadena avanza igual.

    index esta en las coordenadas de la SERIE QUE SE PASA al fold, no en un absoluto: es
    quien llama (detect_divergences sobre la historia entera, o el materializador sobre
    su tramo) el que sabe a que barra corresponde.
    """

    index: int
    price: Decimal
    rsi: Decimal | None


@dataclass(frozen=True, slots=True)
class DivergenceState:
    """El estado del replay: el ultimo pivote confirmado de CADA lado.

    Es TODO lo que hace falta para reanudar la deteccion, y es exactamente lo que
    persiste la 0028. Basta con uno por lado porque la formula solo empareja pivotes
    CONSECUTIVOS del mismo tipo: con el ultimo en la mano, cada pivote nuevo cierra su
    par y pasa a ser el ultimo.

    None en un lado = todavia no hay maximo (o minimo) confirmado. Es un hecho del
    estado, no un hueco: el primer pivote de ese lado no cierra ningun par.
    """

    last_high: PivotObservation | None = None
    last_low: PivotObservation | None = None


def divergence_seed() -> DivergenceState:
    """El estado ANTES de ver ninguna barra: sin pivote en ninguno de los dos lados.

    Es el arranque del bootstrap desde el origen. No hay nada que calcular -- a
    diferencia de rsi_seed, que tiene que promediar los primeros cambios --, porque la
    cadena de pivotes empieza literalmente vacia; se declara igualmente para que el
    arranque tenga UN nombre y no sea un DivergenceState() suelto en el cableado.
    """
    return DivergenceState()


def _pair_event(
    prev: PivotObservation, curr: PivotObservation, kind: PivotKind
) -> Divergence | None:
    """El evento de un par de pivotes consecutivos, o None si no hay divergencia.

    SIN RSI NO HAY PAR. Si a cualquiera de los dos pivotes le falta el RSI (warm-up de
    Wilder) no se emite nada: es el mismo `continue` de siempre, ahora con nombre. Lo
    que
    NO hace es cortar la cadena -- el pivote sigue siendo el ultimo de su lado y cerrara
    el par siguiente.
    """
    if prev.rsi is None or curr.rsi is None:
        return None
    dv_kind = _classify_pair(prev.price, curr.price, prev.rsi, curr.rsi, kind)
    if dv_kind is None:
        return None
    return Divergence(
        kind=dv_kind,
        index=curr.index,
        prev_index=prev.index,
        price_prev=prev.price,
        price_curr=curr.price,
        rsi_prev=prev.rsi,
        rsi_curr=curr.rsi,
    )


def _fold_kind(
    series: Sequence[Decimal],
    rsi: Sequence[Decimal | None],
    last: PivotObservation | None,
    strength: int,
    kind: PivotKind,
) -> tuple[PivotObservation | None, list[Divergence]]:
    """Recorre los pivotes de UN lado, emite sus eventos y devuelve el ultimo pivote.

    ES LA UNICA IMPLEMENTACION del emparejamiento: la usan tanto detect_divergences
    (sembrando con el estado vacio sobre la historia entera) como divergence_replay
    (sembrando con el estado del snapshot sobre un tramo). Si se forkeara, el replay
    podria apartarse de la formula sin que nadie lo notase -- justo lo que el GATE de
    ADR-007 existe para impedir.

    DEDUP POR ANCLA. Un pivote con ancla <= la del estado ya se contabilizo antes de
    este
    tramo: se salta. Es exacto y no aproximado porque los pivotes de un lado NO se
    solapan
    (symmetric_pivots salta al final de cada corrida) y su orden de confirmacion sigue
    al
    de su ancla, asi que "ancla posterior a la guardada" ES el conjunto de los
    pendientes.
    Gracias a eso el tramo puede empezar ANTES del ultimo pivote guardado -- que es lo
    que
    el materializador necesita para tener contexto izquierdo -- sin contar nada dos
    veces.
    """
    pivots = [p for p in symmetric_pivots(series, strength) if p.kind is kind]
    out: list[Divergence] = []
    for pivot in pivots:
        if last is not None and pivot.index <= last.index:
            continue
        curr = PivotObservation(
            index=pivot.index, price=pivot.value, rsi=rsi[pivot.index]
        )
        evento = None if last is None else _pair_event(last, curr, kind)
        if evento is not None:
            out.append(evento)
        last = curr
    return last, out


def divergence_replay(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    rsi: Sequence[Decimal | None],
    state: DivergenceState,
    strength: int = _DEFAULT_STRENGTH,
) -> tuple[DivergenceState, tuple[Divergence, ...]]:
    """Reanuda la deteccion sobre un tramo, desde el estado dado.

    highs/lows/rsi deben tener la misma longitud y estar alineadas por barra; los
    indices
    de `state` y los de los Divergence devueltos estan en ESAS coordenadas. rsi entra ya
    calculado (y no como closes) porque el replay lo obtiene de rsi.value, la fuente que
    ya sabe replayarse desde su propio snapshot: recomputarlo aqui abriria una segunda
    aritmetica de Wilder que podria apartarse de la primera.

    Devuelve (estado tras el tramo, eventos del tramo) con los eventos ordenados por
    (barra de confirmacion, prioridad de v4) -- el mismo orden total que
    detect_divergences, porque es literalmente el mismo sort.

    EL GATE (ADR-007): sembrar con divergence_seed() sobre la historia entera y sembrar
    con el estado de una barra intermedia sobre el tramo posterior dan los MISMOS
    eventos
    en el tramo comun. No es una propiedad que haya que mantener a mano: sale de que el
    estado ES el ultimo pivote por lado y de que el fold es el mismo.
    """
    if not (len(highs) == len(lows) == len(rsi)):
        raise ValueError("highs, lows y rsi deben tener la misma longitud")
    if strength < 1:
        raise ValueError("strength debe ser >= 1")

    last_high, found = _fold_kind(highs, rsi, state.last_high, strength, PivotKind.HIGH)
    last_low, low_events = _fold_kind(
        lows, rsi, state.last_low, strength, PivotKind.LOW
    )
    found += low_events

    found.sort(key=lambda d: (d.index, _PRIORITY[d.kind]))
    return (DivergenceState(last_high=last_high, last_low=last_low), tuple(found))


def detect_divergences(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    strength: int = _DEFAULT_STRENGTH,
    rsi_period: int = _DEFAULT_RSI_PERIOD,
) -> tuple[Divergence, ...]:
    """Detecta todas las divergencias confirmadas sobre las series dadas.

    highs/lows/closes deben tener la misma longitud y estar alineadas por barra.
    Devuelve una tupla ordenada por (barra de confirmacion, prioridad de v4).

    Es el REFERENTE del replay, no una implementacion paralela: calcula el RSI de la
    serie entera y delega en divergence_replay sembrado con el estado vacio. Por eso el
    GATE de ADR-007 puede compararse contra ella con sentido.
    """
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows y closes deben tener la misma longitud")
    if strength < 1:
        raise ValueError("strength debe ser >= 1")
    if rsi_period < 1:
        raise ValueError("rsi_period debe ser >= 1")

    rsi = wilder_rsi(closes, rsi_period)
    return divergence_replay(highs, lows, rsi, divergence_seed(), strength)[1]


# --- Cara SERVIBLE: proyeccion DENSA por barra de un fenomeno DISPERSO ----------------
#
# Los eventos son dispersos (solo hay uno en la barra de un pivote que cierre par), pero
# una fuente CONTINUOUS sirve un valor POR BARRA. La proyeccion es el puente: cada barra
# publica lo que le pasa A ELLA -- 'none' y cuatro false en la inmensa mayoria -- y el
# evento se ancla en la barra `index` del Divergence, que es la del pivote MAS RECIENTE
# del par (la de confirmacion). Anclarlo en prev_index pondria el hecho en una barra que
# ya paso hace mucho.

DIVERGENCE_KIND_SOURCE_ID = "divergence.kind"
DIVERGENCE_REGULAR_BULL_SOURCE_ID = "divergence.regular_bull"
DIVERGENCE_REGULAR_BEAR_SOURCE_ID = "divergence.regular_bear"
DIVERGENCE_HIDDEN_BULL_SOURCE_ID = "divergence.hidden_bull"
DIVERGENCE_HIDDEN_BEAR_SOURCE_ID = "divergence.hidden_bear"

# El token de "en esta barra no hay divergencia". NO es un DivergenceKind: es la
# AUSENCIA
# de uno, y por eso vive fuera del enum -- meterlo dentro obligaria a todo el que
# recorra
# los tipos de divergencia a acordarse de excluirlo. Es un valor SERVIBLE de pleno
# derecho: una regla puede pedir divergence.kind != 'none'.
DIVERGENCE_KIND_NONE = "none"


class DivergenceOutput(Enum):
    """Cual de las CINCO salidas servibles publica una fuente.

    Las cinco salen del MISMO recorrido de pivotes y del MISMO estado (0028); lo unico
    que las distingue es que proyeccion emiten. Por eso el enum vive aqui, junto a la
    funcion pura, y no en el cableado: es una propiedad del indicador. Mismo patron que
    FibOutput y MacdOutput.

    KIND es CATEGORICA (STRING) y colapsa la barra a UN token; las otras cuatro son
    BOOLEAN y son independientes entre si. Los cuatro valores de flag coinciden a
    proposito con los de DivergenceKind: son la MISMA nomenclatura, no dos vocabularios
    que haya que mantener sincronizados (_FLAG_KIND lo ata).
    """

    KIND = "kind"
    REGULAR_BULL = "regular_bull"
    REGULAR_BEAR = "regular_bear"
    HIDDEN_BULL = "hidden_bull"
    HIDDEN_BEAR = "hidden_bear"


# Que tipo de divergencia mira cada flag. KIND no esta porque no mira UNO: los mira
# todos.
_FLAG_KIND: dict[DivergenceOutput, DivergenceKind] = {
    DivergenceOutput.REGULAR_BULL: DivergenceKind.REGULAR_BULL,
    DivergenceOutput.REGULAR_BEAR: DivergenceKind.REGULAR_BEAR,
    DivergenceOutput.HIDDEN_BULL: DivergenceKind.HIDDEN_BULL,
    DivergenceOutput.HIDDEN_BEAR: DivergenceKind.HIDDEN_BEAR,
}


def divergence_kind_token(kinds: Collection[DivergenceKind]) -> str:
    """El token de divergence.kind en una barra: 'none', o el kind de MAYOR prioridad.

    DOS EVENTOS PUEDEN CAER EN LA MISMA BARRA, y no es una rareza teorica: los maximos
    salen de la serie de HIGH y los minimos de la de LOW, asi que una vela envolvente
    puede ser a la vez pivote de las dos y cerrar un par bajista Y uno alcista. Como
    mucho son dos (cada lado produce a lo sumo un evento por barra: _classify_pair
    devuelve UN kind).

    LA PRECEDENCIA NO SE INVENTA AQUI: es _PRIORITY, el _DETECTION_ORDER de v4 que ya
    ordena la salida de detect_divergences (regular-bear, regular-bull, hidden-bear,
    hidden-bull). Asi el token de una barra es SIEMPRE el primer evento que lista
    detect_divergences para ella, y las dos caras de la fuente cuentan la misma
    historia.
    Los CUATRO flags, en cambio, NO colapsan: en esa barra los dos que correspondan
    valen
    true a la vez. Quien necesite el detalle completo lee los flags; kind es el resumen.
    """
    if not kinds:
        return DIVERGENCE_KIND_NONE
    return min(kinds, key=lambda k: _PRIORITY[k]).value


def divergence_flag(
    kinds: Collection[DivergenceKind], output: DivergenceOutput
) -> bool:
    """Si en esta barra hay una divergencia del tipo que publica `output`.

    Independiente de los otros tres flags y de la precedencia de kind: un flag responde
    "paso ESTO", no "fue esto lo mas importante que paso".
    """
    kind = _FLAG_KIND.get(output)
    if kind is None:
        msg = (
            f"{output.value!r} no es una salida BOOLEAN de divergence.*: kind es el "
            "token categorico y se proyecta con divergence_kind_token."
        )
        raise ValueError(msg)
    return kind in kinds


_SOURCE_ID_BY_OUTPUT: dict[DivergenceOutput, str] = {
    DivergenceOutput.KIND: DIVERGENCE_KIND_SOURCE_ID,
    DivergenceOutput.REGULAR_BULL: DIVERGENCE_REGULAR_BULL_SOURCE_ID,
    DivergenceOutput.REGULAR_BEAR: DIVERGENCE_REGULAR_BEAR_SOURCE_ID,
    DivergenceOutput.HIDDEN_BULL: DIVERGENCE_HIDDEN_BULL_SOURCE_ID,
    DivergenceOutput.HIDDEN_BEAR: DIVERGENCE_HIDDEN_BEAR_SOURCE_ID,
}


def _divergence_declaration(output: DivergenceOutput) -> DataSourceDeclaration:
    """Declaracion comun de las cinco divergence.* (LOTE 5, dictamen P08b-D1-05).

    Las cinco tienen la MISMA forma a proposito: salen del mismo recorrido de pivotes,
    con los mismos dos params y la misma cache_key. Lo unico que cambia es el source_id,
    el value_type -- STRING el token, BOOLEAN los cuatro flags -- y, en el cableado, que
    proyeccion emite su materializador.

    RECURSIVE, y aqui esta la razon de ser de la 0028: un evento compara el pivote de
    hoy
    con el ANTERIOR DEL MISMO LADO, y entre dos pivotes consecutivos pueden mediar cinco
    barras o quinientas -- lo decide el mercado, no un parametro. Una ventana acotada
    perderia (o emparejaria mal, que es peor) todo evento cuyo pivote previo cayera
    antes
    de su inicio, y el fallo seria MUDO.

    CONTINUOUS pese a que el fenomeno es DISPERSO. La servibilidad no habla de con que
    frecuencia pasa algo, sino de si hay un valor POR BARRA: lo hay -- 'none' y cuatro
    false cuando no pasa nada --, y por eso value_at/previous_value tienen sentido sobre
    ella. Sporadic es para lo que no tiene serie, no para lo que tiene serie aburrida.

    consumes las DOS series de las que se alimenta de verdad: rsi.value -- el RSI que se
    lee EN la barra de cada pivote, y que NO se recalcula aqui sino que se pide a la
    fuente que ya sabe replayarse -- y market.close, la serie de velas de la que salen
    los
    pivotes geometricos. swing.* NO se consume: sus pivotes son los de la serie de
    CIERRES, y los de divergence son los de las series de HIGH y de LOW (convencion v4,
    DA-I03-9); declararla seria decir que se usa algo que no se usa.

    strength y rsi_period son los DOS parametros, los dos heredados y los dos en la
    cache_key: strength decide QUE pivotes hay (se hereda de swing, que define la fuerza
    simetrica) y rsi_period decide QUE RSI se lee en ellos (se hereda de rsi.value). Los
    umbrales de la divergencia no son parametros: la desigualdad es ESTRICTA y sin
    tolerancia, que es la convencion de paridad v4.
    """
    value_type = (
        ScalarType.STRING if output is DivergenceOutput.KIND else ScalarType.BOOLEAN
    )
    return DataSourceDeclaration(
        source_id=_SOURCE_ID_BY_OUTPUT[output],
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
                    integer_value=_DEFAULT_STRENGTH,
                ),
            ),
            ParamSpec(
                name="rsi_period",
                value_type=ScalarType.INTEGER,
                default=ScalarValue(
                    scalar_type=ScalarType.INTEGER,
                    integer_value=_DEFAULT_RSI_PERIOD,
                ),
            ),
        ),
        overridable_params=("strength", "rsi_period"),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=(
            "exchange",
            "symbol",
            "timeframe",
            "strength",
            "rsi_period",
        ),
        consumes=(RSI_SOURCE_ID, MARKET_CLOSE_SOURCE_ID),
    )


def divergence_kind_declaration() -> DataSourceDeclaration:
    """divergence.kind: el tipo de divergencia de la barra, o 'none' (STRING)."""
    return _divergence_declaration(DivergenceOutput.KIND)


def divergence_regular_bull_declaration() -> DataSourceDeclaration:
    """divergence.regular_bull: minimo mas bajo con RSI mas alto (BOOLEAN)."""
    return _divergence_declaration(DivergenceOutput.REGULAR_BULL)


def divergence_regular_bear_declaration() -> DataSourceDeclaration:
    """divergence.regular_bear: maximo mas alto con RSI mas bajo (BOOLEAN)."""
    return _divergence_declaration(DivergenceOutput.REGULAR_BEAR)


def divergence_hidden_bull_declaration() -> DataSourceDeclaration:
    """divergence.hidden_bull: minimo mas alto con RSI mas bajo (BOOLEAN)."""
    return _divergence_declaration(DivergenceOutput.HIDDEN_BULL)


def divergence_hidden_bear_declaration() -> DataSourceDeclaration:
    """divergence.hidden_bear: maximo mas bajo con RSI mas alto (BOOLEAN)."""
    return _divergence_declaration(DivergenceOutput.HIDDEN_BEAR)


def declarations() -> tuple[DataSourceDeclaration, ...]:
    """Declaraciones que este modulo publica al catalogo vivo (discovery, MAT-02).

    Las CINCO: el token categorico y los cuatro flags. Los cuatro flags no son una
    comodidad sobre kind -- son la unica forma de leer una barra en la que coinciden dos
    divergencias, porque kind la colapsa a una sola por precedencia.
    """
    return (
        divergence_kind_declaration(),
        divergence_regular_bull_declaration(),
        divergence_regular_bear_declaration(),
        divergence_hidden_bull_declaration(),
        divergence_hidden_bear_declaration(),
    )
