"""Materializador de divergence.*: glue de BD + pivotes + RSI + snapshot (P08b-D1-05).

Vive en fichero propio del composition root, como fib_materializer y
pivotphase_materializer y por el mismo motivo: materializers.py lo importa arriba para
registrarlo, asi que las referencias de vuelta al registro se hacen con import DIFERIDO
(dentro de la funcion) para no cerrar el ciclo en import-time.

QUE HACE FALTA PARA REANUDAR, Y POR QUE ESE TRAMO. El estado (0028) es el ultimo pivote
CONFIRMADO de cada lado. El replay tiene que encontrar todos los pivotes posteriores a
ellos, y para decidir si una barra es pivote symmetric_pivots necesita `strength` barras
a su IZQUIERDA. De ahi el tramo: empieza `strength` barras ANTES del mas antiguo de los
dos pivotes guardados, y llega hasta la barra pedida.

POR QUE ESE ARRANQUE ES EXACTO Y NO UN MARGEN A OJO. Tres hechos encajan:

  1. La barra de un pivote guardado es SIEMPRE inicio de corrida (un pivote exige que la
     barra anterior sea estrictamente menor -- o mayor --, nunca igual). Asi que
     arrancar
     el escaneo `strength` barras antes de ella no puede partir por la mitad ninguna
     corrida que llegue hasta ella: la que contenga el arranque termina antes.
  2. La PRIMERA corrida del tramo nunca emite pivote (symmetric_pivots exige contexto
     izquierdo y en el indice 0 no lo hay), asi que un arranque a media corrida no puede
     inventar un pivote falso; y a partir de ella la segmentacion es identica a la de la
     historia entera, porque avanzar sobre valores iguales solo mira hacia delante.
  3. Todo pivote pendiente tiene ancla POSTERIOR a la guardada de su lado, luego cae al
     menos `strength` barras despues del arranque: su contexto izquierdo entra entero.

  Y lo que el tramo vuelva a ver de antes -- incluidos los pivotes ya contabilizados --
  lo descarta el dedup por ancla de _fold_kind. Nada se cuenta dos veces.

SIN ANCLA UTILIZABLE, BOOTSTRAP DESDE EL ORIGEN. "Utilizable" exige los DOS lados: con
uno solo no habria desde donde arrancar el tramo del otro sin adivinar. Al principio de
un historico eso dura lo que tarda en confirmarse el primer maximo y el primer minimo
--- un punado de barras con strength=2 --, asi que el bootstrap ahi no es un coste real.

EL RSI NO SE RECALCULA AQUI: se pide a rsi.value por el REGISTRO, ligado al rsi_period
efectivo, igual que fib pide swing.high/swing.low. Recomputarlo abriria una segunda
aritmetica de Wilder que podria apartarse de la primera sobre los mismos datos sin que
nadie lo notara. Su warm-up llega como serie MAS CORTA y se rellena por la CABEZA con
None -- que es exactamente lo que wilder_rsi devuelve en esas barras --, nunca por la
cola: la serie de rsi.value son siempre las barras mas RECIENTES.

CAUSALIDAD (DEC-PROVISIONAL-02, invariante compartido con swing.py). Un pivote anclado
en
la barra a no se confirma hasta a+strength, asi que las ultimas `strength` barras de una
materializacion todavia no pueden declarar evento. No es una perdida: es la misma
cautela que aplica detect_divergences sobre una serie cerrada, y por eso el GATE compara
replay y bootstrap SOBRE LA MISMA barra final. Lo provisional es de la integracion, no
del compute.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

from ce_v5.infra.db.divergence_snapshot import (
    PivotSnapshot,
    read_divergence_snapshot_before,
    write_divergence_snapshot,
)
from ce_v5.infra.db.market_candles import read_ohlcv_range, read_ohlcv_window
from ce_v5.platform.rules.indicators.divergence import (
    DivergenceKind,
    DivergenceOutput,
    DivergenceState,
    PivotObservation,
    divergence_flag,
    divergence_kind_token,
    divergence_replay,
    divergence_seed,
)
from ce_v5.platform.rules.indicators.rsi import RSI_PERIOD_DEFAULT, RSI_SOURCE_ID
from ce_v5.platform.rules.indicators.swing import SWING_STRENGTH_DEFAULT
from source.rules.scalar import ScalarType, ScalarValue

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ce_v5.infra.db.market_candles import CandleOHLCV

    # ALINEAR: mismo tipo Session que usa materializers.py (el del Protocol).
    from ce_v5.infra.db.ports import Session


def _rsi_series(
    session: Session,
    exchange: str,
    symbol: str,
    timeframe: str,
    open_time: int,
    bars: int,
    rsi_period: int,
) -> tuple[Decimal | None, ...]:
    """La serie de rsi.value del tramo, por el REGISTRO y con el period efectivo.

    Import DIFERIDO del registro (mismo motivo y mismo patron que _swing_series en
    fib_materializer): materializers.py importa este modulo en import-time para
    registrar
    las specs, asi que la referencia de vuelta se resuelve en CALL-time.

    Devuelve SIEMPRE `bars` valores, rellenando por la CABEZA con None lo que el warm-up
    de Wilder no alcance a cubrir. Asi la serie queda alineada 1:1 con el tramo y con la
    misma forma que wilder_rsi -- None donde no hay RSI --, que es lo que la funcion
    pura
    espera. FAIL-LOUD si rsi.value sirviera MAS barras de las pedidas: seria un
    desalineamiento que correria toda la serie de sitio.
    """
    from ce_v5.entrypoints.worker_rules.materializers import (
        SOURCE_MATERIALIZERS,
        ParameterizedMaterializer,
        UnwiredSourceError,
        _scalars_to_decimals,
    )

    spec = SOURCE_MATERIALIZERS.get(RSI_SOURCE_ID)
    if spec is None:
        msg = (
            f"la fuente base {RSI_SOURCE_ID!r} de divergence.* no esta cableada: no se "
            "materializa la divergencia (mismo invariante que MAT-08)."
        )
        raise UnwiredSourceError(msg)
    if isinstance(spec, ParameterizedMaterializer):
        spec = spec.with_params(
            {
                "period": ScalarValue(
                    scalar_type=ScalarType.INTEGER, integer_value=rsi_period
                )
            }
        )
    valores = _scalars_to_decimals(
        spec.materialize(session, exchange, symbol, timeframe, open_time, bars)
    )
    if len(valores) > bars:
        msg = (
            f"{RSI_SOURCE_ID!r} sirvio {len(valores)} barras para un tramo de {bars}: "
            "la serie de RSI no se puede alinear con la de precios (fail-loud)."
        )
        raise UnwiredSourceError(msg)
    faltan: tuple[Decimal | None, ...] = (None,) * (bars - len(valores))
    return faltan + valores


def _proyectar(
    kinds: frozenset[DivergenceKind], output: DivergenceOutput
) -> ScalarValue:
    """La proyeccion de UNA barra, ya en el CARRIER de serie (D1).

    Las cinco salidas salen del MISMO recorrido de pivotes; lo unico que cambia es que
    publican y en que campo tipado viajan: el token en string_value, los cuatro flags en
    boolean_value. divergence.* es la primera fuente BOOLEAN del catalogo vivo, y el
    carrier de D1 es lo que lo hace posible sin abrir un borde paralelo.
    """
    if output is DivergenceOutput.KIND:
        return ScalarValue(
            scalar_type=ScalarType.STRING,
            string_value=divergence_kind_token(kinds),
        )
    return ScalarValue(
        scalar_type=ScalarType.BOOLEAN,
        boolean_value=divergence_flag(kinds, output),
    )


@dataclass(frozen=True, slots=True)
class DivergenceRecursiveSpec:
    """Materializador RECURSIVE de las cinco divergence.* (0028).

    El estado es el ULTIMO PIVOTE DE CADA LADO. Materializa la ventana REANUDANDO las
    dos
    cadenas desde el snapshot vigente ANTERIOR a ella (read_divergence_snapshot_before)
    y
    recorriendo los pivotes posteriores con divergence_replay. Tras calcular, PERSISTE
    el
    estado de la barra vigente: es un materializador CON ESTADO, como los de cvd, ema,
    rsi, macd y fib, e idempotente (ON CONFLICT DO NOTHING).

    UN ESTADO, CINCO FUENTES. Un solo recorrido produce las cinco salidas; `output` dice
    cual publica ESTA instancia. Las cinco entradas del registro comparten cadena,
    snapshot y calculo, y por eso pueden materializar la misma barra sin estorbarse:
    escriben la MISMA fila. Como en MACD y en fib.

    strength y rsi_period son PARAMETROS DECLARADOS (overridables) y los propaga el
    dispatch (with_params) -- rsi_period, ademas, VIAJA hasta la serie de rsi.value que
    alimenta al fold, no solo hasta la cache_key. Los snapshots NO se cruzan: los dos
    entran en la identidad de divergence_snapshot (PK, 0028).
    """

    output: DivergenceOutput
    strength: int = SWING_STRENGTH_DEFAULT
    rsi_period: int = RSI_PERIOD_DEFAULT

    def _param(
        self, params: Mapping[str, ScalarValue], nombre: str, actual: int
    ) -> int:
        """El valor EFECTIVO de un param, validando su dominio (fail-loud).

        Ausente o sin entero -> se conserva el actual (aditividad D7: lo que la regla no
        pide, no cambia). Presente y fuera de dominio -> LANZA: el compilador ya valido
        nombre y tipo para todo override que pase por compile(), asi que esto es la
        ultima linea de defensa de quien llame with_params directamente. Mismo dominio
        que los CHECK de la 0028 (>= 1), heredado de swing y de rsi.
        """
        from ce_v5.entrypoints.worker_rules.materializers import UnwiredSourceError

        value = params.get(nombre)
        if value is None or value.integer_value is None:
            return actual
        efectivo = value.integer_value
        if efectivo < 1:
            msg = (
                f"{nombre} {efectivo!r} no es un valor valido para divergence.* (exige "
                ">= 1): no se materializa (fail-loud)."
            )
            raise UnwiredSourceError(msg)
        return efectivo

    def with_params(self, params: Mapping[str, ScalarValue]) -> DivergenceRecursiveSpec:
        """Copia ligada a los params EFECTIVOS de la regla (MAT-05 Q2).

        Solo sustituye los que lleguen; el registro conserva su instancia con los
        defaults (si se mutara, una regla contaminaria a las demas). output NO es
        parametro: es la identidad de la fuente, no algo que una regla pueda pedir.
        """
        return replace(
            self,
            strength=self._param(params, "strength", self.strength),
            rsi_period=self._param(params, "rsi_period", self.rsi_period),
        )

    def _tramo(
        self,
        session: Session,
        exchange: str,
        symbol: str,
        timeframe: str,
        open_time: int,
        first_open_time: int,
    ) -> tuple[tuple[CandleOHLCV, ...], DivergenceState]:
        """Las velas sobre las que se replaya y el estado con el que se siembra.

        CON ancla utilizable, el tramo arranca `strength` barras antes del mas antiguo
        de
        los dos pivotes guardados (el razonamiento de por que ESE arranque es exacto
        esta
        en el docstring del modulo). SIN ella, desde el ORIGEN.
        """
        anchor = read_divergence_snapshot_before(
            session,
            exchange,
            symbol,
            timeframe,
            self.strength,
            self.rsi_period,
            first_open_time,
        )
        if anchor is None or anchor.last_high is None or anchor.last_low is None:
            origen = read_ohlcv_range(
                session, exchange, symbol, timeframe, None, open_time
            )
            return (origen, divergence_seed())
        desde = min(anchor.last_high.open_time, anchor.last_low.open_time)
        # La cabecera trae `desde` y las `strength` barras que la preceden: el contexto
        # izquierdo que symmetric_pivots exige para el primer pivote pendiente.
        cabecera = read_ohlcv_window(
            session, exchange, symbol, timeframe, desde, self.strength + 1
        )
        resto = read_ohlcv_range(session, exchange, symbol, timeframe, desde, open_time)
        velas = cabecera + resto
        return (velas, self._sembrar(anchor.last_high, anchor.last_low, velas))

    def _sembrar(
        self,
        last_high: PivotSnapshot,
        last_low: PivotSnapshot,
        velas: tuple[CandleOHLCV, ...],
    ) -> DivergenceState:
        """El estado persistido, con sus barras traducidas a indices del tramo.

        FAIL-LOUD si un pivote guardado no aparece en el tramo: significaria que el
        tramo
        no arranco donde debia y el replay se saltaria pivotes -- justo la deriva
        silenciosa que el GATE de ADR-007 prohibe. Por construccion no puede pasar (el
        tramo empieza en la barra del mas antiguo de los dos, o antes).
        """
        from ce_v5.entrypoints.worker_rules.materializers import UnwiredSourceError

        indice_de = {vela.open_time: i for i, vela in enumerate(velas)}

        def _observacion(pivote: PivotSnapshot, lado: str) -> PivotObservation:
            indice = indice_de.get(pivote.open_time)
            if indice is None:
                msg = (
                    f"el ultimo pivote {lado} del snapshot (barra "
                    f"{pivote.open_time}) no cae en el tramo replayado: el replay "
                    "se saltaria pivotes (fail-loud)."
                )
                raise UnwiredSourceError(msg)
            return PivotObservation(index=indice, price=pivote.price, rsi=pivote.rsi)

        return DivergenceState(
            last_high=_observacion(last_high, "de maximos"),
            last_low=_observacion(last_low, "de minimos"),
        )

    def materialize(
        self,
        session: Session,
        exchange: str,
        symbol: str,
        timeframe: str,
        open_time: int,
        history_bars: int,
    ) -> tuple[ScalarValue, ...]:
        window = read_ohlcv_window(
            session, exchange, symbol, timeframe, open_time, history_bars
        )
        if not window:
            return ()
        velas, semilla = self._tramo(
            session, exchange, symbol, timeframe, open_time, window[0].open_time
        )
        if not velas:
            return ()
        rsi = _rsi_series(
            session,
            exchange,
            symbol,
            timeframe,
            open_time,
            len(velas),
            self.rsi_period,
        )
        estado, eventos = divergence_replay(
            [vela.high for vela in velas],
            [vela.low for vela in velas],
            rsi,
            semilla,
            self.strength,
        )
        # PROYECCION DENSA: cada evento se ancla en SU barra (`index`), y las demas
        # barras
        # publican la ausencia. Una barra puede acumular dos kinds (un lado bajista y
        # otro
        # alcista); kind los colapsa por prioridad y los flags no.
        por_barra: dict[int, set[DivergenceKind]] = {}
        for evento in eventos:
            por_barra.setdefault(evento.index, set()).add(evento.kind)
        serie = tuple(
            _proyectar(frozenset(por_barra.get(indice, ())), self.output)
            for indice in range(len(velas))
        )[-len(window) :]
        write_divergence_snapshot(
            session,
            exchange,
            symbol,
            timeframe,
            self.strength,
            self.rsi_period,
            open_time,
            self._persistible(estado.last_high, velas),
            self._persistible(estado.last_low, velas),
        )
        return serie

    def _persistible(
        self, pivote: PivotObservation | None, velas: tuple[CandleOHLCV, ...]
    ) -> PivotSnapshot | None:
        """El pivote del estado con su indice traducido de vuelta a barra (open_time).

        None se conserva como None: "todavia no hay pivote de este lado" es un hecho del
        estado y se guarda como tal (columnas nullable de la 0028), no se rellena.
        """
        if pivote is None:
            return None
        return PivotSnapshot(
            open_time=velas[pivote.index].open_time,
            price=pivote.price,
            rsi=pivote.rsi,
        )
