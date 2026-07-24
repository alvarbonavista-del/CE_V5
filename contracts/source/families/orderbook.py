"""Familia market.* : snapshot del libro L2 y su resync (P07c; ADR-014, ADR-007,
ADR-006).

P07c anade a la familia market.* (que YA existe, ADR-004: NO es familia nueva, no
dispara la regla 5.12) la clase de dato ORDERBOOK. El libro COMPLETO vive en memoria (el
motor OrderbookBook); aqui se PERSISTE solo el TOP-K por lado, en dos variantes de un
mismo snapshot:

- FRONTIER (kind='frontier'): la foto del libro AS-OF el cierre de una barra (uno por
  barra). Es un hecho canonico por ventana, como el footprint: se PUBLICA por outbox
  (market.orderbook_frontier) y P08c lo consume como VENTANA CERRADA en candle_closed,
  NUNCA intrabar (DEC-PROVISIONAL-02).

- SAMPLE (kind='sample'): una muestra intra-ventana a cadencia fija. Se PERSISTE SIN
  publicar (como los trades, que no van al bus): nadie la consume por evento, sirve para
  reconstruir la evolucion del libro dentro de la barra. NO es un event_type publicado.

REPRODUCIBILIDAD (cond.1): K (profundidad), cadencia y ventana entran en la
idempotency/cache_key, y formula_version sube ante cualquier cambio semantico. Dos
corridas con la misma configuracion producen las MISMAS claves; cambiar K o la cadencia
produce OTRO hecho, no pisa el anterior.

is_complete FAIL-SAFE UNIFORME (cond.3): un hueco/resync en la ventana marca
is_complete=False en las muestras afectadas Y en el frontier. El DEFAULT es False: lo
que no declara su completitud cuenta como incompleto.

El RESYNC es su PROPIO hecho publicado (market.orderbook_resynced), NO una correccion al
estilo de candle_corrected: un libro no se "corrige" retroactivamente, se REINICIA desde
una foto nueva, y ese reinicio es un evento en si mismo.

PRECIOS Y TAMANOS EN Decimal, NUNCA float: el exchange los publica como texto decimal y
asi se conservan; el libro es la base del precio de ejecucion (M5).
"""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from source.envelope import EventPayload
from source.families.market import (
    EXCHANGE_PATTERN,
    SYMBOL_PATTERN,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    Timeframe,
)
from source.time import EpochMillis


class MarketOrderbookEventType(StrEnum):
    """Tipos de evento de orderbook PUBLICADOS (market.*), ADR-007.

    SOLO estos dos van al registro (CA-06) y a la outbox. La variante 'sample' del
    snapshot NO es un event_type: se persiste, no se encola, asi que no tiene sitio
    aqui.
    """

    ORDERBOOK_FRONTIER = "market.orderbook_frontier"
    ORDERBOOK_RESYNCED = "market.orderbook_resynced"


class MarketOrderbookSnapshotKind(StrEnum):
    """Las dos variantes de un snapshot en UNA tabla (dictamen de Central).

    FRONTIER se publica (as-of close_time, uno por barra); SAMPLE se persiste sin
    publicar (muestra intra-ventana a cadencia). La misma forma de payload; el kind y el
    sitio (outbox o no) los distingue.
    """

    FRONTIER = "frontier"
    SAMPLE = "sample"


def orderbook_snapshot_idempotency_key(
    *,
    kind: MarketOrderbookSnapshotKind,
    stream_key: str,
    timeframe: Timeframe,
    open_time: int,
    sample_time: int | None,
    depth_k: int,
    cadence_ms: int,
    formula_version: int,
) -> str:
    """idempotency/cache_key de un snapshot: UNICA POR CONSTRUCCION e incluye la CONFIG.

    REPRODUCIBILIDAD (cond.1): la clave lleva K, cadencia, ventana y formula_version.
    Dos snapshots del mismo instante con distinto K -- o distinta cadencia, o distinta
    formula_version -- son HECHOS DISTINTOS y no colisionan; reprocesar con la MISMA
    config reconstruye la MISMA clave y no duplica. El frontier se ancla en la ventana
    (open_time, uno por barra); el sample anade su instante (sample_time) dentro de
    ella. Los publicos NO llevan tenant (scope=public_market, ADR-011).
    """
    if kind is MarketOrderbookSnapshotKind.FRONTIER:
        if sample_time is not None:
            msg = (
                "un snapshot 'frontier' no lleva sample_time (es la foto de la barra)."
            )
            raise ValueError(msg)
        window = str(open_time)
        prefix = MarketOrderbookEventType.ORDERBOOK_FRONTIER.value
    else:
        if sample_time is None:
            msg = "un snapshot 'sample' exige sample_time (su instante en la ventana)."
            raise ValueError(msg)
        window = f"{open_time}@{sample_time}"
        prefix = "market.orderbook_sample"
    parts = [
        prefix,
        stream_key,
        timeframe.value,
        window,
        f"k{depth_k}",
        f"c{cadence_ms}",
        f"v{formula_version}",
    ]
    return "|".join(parts)


def orderbook_resynced_idempotency_key(
    *,
    stream_key: str,
    from_sequence: int,
    to_sequence: int | None,
) -> str:
    """idempotency_key de un resync: el MISMO hueco es UN hecho, no dos.

    Se ancla en (stream, from_sequence, to_sequence), igual que el UNIQUE NULLS NOT
    DISTINCT de market_orderbook_discontinuity: si el mismo hueco se detecta dos veces
    (dos reconexiones antes de consumirlo), la clave coincide y la outbox no reencola.
    Un extremo desconocido (to_sequence None) se codifica explicito para no colisionar
    con un to real.
    """
    to = "none" if to_sequence is None else str(to_sequence)
    return "|".join(
        [
            MarketOrderbookEventType.ORDERBOOK_RESYNCED.value,
            stream_key,
            f"from{from_sequence}",
            f"to{to}",
        ]
    )


class OrderbookLevel(BaseModel):
    """Un nivel del top-K persistido: precio y tamano agregado a ese precio.

    Ya VALIDADO (ADR-006): precio y tamano finitos y positivos. Un nivel de tamano 0 no
    es un nivel del libro (en el motor un tamano 0 BORRA el nivel; lo que se persiste
    son niveles vivos). Decimal, nunca float: el libro es la base del precio de
    ejecucion.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    price: Decimal
    size: Decimal

    @model_validator(mode="after")
    def _nivel_coherente(self) -> "OrderbookLevel":
        if not self.price.is_finite() or self.price <= 0:
            msg = f"price: nivel de precio no finito o no positivo ({self.price})."
            raise ValueError(msg)
        if not self.size.is_finite() or self.size <= 0:
            msg = f"size: tamano no finito o no positivo ({self.size})."
            raise ValueError(msg)
        return self


class OrderbookSnapshotPayload(EventPayload):
    """Snapshot top-K del libro L2 (P07c). Cubre las dos variantes: frontier y sample.

    El payload es UNO; el kind decide si se publica (frontier, por outbox) o solo se
    persiste (sample). Por eso NO se registra por si mismo dos veces: el registro
    (CA-06) mapea market.orderbook_frontier -> esta clase; el sample no es event_type.

    is_complete es ORTOGONAL al kind: una muestra o un frontier pueden estar completos o
    no segun hubiera un hueco/resync en su ventana (cond.3). DEFAULT False (fail-safe):
    lo que no declara su completitud cuenta como incompleto.
    """

    model_config = ConfigDict(extra="forbid")

    exchange: str = Field(pattern=EXCHANGE_PATTERN)
    market_type: MarketType
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    depth_k: int = Field(ge=1)
    bids: tuple[OrderbookLevel, ...]
    asks: tuple[OrderbookLevel, ...]
    sequence: int = Field(ge=0)
    kind: MarketOrderbookSnapshotKind
    timeframe: Timeframe
    open_time: EpochMillis
    close_time: EpochMillis
    sample_time: EpochMillis | None = None
    # FAIL-SAFE: lo que no se declara NO cuenta como completo (cond.3, como el
    # footprint).
    is_complete: bool = False
    cadence_ms: int = Field(ge=1)
    formula_version: int = Field(ge=1)

    @model_validator(mode="after")
    def _snapshot_coherente(self) -> "OrderbookSnapshotPayload":
        # Ventana alineada al intervalo (los seis timeframes dividen el dia), igual que
        # la vela y el footprint: una ventana desalineada es un dato de otro flujo o un
        # bug.
        duration = self.timeframe.duration_ms
        if self.open_time % duration != 0:
            msg = (
                f"open_time {self.open_time} no alineado con el intervalo "
                f"{self.timeframe.value} ({duration} ms)."
            )
            raise ValueError(msg)
        if not (self.open_time < self.close_time <= self.open_time + duration):
            msg = (
                f"close_time {self.close_time} fuera de la ventana "
                f"[{self.open_time}, {self.open_time + duration}]."
            )
            raise ValueError(msg)

        # kind y sample_time atados: el frontier es la foto de la barra (sin instante);
        # el sample es una muestra en un instante DENTRO de la ventana.
        if self.kind is MarketOrderbookSnapshotKind.FRONTIER:
            if self.sample_time is not None:
                msg = "un snapshot 'frontier' no lleva sample_time."
                raise ValueError(msg)
        else:
            if self.sample_time is None:
                msg = "un snapshot 'sample' exige sample_time."
                raise ValueError(msg)
            if not (self.open_time <= self.sample_time <= self.close_time):
                msg = (
                    f"sample_time {self.sample_time} fuera de la ventana "
                    f"[{self.open_time}, {self.close_time}]."
                )
                raise ValueError(msg)

        # Cada lado, dentro del top-K y ORDENADO sin nivel repetido: bids DESCENDENTE
        # (mejor precio primero), asks ASCENDENTE. Un orden determinista es lo que hace
        # el snapshot reproducible bit a bit.
        if len(self.bids) > self.depth_k or len(self.asks) > self.depth_k:
            msg = (
                f"un lado excede depth_k={self.depth_k} "
                f"(bids={len(self.bids)}, asks={len(self.asks)})."
            )
            raise ValueError(msg)
        bid_prices = [level.price for level in self.bids]
        if any(bid_prices[i] <= bid_prices[i + 1] for i in range(len(bid_prices) - 1)):
            msg = "los bids deben ir por precio DESCENDENTE y sin repetir nivel."
            raise ValueError(msg)
        ask_prices = [level.price for level in self.asks]
        if any(ask_prices[i] >= ask_prices[i + 1] for i in range(len(ask_prices) - 1)):
            msg = "los asks deben ir por precio ASCENDENTE y sin repetir nivel."
            raise ValueError(msg)

        # NO VACIO (5.21): un snapshot sin un solo nivel no es un libro. Si ambos lados
        # estan vacios, no hay nada que persistir ni publicar.
        if not self.bids and not self.asks:
            msg = "snapshot vacio: un libro sin bids ni asks no es un hecho (5.21)."
            raise ValueError(msg)
        return self

    def stream_key(self) -> str:
        """stream_key del flujo de orderbook al que pertenece (ADR-003/014).

        SIN timeframe: la clave de orderbook no lo lleva (su granularidad es
        depth/channel, MarketStreamKey lo prohibe). El timeframe de la barra as-of vive
        en la idempotency_key, no en la identidad del stream.
        """
        return MarketStreamKey(
            exchange=self.exchange,
            market_type=self.market_type,
            symbol=self.symbol,
            data_kind=MarketDataKind.ORDERBOOK,
        ).as_stream_key()

    def idempotency_key(self, kind: MarketOrderbookSnapshotKind) -> str:
        """idempotency/cache_key de este snapshot (ADR-003, cond.1).

        Incluye K, cadencia, ventana y formula_version. Se pasa el kind para el que se
        computa (debe coincidir con self.kind: un frontier no se identifica como
        sample).
        """
        if kind is not self.kind:
            msg = (
                f"idempotency_key pedida para {kind.value} pero el snapshot "
                f"es {self.kind.value}."
            )
            raise ValueError(msg)
        return orderbook_snapshot_idempotency_key(
            kind=kind,
            stream_key=self.stream_key(),
            timeframe=self.timeframe,
            open_time=self.open_time,
            sample_time=self.sample_time,
            depth_k=self.depth_k,
            cadence_ms=self.cadence_ms,
            formula_version=self.formula_version,
        )


class OrderbookResyncedPayload(EventPayload):
    """market.orderbook_resynced: el libro perdio continuidad y se REINICIO (P07c).

    Su PROPIO hecho publicado, no una correccion (no hay candle_corrected para el
    libro): un resync dice que entre from_sequence (lo ultimo bueno) y to_sequence
    (donde reanudo) hubo un hueco, y que el estado se reconstruyo desde una foto nueva.
    to_sequence es None cuando el extremo es DESCONOCIDO (el motor no supo acotar donde
    reanudo): fail-safe, un hueco abierto por ese lado.
    """

    model_config = ConfigDict(extra="forbid")

    exchange: str = Field(pattern=EXCHANGE_PATTERN)
    market_type: MarketType
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    from_sequence: int = Field(ge=0)
    to_sequence: int | None = None
    reason: str = Field(min_length=1, max_length=64)
    event_time: EpochMillis

    @model_validator(mode="after")
    def _resync_coherente(self) -> "OrderbookResyncedPayload":
        # Si el extremo de reanudacion se conoce, va DESPUES del ultimo bueno: un hueco
        # no puede reanudar antes de donde empezo. NULL (desconocido) no se compara.
        if self.to_sequence is not None and self.to_sequence < self.from_sequence:
            msg = (
                f"to_sequence ({self.to_sequence}) < from_sequence "
                f"({self.from_sequence}): un hueco no reanuda antes de empezar."
            )
            raise ValueError(msg)
        return self

    def stream_key(self) -> str:
        """stream_key del flujo de orderbook al que pertenece (ADR-003/014). Sin
        timeframe.
        """
        return MarketStreamKey(
            exchange=self.exchange,
            market_type=self.market_type,
            symbol=self.symbol,
            data_kind=MarketDataKind.ORDERBOOK,
        ).as_stream_key()

    def idempotency_key(self) -> str:
        """idempotency_key de este resync (ADR-003): el mismo hueco es un solo hecho."""
        return orderbook_resynced_idempotency_key(
            stream_key=self.stream_key(),
            from_sequence=self.from_sequence,
            to_sequence=self.to_sequence,
        )
