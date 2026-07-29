"""Volume profile desde footprint (vp.*, P08c): declaraciones ADR-008 + nucleo puro.

Fuente DERIVADA de market.footprint (DEC-VP-INPUT-01): usa el volumen EXACTO por nivel
de precio del footprint (no la aproximacion OHLC de v4) y BINA el perfil en bins de
ancho declarado para robustez de POC/VA (paridad SEMANTICA de v4: el binado era diseno,
no limitacion). Entrega las DECLARACIONES ADR-008 de las salidas servibles
(vp.poc, vp.vah, vp.val), el nucleo DETERMINISTA que las calcula -- POC, Value Area
70%, VAH, VAL -- y los NODOS de volumen HVN/LVN (detectores con umbral, pre-registro
AHP firmado). POC/VA y HVN/LVN salen del MISMO perfil binado (_build_bins): no se
perfila dos veces (precision del CSA).

LAS DECLARACIONES NO SE CABLEAN AUN EN EL CATALOGO VIVO. Como market.footprint
(rawfootprint.py), solo se ANADEN aqui: su registro en el catalogo del worker y su
materializador viven en el paso del SourceMaterializer y en CE-14 (cableado vivo). Son
WINDOWED: el valor de la barra T sale de una ventana ACOTADA de footprints, por lo que
en v5.0 quedan NO-CONFORMES para correccion (como fija el enum MemoryModel).

CONVENCION DE PRECIO DEL BIN (fijada explicita): el precio representativo de un bin es
su CENTRO, min_price + (idx + 0.5) * bin_width. POC/VAH/VAL se reportan como el centro
de su bin; un bin no es un tick real, y el centro es su posicion canonica.

DETERMINISMO: la SELECCION de bins (POC y expansion de VA) es exacta -- indices enteros
y sumas de Decimal, sin division --; solo los PRECIOS reportados usan la division del
ancho de bin, bajo el contexto Decimal por defecto (mismo input -> mismo precio). Los
desempates son deterministas: POC = bin de MENOR indice ante empate de volumen; la
expansion de VA prefiere el lado SUPERIOR ante empate (paridad v4).

ALCANCE: funcion PURA (sin DB, sin reloj). Asume una ventana de footprints del MISMO
flujo ya CERRADOS; la degradacion por is_complete y la lectura por ventana son de la
capa de materializacion, no de aqui.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from ce_v5.platform.rules.rawfootprint import MARKET_FOOTPRINT_SOURCE_ID
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

if TYPE_CHECKING:
    from collections.abc import Sequence

    from source.families.footprint import FootprintPayload

# Semilla [PARIDAD v4]: v4 uso 50 bins (tick = rango/50). Es PARAMETRO, no constante;
# entra en la cache_key + formula_version de la declaracion (S4). Su calibracion AHP es
# OPCIONAL (D-VP-3), no obligatoria en v5.0.
DEFAULT_BIN_COUNT = 50
# Value Area estandar: 70% del volumen en torno al POC. Es DEFINICION, no un tunable.
DEFAULT_VALUE_AREA_PCT = Decimal("0.70")

VP_POC_SOURCE_ID = "vp.poc"
VP_VAH_SOURCE_ID = "vp.vah"
VP_VAL_SOURCE_ID = "vp.val"

# Dimensiones de la cache_key de vp.*: las cuatro del footprint del que deriva MAS
# bin_count, porque dos perfiles con distinto numero de bins son HECHOS DISTINTOS (POC y
# VA cambian). value_area_pct NO entra: es DEFINICION fija (70%), no un parametro.
VP_CACHE_KEY_SCHEMA: tuple[str, ...] = (
    "exchange",
    "symbol",
    "market_type",
    "timeframe",
    "bin_count",
)


def _vp_declaration(source_id: str) -> DataSourceDeclaration:
    """Declaracion ADR-008 comun de una salida servible del volume profile (vp.*).

    Las tres salidas (POC, VAH, VAL) comparten forma y solo difieren en source_id: todas
    son precios (DECIMAL) derivados de la MISMA ventana de footprint, con la misma
    memoria y la misma clave. WINDOWED: el valor de T sale de una ventana ACOTADA de
    footprints [T-w+1, T], no de su propio valor anterior; por eso en v5.0 queda
    NO-CONFORME para correccion (como fija el enum), pero es recomputable por ventana
    el dia que el cableado vivo lo soporte (CE-14). bin_count se declara como
    PARAMETRO (con su default de paridad v4); value_area_pct no, por ser definicion
    fija.
    """
    return DataSourceDeclaration(
        source_id=source_id,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        memory_model=MemoryModel.WINDOWED,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        history_units=(HistoryUnit.BARS,),
        params=(
            ParamSpec(
                name="bin_count",
                value_type=ScalarType.INTEGER,
                default=ScalarValue(
                    scalar_type=ScalarType.INTEGER, integer_value=DEFAULT_BIN_COUNT
                ),
            ),
        ),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=VP_CACHE_KEY_SCHEMA,
        consumes=(MARKET_FOOTPRINT_SOURCE_ID,),
    )


def vp_poc_declaration() -> DataSourceDeclaration:
    """vp.poc: Point of Control (precio del bin de mayor volumen de la ventana)."""
    return _vp_declaration(VP_POC_SOURCE_ID)


def vp_vah_declaration() -> DataSourceDeclaration:
    """vp.vah: Value Area High (borde superior del area de valor del 70%)."""
    return _vp_declaration(VP_VAH_SOURCE_ID)


def vp_val_declaration() -> DataSourceDeclaration:
    """vp.val: Value Area Low (borde inferior del area de valor del 70%)."""
    return _vp_declaration(VP_VAL_SOURCE_ID)


class VolumeProfileError(RuntimeError):
    """La ventana de footprints no forma un perfil valido."""


@dataclass(frozen=True, slots=True)
class VolumeProfile:
    """Perfil de volumen determinista de una ventana de footprint.

    poc/vah/val son PRECIOS (centro de bin, Decimal). HVN/LVN NO estan aqui: llegan en
    una tanda posterior, tras su pre-registro AHP.
    """

    poc: Decimal
    vah: Decimal
    val: Decimal


@dataclass(frozen=True, slots=True)
class _Bins:
    """Perfil binado intermedio COMPARTIDO por vp.poc/vah/val y vp.hvn/lvn.

    Existe para que NO haya dos perfiles distintos de la misma familia (precision del
    CSA): se bina una sola vez y ambos consumidores leen de aqui. volume_by_bin lleva
    SOLO los bins ocupados (con volumen). degenerate_price es no-None cuando todo el
    volumen cae en un unico precio (rango nulo): no hay bins reales, ni VA, ni nodos.
    """

    min_price: Decimal
    bin_width: Decimal
    bin_count: int
    volume_by_bin: dict[int, Decimal]
    total_volume: Decimal
    poc_index: int
    lo_index: int
    hi_index: int
    degenerate_price: Decimal | None


def _build_bins(
    window: Sequence[FootprintPayload],
    *,
    bin_count: int,
    value_area_pct: Decimal,
) -> _Bins:
    """Agrega, bina y localiza POC + bordes de Value Area. Perfil base unico.

    Agrega el volumen EXACTO (buy+sell) por precio sobre la ventana, bina en bin_count
    bins uniformes sobre [min_price, max_price], y calcula POC (bin de mayor volumen) y
    la Value Area (expansion desde el POC al vecino de mayor volumen hasta cubrir
    value_area_pct). Rango nulo -> degenerate_price con ese unico precio.
    """
    if bin_count <= 0:
        msg = f"bin_count debe ser > 0, llego {bin_count}."
        raise VolumeProfileError(msg)
    if not window:
        msg = "ventana vacia: no hay footprint del que calcular el perfil."
        raise VolumeProfileError(msg)
    _require_single_flow(window)

    volume_by_price: defaultdict[Decimal, Decimal] = defaultdict(lambda: Decimal(0))
    for footprint in window:
        for cell in footprint.cells:
            volume_by_price[cell.price] += cell.buy_volume + cell.sell_volume

    if not volume_by_price:
        msg = "la ventana no tiene celdas: no hay volumen que perfilar."
        raise VolumeProfileError(msg)
    total_volume: Decimal = sum(volume_by_price.values(), Decimal(0))
    if total_volume <= 0:
        msg = "el volumen total de la ventana es cero: perfil indefinido."
        raise VolumeProfileError(msg)

    min_price = min(volume_by_price)
    max_price = max(volume_by_price)
    # Rango nulo (todo el volumen a un solo precio): POC=VAH=VAL, sin binar ni dividir.
    if min_price == max_price:
        return _Bins(
            min_price=min_price,
            bin_width=Decimal(0),
            bin_count=bin_count,
            volume_by_bin={0: total_volume},
            total_volume=total_volume,
            poc_index=0,
            lo_index=0,
            hi_index=0,
            degenerate_price=min_price,
        )

    bin_width = (max_price - min_price) / Decimal(bin_count)
    last_index = bin_count - 1
    volume_by_bin: dict[int, Decimal] = {}
    for price, volume in volume_by_price.items():
        # int() de un Decimal no negativo trunca = floor; el max cae en el ultimo bin.
        index = int((price - min_price) / bin_width)
        if index > last_index:
            index = last_index
        volume_by_bin[index] = volume_by_bin.get(index, Decimal(0)) + volume

    poc_index = _max_volume_index(volume_by_bin)
    lo_index, hi_index = _value_area_bounds(
        volume_by_bin, poc_index, total_volume, value_area_pct
    )
    return _Bins(
        min_price=min_price,
        bin_width=bin_width,
        bin_count=bin_count,
        volume_by_bin=volume_by_bin,
        total_volume=total_volume,
        poc_index=poc_index,
        lo_index=lo_index,
        hi_index=hi_index,
        degenerate_price=None,
    )


def compute_volume_profile(
    window: Sequence[FootprintPayload],
    *,
    bin_count: int = DEFAULT_BIN_COUNT,
    value_area_pct: Decimal = DEFAULT_VALUE_AREA_PCT,
) -> VolumeProfile:
    """POC / Value Area / VAH / VAL de una ventana de footprints del MISMO flujo.

    Sobre el perfil binado unico (_build_bins): POC = centro del bin de mayor volumen;
    VAH/VAL = centros de los bins extremos de la Value Area. En rango nulo (un solo
    precio) los tres son ese precio (bin_width=0 -> el centro es el propio precio).
    """
    bins = _build_bins(window, bin_count=bin_count, value_area_pct=value_area_pct)
    return VolumeProfile(
        poc=_bin_center(bins.min_price, bins.poc_index, bins.bin_width),
        vah=_bin_center(bins.min_price, bins.hi_index, bins.bin_width),
        val=_bin_center(bins.min_price, bins.lo_index, bins.bin_width),
    )


def _require_single_flow(window: Sequence[FootprintPayload]) -> None:
    """Un solo flujo: mezclar exchange/tipo/symbol/tf seria dato corrupto."""
    first = window[0]
    key = (first.exchange, first.market_type, first.symbol, first.timeframe)
    for footprint in window:
        other = (
            footprint.exchange,
            footprint.market_type,
            footprint.symbol,
            footprint.timeframe,
        )
        if other != key:
            msg = "la ventana mezcla flujos distintos (exchange/symbol/tipo/timeframe)."
            raise VolumeProfileError(msg)


def _max_volume_index(volume_by_bin: dict[int, Decimal]) -> int:
    """Indice del bin de mayor volumen; empate -> el de MENOR indice (determinista)."""
    best_index = min(volume_by_bin)
    best_volume = volume_by_bin[best_index]
    for index in sorted(volume_by_bin):
        if volume_by_bin[index] > best_volume:
            best_index = index
            best_volume = volume_by_bin[index]
    return best_index


def _value_area_bounds(
    volume_by_bin: dict[int, Decimal],
    poc_index: int,
    total_volume: Decimal,
    value_area_pct: Decimal,
) -> tuple[int, int]:
    """Expande desde el POC al vecino ocupado de mayor volumen hasta cubrir el objetivo.

    Trabaja sobre los bins OCUPADOS (con volumen), como v4: salta bins vacios. Ante
    empate up/down, prefiere el lado SUPERIOR (paridad v4).
    """
    occupied = sorted(volume_by_bin)
    target = total_volume * value_area_pct
    poc_pos = occupied.index(poc_index)
    lo_pos = poc_pos
    hi_pos = poc_pos
    accumulated = volume_by_bin[occupied[poc_pos]]
    last_pos = len(occupied) - 1
    while accumulated < target and (lo_pos > 0 or hi_pos < last_pos):
        up = volume_by_bin[occupied[hi_pos + 1]] if hi_pos < last_pos else Decimal(-1)
        down = volume_by_bin[occupied[lo_pos - 1]] if lo_pos > 0 else Decimal(-1)
        if up >= down:
            hi_pos += 1
            accumulated += volume_by_bin[occupied[hi_pos]]
        else:
            lo_pos -= 1
            accumulated += volume_by_bin[occupied[lo_pos]]
    return occupied[lo_pos], occupied[hi_pos]


def _bin_center(min_price: Decimal, index: int, bin_width: Decimal) -> Decimal:
    """Precio representativo de un bin: su centro (convencion fijada del modulo)."""
    return min_price + (Decimal(index) + Decimal("0.5")) * bin_width


# --- Nodos de volumen (vp.hvn / vp.lvn), F6 -----------------------------------------
# Semillas [PARIDAD v4] del detector (engines/l1/volume_profile_engine.py; pre-registro
# AHP FIRMADO). Parametros, no verdades: la calibracion (diferida) los sustituye.
HVN_VOLUME_MULTIPLIER = Decimal("1.5")  # HVN: volumen de bin > media * 1.5
LVN_VOLUME_MULTIPLIER = Decimal("0.3")  # LVN: volumen de bin < media * 0.3
# ADYACENCIA en INDICES de bin (no precio ni ticks de exchange): v4 uso 3 ticks, y en el
# espacio binado de v5.0 eso son 3 bins (precision de unidades del CSA).
ADJACENCY_BINS = 3
HVN_MAX = 5
LVN_MAX = 5


@dataclass(frozen=True, slots=True)
class VolumeNodes:
    """Nodos de volumen del perfil (vp.hvn / vp.lvn). Precios = centro de bin.

    hvn: nodos de ALTO volumen (iman / soporte-resistencia), SIN el POC. lvn: nodos de
    BAJO volumen (vacios), SOLO dentro de la Value Area. Tuplas ordenadas por precio y
    DETERMINISTAS (reproducibles bit a bit). Vacias si no hay nodos.
    """

    hvn: tuple[Decimal, ...]
    lvn: tuple[Decimal, ...]


@dataclass(frozen=True, slots=True)
class VolumeNodeParams:
    """Umbrales del detector de nodos como PARAMETRO (semillas [PARIDAD v4]).

    La calibracion (AHP, diferida) los sustituye sin tocar la logica.
    """

    hvn_multiplier: Decimal = HVN_VOLUME_MULTIPLIER
    lvn_multiplier: Decimal = LVN_VOLUME_MULTIPLIER
    adjacency_bins: int = ADJACENCY_BINS
    hvn_max: int = HVN_MAX
    lvn_max: int = LVN_MAX


# Singleton por defecto (evita construir en el default de la firma, B008).
_DEFAULT_NODE_PARAMS = VolumeNodeParams()


def compute_volume_nodes(
    window: Sequence[FootprintPayload],
    *,
    bin_count: int = DEFAULT_BIN_COUNT,
    value_area_pct: Decimal = DEFAULT_VALUE_AREA_PCT,
    params: VolumeNodeParams = _DEFAULT_NODE_PARAMS,
) -> VolumeNodes:
    """HVN/LVN sobre el MISMO perfil binado que vp.poc/vah/val. [PARIDAD v4].

    media = volumen_total / bins OCUPADOS (NO se promedia sobre bins vacios). HVN: bins
    ocupados con volumen > media*hvn_mult, excluido el POC y sus adyacentes (por INDICE
    de bin), ordenados por volumen DESCENDENTE (desempate por indice ascendente), dedup
    por adyacencia, tope hvn_max. LVN: TODOS los bins de [val_index, vah_index] --
    incluidos los VACIOS, que valen 0 y empatan -- con volumen < media*lvn_mult,
    ordenados por volumen ASCENDENTE y desempate por indice ASCENDENTE (reproducible bit
    a bit), dedup por adyacencia, tope lvn_max. Rango nulo -> sin nodos.
    """
    bins = _build_bins(window, bin_count=bin_count, value_area_pct=value_area_pct)
    if bins.degenerate_price is not None:
        return VolumeNodes(hvn=(), lvn=())

    occupied = bins.volume_by_bin
    mean_bin_volume = bins.total_volume / Decimal(len(occupied))

    hvn_min = mean_bin_volume * params.hvn_multiplier
    hvn_candidates = [
        index
        for index, volume in occupied.items()
        if volume > hvn_min and abs(index - bins.poc_index) > params.adjacency_bins
    ]
    hvn_candidates.sort(key=lambda index: (-occupied[index], index))
    hvn_indices = _dedup_adjacent(hvn_candidates, params.adjacency_bins, params.hvn_max)

    lvn_max_volume = mean_bin_volume * params.lvn_multiplier
    lvn_candidates = [
        index
        for index in range(bins.lo_index, bins.hi_index + 1)
        if occupied.get(index, Decimal(0)) < lvn_max_volume
    ]
    lvn_candidates.sort(key=lambda index: (occupied.get(index, Decimal(0)), index))
    lvn_indices = _dedup_adjacent(lvn_candidates, params.adjacency_bins, params.lvn_max)

    return VolumeNodes(
        hvn=tuple(
            _bin_center(bins.min_price, index, bins.bin_width)
            for index in sorted(hvn_indices)
        ),
        lvn=tuple(
            _bin_center(bins.min_price, index, bins.bin_width)
            for index in sorted(lvn_indices)
        ),
    )


def _dedup_adjacent(ordered_indices: list[int], adjacency: int, cap: int) -> list[int]:
    """Conserva indices en el ORDEN dado, saltando los que disten <= adjacency de uno ya
    conservado, y corta en cap. Determinista: depende solo del orden de entrada.
    """
    kept: list[int] = []
    for index in ordered_indices:
        if all(abs(index - other) > adjacency for other in kept):
            kept.append(index)
        if len(kept) >= cap:
            break
    return kept
