"""Familia market.* : trade individual y footprint (ADR-014, ADR-007, ADR-006).

P07b anade a la familia market.* (que YA existe, ADR-004: NO es familia nueva, no
dispara la regla 5.12) dos cosas:

- El TRADE INDIVIDUAL normalizado (MarketTrade): el hecho crudo del exchange ya
  VALIDADO en el borde (ADR-006). NO se publica al bus uno a uno (seria la avalancha
  que I-02 advirtio): se persiste (retencion por familia) y alimenta el footprint.
  Vive en contracts porque lo produce platform (normalize) y lo consume infra (el
  store), y esas dos capas hermanas no pueden verse.

- El FOOTPRINT por barra: market.footprint_closed y market.footprint_corrected, sobre
  MaturityAwarePayload igual que las velas. La CELDA es (nivel de precio x barra) con
  volumen agresor comprador y vendedor y su delta; la BARRA lleva su delta total. Es
  la BASE de orderflow/absorcion/volume profile, que construye P08c (no P07b).

LADO AGRESOR EXACTO (I-04 Parte 1): en cripto el exchange PUBLICA quien fue el taker
(Binance `m`, Bybit `S`, OKX `side`), asi que la clasificacion es DETERMINISTA, no
estimada. El adaptador traduce el flag a AggressorSide (buy|sell); la regla de tick
queda solo como fallback degradado documentado si faltara el flag.

REPRODUCIBILIDAD BIT A BIT (cierra los dos NO VERIFICADO de I-04 1.1/4.4): el
footprint se agrega de forma determinista en platform (P07b Tanda 3) fijando (a) un
orden total de los trades del mismo milisegundo y (b) el bucketing de cada trade a su
barra por el timestamp del trade, con la dimension de alineacion declarada en la
clave. El CONTRATO de aqui garantiza la FORMA -- celdas ordenadas por precio, sin
nivel repetido, totales de barra cuadrados con las celdas y delta coherente --; la
maquinaria de agregacion es de Tanda 3.

PRECIOS Y VOLUMENES EN Decimal, NUNCA float: el exchange los publica como texto
decimal y asi se conservan.
"""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from source.families.market import (
    EXCHANGE_PATTERN,
    SYMBOL_PATTERN,
    AggressorSide,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    Timeframe,
)
from source.families.maturity import MaturityAwarePayload
from source.time import EpochMillis, MaturityState


class MarketFootprintEventType(StrEnum):
    """Tipos de evento de footprint (market.*), ADR-007.

    Solo CLOSED y CORRECTED en v5.0: el footprint es un hecho canonico por barra
    (como candle_closed) y su correccion append-only (como candle_corrected). No hay
    footprint provisional en vivo: nadie lo consume aun y construirlo seria "por si
    acaso" (prohibido).
    """

    FOOTPRINT_CLOSED = "market.footprint_closed"
    FOOTPRINT_CORRECTED = "market.footprint_corrected"


def footprint_idempotency_key(
    *,
    event_type: MarketFootprintEventType,
    stream_key: str,
    open_time: int,
    maturity_state: MaturityState,
    correction_revision: int | None = None,
) -> str:
    """idempotency_key de un footprint de barra: UNICA GLOBALMENTE POR CONSTRUCCION.

    Misma formula que la vela (dictamen P07-A): tipo de evento + stream_key (que ya
    lleva exchange, tipo de mercado, simbolo y timeframe) + ventana (open_time) +
    discriminador de madurez, mas el numero de revision cuando es una correccion. Sin
    la revision, dos correcciones de la misma barra colisionarian y el indice UNIQUE
    de la outbox (P02b) se tragaria la segunda EN SILENCIO. Los publicos no llevan
    tenant (scope=public_market, ADR-011).
    """
    if maturity_state is MaturityState.CORRECTION and correction_revision is None:
        msg = "una correccion de footprint exige correction_revision (>=1)."
        raise ValueError(msg)
    if (
        maturity_state is not MaturityState.CORRECTION
        and correction_revision is not None
    ):
        msg = "correction_revision solo aplica a maturity_state=correction."
        raise ValueError(msg)
    parts = [event_type.value, stream_key, str(open_time), maturity_state.value]
    if correction_revision is not None:
        parts.append(f"r{correction_revision}")
    return "|".join(parts)


class MarketTrade(BaseModel):
    """Un trade individual normalizado y VALIDADO en el borde (ADR-006).

    Forma de confianza del trade crudo (RawTrade): precio y tamano finitos y
    positivos, lado agresor en el enum cerrado. NO es un EventPayload: no se publica
    al bus (evita la avalancha, I-02), se persiste y alimenta el footprint. trade_id
    es el identificador del exchange, base del orden determinista entre trades del
    mismo milisegundo (P07b Tanda 3).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    exchange: str = Field(pattern=EXCHANGE_PATTERN)
    market_type: MarketType
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    trade_id: str = Field(min_length=1, max_length=64)
    price: Decimal
    qty: Decimal
    aggressor_side: AggressorSide
    event_time: EpochMillis
    source_sequence: int | None = None

    @model_validator(mode="after")
    def _dato_de_tercero_coherente(self) -> "MarketTrade":
        # Precio: finito y positivo. Un NaN/Infinity de un exchange NO entra (ADR-006).
        if not self.price.is_finite() or self.price <= 0:
            msg = f"price: precio no finito o no positivo rechazado ({self.price})."
            raise ValueError(msg)
        # Tamano: finito y POSITIVO. Un trade con tamano 0 no es un trade.
        if not self.qty.is_finite() or self.qty <= 0:
            msg = f"qty: tamano no finito o no positivo rechazado ({self.qty})."
            raise ValueError(msg)
        return self

    def stream_key(self) -> str:
        """stream_key del flujo de trades al que pertenece (ADR-003/014)."""
        return MarketStreamKey(
            exchange=self.exchange,
            market_type=self.market_type,
            symbol=self.symbol,
            data_kind=MarketDataKind.TRADES,
        ).as_stream_key()


class FootprintCell(BaseModel):
    """Una celda del footprint: un nivel de precio dentro de una barra.

    Volumen agresor comprador y vendedor a ese precio, y su delta (buy - sell). El
    delta se lleva EXPLICITO y se valida contra buy-sell: un consumidor no tiene que
    recalcularlo ni puede recibir uno incoherente.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    price: Decimal
    buy_volume: Decimal
    sell_volume: Decimal
    delta: Decimal

    @model_validator(mode="after")
    def _celda_coherente(self) -> "FootprintCell":
        if not self.price.is_finite() or self.price <= 0:
            msg = f"price: nivel de precio no finito o no positivo ({self.price})."
            raise ValueError(msg)
        for name in ("buy_volume", "sell_volume"):
            value: Decimal = getattr(self, name)
            if not value.is_finite() or value < 0:
                msg = f"{name}: volumen no finito o negativo ({value})."
                raise ValueError(msg)
        if (
            not self.delta.is_finite()
            or self.delta != self.buy_volume - self.sell_volume
        ):
            msg = (
                f"delta ({self.delta}) no coincide con buy-sell "
                f"({self.buy_volume - self.sell_volume})."
            )
            raise ValueError(msg)
        return self


class FootprintPayload(MaturityAwarePayload):
    """Payload del footprint de una barra (ADR-007). Base de closed y corrected.

    No se registra por si misma en EVENT_PAYLOAD_REGISTRY: cada event_type apunta a
    su subclase concreta, que FIJA su maturity_state. Asi un footprint cerrado marcado
    como correccion lo rechaza el CONTRATO, no un if perdido en el codigo.

    is_complete DICE SI LA BARRA VIO TODOS SUS TRADES: True = se capturaron todos los
    trades de la ventana; False = un hueco de reconexion NO cubierto se solapa con esta
    barra, asi que le faltan trades y sus celdas no son la verdad completa del mercado.

    Es ORTOGONAL a maturity_state: una barra puede estar CERRADA (su ventana temporal
    termino) y ser INCOMPLETA a la vez (durante esa ventana el socket estuvo caido mas
    de lo que el REST del exchange pudo rellenar). Por eso NO hay validador que cruce
    los dos campos: cruzarlos inventaria una relacion que no existe.

    EL DEFAULT ES False, Y ES DELIBERADO: fail-safe. Lo que no declara su completitud
    cuenta como INCOMPLETO. Un default True convertiria un olvido del productor en una
    barra que se publica como completa sin serlo, que es exactamente la mentira que este
    campo existe para impedir. El agregador de 3b lo fija SIEMPRE de forma explicita; el
    default solo cubre el olvido, y lo hace hacia el lado seguro.
    """

    model_config = ConfigDict(extra="forbid")

    exchange: str = Field(pattern=EXCHANGE_PATTERN)
    market_type: MarketType
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    timeframe: Timeframe
    open_time: EpochMillis
    close_time: EpochMillis
    cells: tuple[FootprintCell, ...]
    bar_buy_volume: Decimal
    bar_sell_volume: Decimal
    bar_delta: Decimal
    trade_count: int = Field(ge=0)
    # FAIL-SAFE: lo que no se declara NO cuenta como completo (ver docstring).
    is_complete: bool = False
    correction_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _footprint_coherente(self) -> "FootprintPayload":
        # Alineacion de la ventana: el inicio cae en frontera exacta del intervalo,
        # igual que la vela (los seis timeframes dividen el dia).
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

        # Celdas ordenadas por precio ASCENDENTE y sin nivel repetido: orden
        # determinista para que el footprint salga bit a bit igual (I-04).
        prices = [cell.price for cell in self.cells]
        if any(prices[i] >= prices[i + 1] for i in range(len(prices) - 1)):
            msg = (
                "las celdas deben ir ordenadas por precio ascendente y sin repetir "
                "nivel (orden determinista del footprint)."
            )
            raise ValueError(msg)

        # Totales de barra cuadrados con la suma de las celdas: sin esto, un
        # consumidor veria una barra que no es la suma de sus celdas.
        sum_buy = sum((cell.buy_volume for cell in self.cells), Decimal(0))
        sum_sell = sum((cell.sell_volume for cell in self.cells), Decimal(0))
        if self.bar_buy_volume != sum_buy:
            msg = (
                f"bar_buy_volume ({self.bar_buy_volume}) != suma de celdas ({sum_buy})."
            )
            raise ValueError(msg)
        if self.bar_sell_volume != sum_sell:
            msg = (
                f"bar_sell_volume ({self.bar_sell_volume}) != suma de celdas "
                f"({sum_sell})."
            )
            raise ValueError(msg)
        if (
            not self.bar_delta.is_finite()
            or self.bar_delta != self.bar_buy_volume - self.bar_sell_volume
        ):
            msg = (
                f"bar_delta ({self.bar_delta}) != bar_buy - bar_sell "
                f"({self.bar_buy_volume - self.bar_sell_volume})."
            )
            raise ValueError(msg)

        # La revision solo existe en una correccion (igual que la vela).
        if (
            self.correction_revision is not None
            and self.maturity_state is not MaturityState.CORRECTION
        ):
            msg = "correction_revision solo aplica a maturity_state=correction."
            raise ValueError(msg)
        return self

    def stream_key(self) -> str:
        """stream_key del flujo de footprint al que pertenece (ADR-003/014)."""
        return MarketStreamKey(
            exchange=self.exchange,
            market_type=self.market_type,
            symbol=self.symbol,
            data_kind=MarketDataKind.FOOTPRINT,
            timeframe=self.timeframe,
        ).as_stream_key()

    def idempotency_key(self, event_type: MarketFootprintEventType) -> str:
        """idempotency_key de este footprint para su tipo de evento (ADR-003)."""
        return footprint_idempotency_key(
            event_type=event_type,
            stream_key=self.stream_key(),
            open_time=self.open_time,
            maturity_state=self.maturity_state,
            correction_revision=self.correction_revision,
        )


class FootprintClosedPayload(FootprintPayload):
    """market.footprint_closed: footprint CERRADO de la barra, hecho canonico.

    Se deriva de trades cerrados y se publica por OUTBOX en la misma transaccion que
    su persistencia (patron de candle_closed, P07-A). Es la base que consumira P08c.
    """

    @model_validator(mode="after")
    def _madurez_del_tipo(self) -> "FootprintClosedPayload":
        if self.maturity_state is not MaturityState.CLOSED:
            msg = "market.footprint_closed exige maturity_state=closed."
            raise ValueError(msg)
        return self


class FootprintCorrectedPayload(FootprintPayload):
    """market.footprint_corrected: correccion append-only por trade tardio (ADR-007).

    No muta el original (append-only): es un hecho NUEVO que referencia por
    corrects_idempotency_key el footprint corregido y numera su revision.
    correction_revision es OBLIGATORIO (>=1), como en CandleCorrectedPayload: estrecha
    el int|None de la base a int requerido para que dos correcciones de la misma barra
    no colisionen en la idempotency_key.
    """

    correction_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def _madurez_del_tipo(self) -> "FootprintCorrectedPayload":
        if self.maturity_state is not MaturityState.CORRECTION:
            msg = "market.footprint_corrected exige maturity_state=correction."
            raise ValueError(msg)
        return self
