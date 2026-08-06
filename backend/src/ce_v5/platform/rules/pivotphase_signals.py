"""Extraccion de senales de pivotphase desde series materializadas (P08c P5 T2/T3).

Convierte las series ya materializadas (orderflow.delta, footprint, vp.*) en los
insumos que consume pivotphase: el impulse_score del gate de fase 1 (T2) y las features
crudas de los factores de confianza (T3). Capa de EXTRACCION (P5), no de modelo: la
normalizacion final (percentil / combinacion) vive en pivotphase_confidence.py; aqui se
producen los escalares crudos por barra. Determinista, solo Decimal (ADR-007).

6a / T2 (impulse_score): escalado de |delta| por percentil de su distribucion reciente,
0-100 (opcion B del AHP REV 2; DICTAMEN P08c-PIVOT-04). Deriva firmada respecto a v4.

T3 (features de confianza, formas semilla ratificadas P08c-PIVOT-05 [A CALIBRAR]):
- F2 exhaustion: 1 - |delta| / max(|delta| reciente). Mas caida desde el pico = mas
  exhaustion = mas soporte (6b: delta real, no proxy notrade).
- F4 esfuerzo/resultado: |delta| / span de precio de la barra. Alto delta con poco
  desplazamiento = esfuerzo absorbido (I-04 2.3).
- F3 divergencia precio-vs-CVD (P08c-CONF-03): magnitud del salto relativo de precio
  entre los dos pivotes de una divergencia REGULAR confirmada, orientada por la
  direccion del impulso. Reutiliza el emparejamiento de divergence.* (P08b) sobre
  close y cvd.value.
- F6 (contexto VP) NO necesita funcion: se arma directo como VpContextInput(price,
  vp.hvn, vp.lvn) y su normalizacion por distancia vive en el modelo.
Las features F2/F3/F4 son el 'raw' que el modelo normaliza luego por percentil contra su
distribucion reciente.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING

from ce_v5.platform.rules.indicators.divergence import (
    DivergenceKind,
    divergence_replay,
    divergence_seed,
)
from ce_v5.platform.rules.indicators.swing import SWING_STRENGTH_DEFAULT
from ce_v5.platform.rules.pivotphase import BEARISH, BULLISH

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Cuantizacion determinista del score 0-100 (ADR-007).
_IMPULSE_QUANTUM = Decimal("0.01")


def _percentile_rank(value: Decimal, distribution: Sequence[Decimal]) -> Decimal:
    """Rango percentil mid-rank de value en [0,1]: (n_menores + n_iguales/2) / n.

    Misma forma semilla que pivotphase_confidence (P4); si aparece un tercer uso se
    extrae a un util comun. distribution vacia la trata el llamador (no evaluable).
    """
    n = len(distribution)
    below = sum(1 for d in distribution if d < value)
    equal = sum(1 for d in distribution if d == value)
    return (Decimal(below) + Decimal(equal) / Decimal(2)) / Decimal(n)


def normalize_impulse_score(
    delta: Decimal, recent_abs_delta: Sequence[Decimal]
) -> Decimal | None:
    """impulse_score 0-100 = percentil de |delta| en la distribucion reciente x100.

    recent_abs_delta: ventana reciente de |delta| del propio simbolo/TF (materializada).
    Vacia -> None: sin base para normalizar es "sin impulso" (BarSignals.impulse_score
    None -> la FSM no arranca fase 1). El tamano de la ventana y percentil vs z-score
    son [A CALIBRAR AHP]. Determinista.
    """
    if not recent_abs_delta:
        return None
    rank = _percentile_rank(abs(delta), recent_abs_delta)
    return (rank * Decimal(100)).quantize(_IMPULSE_QUANTUM, rounding=ROUND_HALF_EVEN)


def exhaustion_feature(
    delta: Decimal, recent_abs_delta: Sequence[Decimal]
) -> Decimal | None:
    """F2 (exhaustion de delta): 1 - |delta| / max(|delta| reciente), acotado a [0,1].

    Mas caida desde el pico reciente = mas exhaustion = mas soporte (6b: delta real, no
    proxy notrade). recent_abs_delta = ventana reciente de |delta| que INCLUYE la barra
    actual (asi el pico >= |delta| y el resultado cae en [0,1]); si por ventana no lo
    incluyera, el acotado a >=0 lo protege. Pico 0 (todo delta nulo) o ventana vacia ->
    None (no evaluable). Tamano de ventana [A CALIBRAR]. Es el 'raw' de F2; el modelo lo
    normaliza luego por percentil.
    """
    if not recent_abs_delta:
        return None
    peak = max(recent_abs_delta)
    if peak <= 0:
        return None
    feature = Decimal(1) - abs(delta) / peak
    return feature if feature > 0 else Decimal(0)


def effort_result_feature(delta: Decimal, price_range: Decimal) -> Decimal | None:
    """F4 (esfuerzo/resultado): |delta| / span de precio de la barra.

    Alto delta con poco desplazamiento = esfuerzo absorbido = mas soporte (I-04 2.3).
    price_range = span de precio del footprint de la barra (max-min de sus celdas).
    Rango <= 0 -> None (no evaluable). Es el 'raw' de F4; el modelo lo normaliza luego
    por percentil.
    """
    if price_range <= 0:
        return None
    return abs(delta) / price_range


# --- F3: divergencia precio-vs-CVD (P08c-CONF-03) ------------------------------------

# Fuerza simetrica de los pivotes de F3. Se hereda de swing.* en vez de declararse aqui:
# los pivotes de precio y los de CVD tienen que salir de la MISMA geometria, y duplicar
# el 2 abriria la puerta a que un dia divergieran. Semilla [A CALIBRAR AHP].
_F3_STRENGTH = SWING_STRENGTH_DEFAULT

# Que divergencia SOPORTA el pivote que se espera, segun la direccion del IMPULSO.
#
# La trampa es la misma que en F1 (P08c-CONF-01 paso 3a): PivotState.direction es la del
# IMPULSO, no la del pivote. Un impulso alcista busca un TECHO, y lo que confirma un
# techo es la divergencia BAJISTA sobre los MAXIMOS (precio hace maximo mas alto, CVD
# hace maximo mas bajo: el precio sube sin que lo acompane el flujo acumulado).
#
# SOLO LAS REGULARES, y esto no es un recorte: la divergencia REGULAR senala AGOTAMIENTO
# (reversion), que es justo lo que un pivote es. La HIDDEN senala CONTINUACION -- con
# impulso alcista, un maximo mas bajo de precio con CVD mas alto dice que la tendencia
# sigue --, asi que contarla como soporte del pivote seria puntuar como evidencia a
# favor lo que es evidencia en contra.
_F3_SUPPORTING_KIND: dict[str, DivergenceKind] = {
    BULLISH: DivergenceKind.REGULAR_BEAR,
    BEARISH: DivergenceKind.REGULAR_BULL,
}


def cvd_divergence_magnitudes(
    closes: Sequence[Decimal],
    cvd: Sequence[Decimal],
    strength: int = _F3_STRENGTH,
) -> dict[int, dict[DivergenceKind, Decimal]]:
    """Magnitud de cada divergencia precio-vs-CVD CONFIRMADA, indexada por barra.

    REUTILIZA divergence_replay (P08b) en vez de reimplementar el emparejamiento: ese
    modulo avisa de que _fold_kind es la UNICA implementacion y que forkearla dejaria al
    replay apartarse de la formula sin que nadie lo note. Aqui se le pasa el CVD en el
    hueco del indicador (la firma pide Sequence[Decimal | None] y una de Decimal lo
    satisface) y `closes` en los DOS huecos de precio: eso es exactamente la convencion
    CLOSE-ONLY ratificada en P08c-CONF-03 -- symmetric_pivots(closes) se filtra a HIGH
    para un lado y a LOW para el otro, asi que los maximos y los minimos salen de la
    MISMA serie de cierres, no de high/low separados como en divergence.* de P08b.

    MAGNITUD = |price_curr - price_prev| / price_prev, el salto RELATIVO del precio
    entre los dos pivotes del par. Semilla [A CALIBRAR AHP]: mide "cuanto se movio el
    precio mientras el flujo no lo acompanaba". Es relativa y no absoluta para que la
    escala no dependa del precio del simbolo.

    price_prev <= 0 -> esa divergencia se DESCARTA (fail-safe): dividir por un precio no
    positivo daria una magnitud sin sentido. Con cierres reales no ocurre; si ocurriera,
    la barra queda sin soporte de F3 en vez de con un numero inventado.

    Devuelve solo las barras CON divergencia; el llamador trata la ausencia como 0.
    """
    _, eventos = divergence_replay(closes, closes, cvd, divergence_seed(), strength)
    por_barra: dict[int, dict[DivergenceKind, Decimal]] = {}
    for evento in eventos:
        if evento.price_prev <= 0:
            continue
        magnitud = abs(evento.price_curr - evento.price_prev) / evento.price_prev
        por_barra.setdefault(evento.index, {})[evento.kind] = magnitud
    return por_barra


def cvd_divergence_feature(
    direction: str, en_la_barra: Mapping[DivergenceKind, Decimal]
) -> Decimal:
    """F3: la magnitud de la divergencia que SOPORTA el pivote esperado, o 0.

    direction = la del IMPULSO vigente en la FSM (ver _F3_SUPPORTING_KIND). Sin
    direccion (FSM en IDLE, direction == "") no hay pivote que soportar -> 0.

    DEVUELVE 0 Y NO None, a diferencia de F2/F4: "no hubo divergencia en esta barra"
    es un HECHO, no un hueco -- la inmensa mayoria de las barras no tienen ninguna,
    igual que pasa con los detectores de F7. Que los 0 SI entren en la distribucion es
    lo que hace que una divergencia real destaque por percentil; si se filtraran como
    no evaluables, la distribucion solo tendria divergencias y todas parecerian
    corrientes.
    """
    kind = _F3_SUPPORTING_KIND.get(direction)
    if kind is None:
        return Decimal(0)
    return en_la_barra.get(kind, Decimal(0))
