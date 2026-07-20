"""Alcance de una correccion de vela sobre un plan de regla (CA-P08-08, firmada).

Codigo PURO de plataforma (sin DB, sin outbox, sin reloj). Responde a DOS preguntas y a
ninguna mas:

  1. ?Se PUEDE propagar una correccion a este plan? Solo si TODAS sus fuentes son
     POINT_LOCAL. Si alguna es RECURSIVE o INTEGRATOR, la respuesta es NO -- y es un NO
     honesto, no una aproximacion.
  2. Si se puede, ?QUE evaluaciones invalida corregir la vela T? Las que miran T dentro
     de su ventana de history_bars: exactamente h barras a partir de T.

POR QUE LA GUARDIA ES DURA (Precision A, CA-P08-08). Para una fuente POINT_LOCAL el
valor de la barra T depende solo del dato de T, asi que corregir T invalida un conjunto
ACOTADO de evaluaciones y recalcular esa ventana es correcto y finito. Para una fuente
RECURSIVE (EMA, RSI, MACD) el valor de T depende de su propio valor en T-1, de modo que
un error en T contamina TODOS los valores posteriores; para un INTEGRATOR (CVD) el
acumulado arrastra el error indefinidamente. En ambos casos "recalcular unas cuantas
barras" NO produce el valor correcto: produce un numero equivocado con aspecto de
correcto. Por eso v5.0 SE ABSTIENE en vez de aproximar -- una senal derivada de un
indicador mal recalculado es peor que una senal que no se recalculo.

Abstenerse NO es degradar la regla: la regla sigue evaluando con normalidad cada
candle_closed. Lo unico que no ocurre es la PROPAGACION de la correccion. Y NO es
cuarentena: la regla no esta rota, es el motor el que aun no sabe corregirla (v5.0). Su
soporte real llega en P08b/P08c.
"""

from dataclasses import dataclass

from ce_v5.platform.rules.compiler import ExecutionPlan
from source.datasource import MemoryModel


@dataclass(frozen=True, slots=True)
class CorrectionScope:
    """Veredicto sobre si una correccion se propaga a un plan, y con que alcance.

    conformant=True -> todas las fuentes son POINT_LOCAL y history_bars es el ancho de
    la ventana a recalcular. conformant=False -> hay al menos una fuente no point-local
    y blocking_source_id / blocking_memory_model dicen CUAL y POR QUE, para que el
    motivo del salto quede registrado y no sea un silencio.
    """

    conformant: bool
    history_bars: int = 0
    blocking_source_id: str | None = None
    blocking_memory_model: MemoryModel | None = None


def correction_scope(plan: ExecutionPlan) -> CorrectionScope:
    """?Se puede propagar una correccion a este plan, y con que ventana? (CA-P08-08).

    CUALQUIER fuente no point-local DESCALIFICA el plan entero, aunque las demas si lo
    sean: una regla mixta combina en el mismo arbol un termino que se puede recalcular
    bien con otro que no, y el resultado de esa combinacion es tan incorrecto como su
    peor termino. No hay recalculo parcial que salve una conjuncion.

    h = MAXIMO history_bars entre las fuentes: la ventana debe cubrir a la fuente mas
    exigente, porque basta con que UNA mire la barra corregida para que la evaluacion
    completa quede invalidada.

    Un plan SIN fuentes (una regla de puras constantes) es conformante con h=0: no mira
    ninguna vela, asi que ninguna correccion la afecta -- y la ventana vacia que produce
    h=0 lo refleja con exactitud.
    """
    for source in plan.resolved_sources:
        model = source.declaration.memory_model
        if model is not MemoryModel.POINT_LOCAL:
            return CorrectionScope(
                conformant=False,
                blocking_source_id=source.source_id,
                blocking_memory_model=model,
            )
    history_bars = max(
        (source.history_bars for source in plan.resolved_sources), default=0
    )
    return CorrectionScope(conformant=True, history_bars=history_bars)


def affected_window(
    corrected_open_time: int, history_bars: int, timeframe_ms: int
) -> tuple[int, int] | None:
    """Las velas cuya evaluacion invalida corregir `corrected_open_time`.

    Devuelve (primero, ultimo) en open_time INCLUSIVE, o None si la ventana es vacia
    (history_bars=0: el plan no mira velas).

    Una evaluacion en la vela L mira las h barras que terminan en L, o sea
    [L-(h-1), L]. Esa ventana contiene T exactamente cuando L va de T a T+(h-1) barras.
    De ahi la ventana [T, T+(h-1)*timeframe_ms]: h barras contando la propia T.

    Se cuenta en BARRAS y se traduce a milisegundos con timeframe_ms; sumar h a un
    open_time en ms no significaria nada.
    """
    if history_bars <= 0:
        return None
    return (
        corrected_open_time,
        corrected_open_time + (history_bars - 1) * timeframe_ms,
    )


def is_within_window(open_time: int, window: tuple[int, int] | None) -> bool:
    """?Cae `open_time` dentro de la ventana afectada? Ventana vacia -> nunca."""
    if window is None:
        return False
    first, last = window
    return first <= open_time <= last
