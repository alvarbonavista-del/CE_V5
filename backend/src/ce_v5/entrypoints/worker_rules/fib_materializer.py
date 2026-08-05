"""Materializador de fib.*: glue de BD + swing + grid con histeresis (P08b-FIB-01).

Es el materializador mas compuesto del catalogo: RECURSIVE (el rango es pegajoso y su
memoria no tiene cota) Y consumidor de fuentes DERIVADAS (swing.high/swing.low, que a su
vez se materializan sobre los cierres). Vive en fichero propio del composition root,
como pivotphase_materializer.py y por el mismo motivo: materializers.py lo importa
arriba para registrarlo, asi que las referencias de vuelta al registro se hacen con
import DIFERIDO (dentro de la funcion) para no cerrar el ciclo en import-time.

COMO SE COMPONE, Y POR QUE ASI. El replay necesita, para cada barra del tramo que
reproduce, el par (swing_high, swing_low) de ESA barra. swing.* ya sabe servir su serie
por su source_id, asi que no se recalcula aqui: se pide al REGISTRO, ligado al strength
efectivo, con history_bars = el numero de barras del tramo y el mismo open_time. Las dos
series salen entonces alineadas a las ULTIMAS barras hasta open_time, que es exactamente
el tramo pedido.

LA ALINEACION, QUE ES LO DELICADO. swing.* es WINDOWED con ventana 100: no emite valor
hasta tener 100 cierres. Por eso puede devolver MENOS barras de las pedidas, y siempre
son las mas RECIENTES. De ahi la regla de alineacion de este glue: la serie de swing se
empareja con la COLA de los cierres del tramo (closes[-k:]). Dos casos, los dos exactos:

- CON ancla: el tramo es (ancla, open_time]. Un ancla solo existe en una barra en la que
  ya se emitio serie, es decir en la que swing ya emitia; toda barra posterior tiene
  swing, asi que k == n y la alineacion es 1:1. Si no lo fuera, el replay se saltaria
  barras y divergeria del bootstrap en silencio: se comprueba y FALLA RUIDOSO.
- SIN ancla (bootstrap): el tramo es todo el historico y k = n - 99 (el warm-up
  propio de swing). El grid arranca en la primera barra con swing y las anteriores
  no emiten valor: la serie sale MAS CORTA, como en el warm-up del RSI, y el
  evaluador lo trata como NOT_EVALUABLE (K3). Nunca se rellena.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

from ce_v5.infra.db.fib_range_snapshot import (
    read_fib_range_snapshot_before,
    write_fib_range_snapshot,
)
from ce_v5.infra.db.market_candles import read_close_range, read_ohlcv_window
from ce_v5.platform.rules.indicators.fib import (
    FibOutput,
    fib_output,
    fib_range_seed,
    fib_range_step,
)
from ce_v5.platform.rules.indicators.swing import (
    SWING_HIGH_SOURCE_ID,
    SWING_LOW_SOURCE_ID,
    SWING_STRENGTH_DEFAULT,
)
from source.rules.scalar import ScalarType, ScalarValue

if TYPE_CHECKING:
    from collections.abc import Mapping

    # ALINEAR: mismo tipo Session que usa materializers.py (el del Protocol).
    from ce_v5.infra.db.ports import Session


def _swing_series(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    open_time: int,
    bars: int,
    strength: int,
) -> tuple[tuple[Decimal, ...], tuple[Decimal, ...]]:
    """Las series swing.high y swing.low del tramo, por el REGISTRO y con el strength.

    Import DIFERIDO del registro (mismo motivo y mismo patron que el _mat de
    pivotphase_materializer): materializers.py importa este modulo en import-time para
    registrar la spec, asi que la referencia de vuelta se resuelve en CALL-time, cuando
    SOURCE_MATERIALIZERS ya existe.

    No se recalculan los pivotes aqui: se pide la MISMA serie que serviria swing.high /
    swing.low a una regla. Si se recomputaran, fib podria discrepar de swing sobre los
    mismos datos y nadie lo notaria.
    """
    from ce_v5.entrypoints.worker_rules.materializers import (
        SOURCE_MATERIALIZERS,
        ParameterizedMaterializer,
        UnwiredSourceError,
    )

    params: Mapping[str, ScalarValue] = {
        "strength": ScalarValue(scalar_type=ScalarType.INTEGER, integer_value=strength)
    }
    series: list[tuple[Decimal, ...]] = []
    for source_id in (SWING_HIGH_SOURCE_ID, SWING_LOW_SOURCE_ID):
        spec = SOURCE_MATERIALIZERS.get(source_id)
        if spec is None:
            msg = (
                f"la fuente base {source_id!r} de fib.* no esta cableada: no se "
                "materializa el grid (mismo invariante que MAT-08)."
            )
            raise UnwiredSourceError(msg)
        if isinstance(spec, ParameterizedMaterializer):
            spec = spec.with_params(params)
        series.append(
            spec.materialize(session, exchange, symbol, timeframe, open_time, bars)
        )
    high, low = series
    if len(high) != len(low):
        msg = (
            f"swing.high y swing.low devolvieron series de distinta longitud "
            f"({len(high)} y {len(low)}): el grid no puede emparejarlas por barra "
            "(fail-loud)."
        )
        raise UnwiredSourceError(msg)
    return (high, low)


@dataclass(frozen=True, slots=True)
class FibRecursiveSpec:
    """Materializador RECURSIVE de fib.nearest_level/fib.level_pct (0027).

    El estado es el RANGO [range_low, range_high] con histeresis: solo se mueve cuando
    un swing lo supera por >= 0.414 veces su tamano. Materializa la ventana SEMBRANDO el
    rango con el snapshot vigente ANTERIOR a ella (read_fib_range_snapshot_before) y
    aplicando fib_range_step con los swing de las barras posteriores. Tras calcular,
    PERSISTE el rango de la barra vigente: es un materializador CON ESTADO, como los de
    cvd, ema, rsi y macd, e idempotente (ON CONFLICT DO NOTHING).

    UN ESTADO, DOS FUENTES. Un solo recorrido produce las dos salidas; `output` dice
    cual publica ESTA instancia. Las dos entradas del registro comparten rango, snapshot
    y calculo, y por eso pueden materializar la misma barra sin estorbarse: escriben la
    MISMA fila. Como en MACD.

    SIN ancla, el bootstrap arranca DESDE EL ORIGEN. Aqui no es una eleccion de diseno
    sino una necesidad: el rango es pegajoso, asi que el de hoy puede venir de un pivote
    de hace cientos de barras y recortar el bootstrap a la ventana daria OTRO rango.

    El GATE de ADR-007: el replay desde CUALQUIER snapshot valido reproduce las dos
    colas identicas a las del bootstrap desde el origen, porque el fold de la histeresis
    es determinista sobre Decimal bajo el contexto pinneado de fib.py.

    strength es PARAMETRO DECLARADO (overridable) y lo propaga el dispatch
    (with_params), tanto al grid como -- y esto es lo que importa -- a las series de
    swing que lo alimentan. Los snapshots NO se cruzan: strength entra en la identidad
    de fib_range_snapshot (PK, 0027).
    """

    output: FibOutput
    strength: int = SWING_STRENGTH_DEFAULT

    def with_params(self, params: Mapping[str, ScalarValue]) -> FibRecursiveSpec:
        """Copia ligada al strength EFECTIVO de la regla (MAT-05 Q2).

        El compilador ya valido nombre y tipo (ParamSpec.value_type=INTEGER) para todo
        override que pase por compile(). Este chequeo queda como ULTIMA linea de defensa
        para quien llame with_params directamente (fuera de un ExecutionPlan compilado):
        un strength fuera de dominio (< 1) falla RUIDOSO, no materializa con el default
        en silencio. Mismo dominio que el CHECK de la 0027 y que swing, de quien se
        hereda el parametro.
        """
        from ce_v5.entrypoints.worker_rules.materializers import UnwiredSourceError

        value = params.get("strength")
        if value is None or value.integer_value is None:
            return self
        strength = value.integer_value
        if strength < 1:
            msg = (
                f"strength {strength!r} no es una fuerza simetrica valida (exige "
                ">= 1): no se materializa el grid fib (fail-loud)."
            )
            raise UnwiredSourceError(msg)
        return replace(self, strength=strength)

    def materialize(
        self,
        session: Session,
        exchange: str,
        symbol: str,
        timeframe: str,
        open_time: int,
        history_bars: int,
    ) -> tuple[Decimal, ...]:
        from ce_v5.entrypoints.worker_rules.materializers import UnwiredSourceError

        window = read_ohlcv_window(
            session, exchange, symbol, timeframe, open_time, history_bars
        )
        if not window:
            return ()
        first_open_time = window[0].open_time
        anchor = read_fib_range_snapshot_before(
            session, exchange, symbol, timeframe, self.strength, first_open_time
        )
        after = anchor[0] if anchor is not None else None
        tramo = read_close_range(session, exchange, symbol, timeframe, after, open_time)
        if not tramo:
            return ()
        swing_high, swing_low = _swing_series(
            session,
            exchange,
            symbol,
            timeframe,
            open_time,
            len(tramo),
            self.strength,
        )
        bars = len(swing_high)
        if bars == 0:
            # swing aun no emite (menos de su ventana de historia): sin pivotes no hay
            # rango que tender. Serie vacia (NOT_EVALUABLE, K3) y NINGUN snapshot: un
            # rango inventado seria un ancla falsa que envenenaria todo replay
            # posterior.
            return ()
        if anchor is not None and bars != len(tramo):
            # Con ancla, el grid tiene que avanzar sobre TODAS las barras posteriores a
            # ella. Si swing diera menos, el replay se saltaria barras y divergeria del
            # bootstrap en silencio -- justo la deriva que el GATE de ADR-007 prohibe.
            msg = (
                f"swing sirvio {bars} barras para un tramo de {len(tramo)} tras el "
                f"ancla en {after!r}: el replay del grid se saltaria barras "
                "(fail-loud)."
            )
            raise UnwiredSourceError(msg)
        # ALINEACION: swing sirve siempre las barras MAS RECIENTES, asi que se empareja
        # con la cola del tramo. Con ancla la cola es el tramo entero; en bootstrap deja
        # fuera el warm-up de swing, que es justo lo que no puede emitir valor.
        closes = [close for _, close in tramo[-bars:]]
        if anchor is not None:
            range_high, range_low = anchor[1], anchor[2]
        else:
            range_high, range_low = fib_range_seed(swing_high[0], swing_low[0])
        valores: list[Decimal] = []
        for indice in range(bars):
            range_high, range_low = fib_range_step(
                range_high, range_low, swing_high[indice], swing_low[indice]
            )
            valores.append(
                fib_output(range_high, range_low, closes[indice], self.output)
            )
        series = tuple(valores[-len(window) :])
        write_fib_range_snapshot(
            session,
            exchange,
            symbol,
            timeframe,
            self.strength,
            open_time,
            range_high,
            range_low,
        )
        return series
