"""Familia market.* : identidad de stream y velas (ADR-014, ADR-007, ADR-004).

Declara TRES cosas:
- El vocabulario de identidad de un flujo publico de mercado: exchange,
  tipo de mercado, simbolo CANONICO, clase de dato y granularidad; su
  composicion es la MarketStreamKey de ADR-014.
- La derivacion DETERMINISTA del stream_key del envelope (ADR-003) desde
  la MarketStreamKey: dos sujetos que piden el mismo flujo derivan la
  MISMA clave y por eso comparten UN SOLO stream (proposito de ADR-014).
- Los payloads de los tres tipos de vela (ADR-007), sobre
  MaturityAwarePayload: provisional, cerrada y correccion.

SIMBOLO CANONICO, NO NATIVO: cada exchange nombra el mismo mercado a su
manera (BTCUSDT, BTC-USDT, BTC/USDT). El contrato usa SIEMPRE la forma
canonica BASE-QUOTE; la traduccion a la forma nativa es responsabilidad
del adaptador del exchange. Sin esto, el mismo mercado tendria dos
identidades y se abririan dos streams para el mismo flujo.

PRECIOS EN Decimal, NUNCA float: un float binario no representa 0.1 de
forma exacta. Los exchanges publican los precios como texto decimal y asi
se conservan. En la cadena de ejecucion (M5) esto es dinero.

ENTRADA NO CONFIABLE (ADR-006): los datos vienen de un tercero. El
contrato rechaza en el borde el precio no finito (NaN/Infinity), el
precio no positivo, el volumen negativo, la vela desalineada con su
intervalo, el rango OHLC incoherente y la vela cuyo estado de madurez no
concuerda con su tipo de evento.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from source.families.maturity import MaturityAwarePayload
from source.time import EpochMillis, MaturityState


class MarketCandleEventType(StrEnum):
    """Tipos de evento de vela (market.*), ADR-007."""

    CANDLE_UPDATED = "market.candle_updated"
    CANDLE_CLOSED = "market.candle_closed"
    CANDLE_CORRECTED = "market.candle_corrected"


class MarketDataKind(StrEnum):
    """Clase de dato de mercado (data_family de ADR-014).

    CANDLES es un flujo publico del exchange. TRADES es el flujo publico de
    operaciones individuales que P07b suscribe. FOOTPRINT es la clase DERIVADA
    (no un flujo del exchange): el footprint por barra que P07b agrega de los
    trades. Orderbook (P07c) y ticker entraran cuando exista quien los produzca
    y consuma; declararlos hoy seria vocabulario muerto. Todos los reservo
    ADR-014 ("timeframe para candles, depth/channel para orderbook, tipo para
    trades/ticker"): anadirlos es la extension ADITIVA prevista, no un cambio
    estructural de MarketStreamKey.
    """

    CANDLES = "candles"
    TRADES = "trades"
    FOOTPRINT = "footprint"


class MarketType(StrEnum):
    """Tipo de mercado. Solo SPOT en v5.0 (derivados: fuera de alcance)."""

    SPOT = "spot"


class AggressorSide(StrEnum):
    """Lado AGRESOR (taker) de un trade individual (I-04 Parte 1, EXP-M3-01).

    En cripto el exchange PUBLICA quien fue el taker (Binance `m`, Bybit `S`,
    OKX `side`), asi que el lado es un HECHO exacto y determinista, no una
    estimacion: de ahi que el footprint salga reproducible bit a bit. El
    adaptador de cada exchange traduce su flag a este enum, igual que traduce
    'x' a is_closed; la regla de tick queda SOLO como fallback degradado
    documentado si algun dia faltara el flag.
    """

    BUY = "buy"
    SELL = "sell"


class Timeframe(StrEnum):
    """Granularidad de vela. Conjunto CERRADO y ampliable (ADR-005).

    Los seis son DIVISORES EXACTOS del dia. Gracias a eso vale una
    invariante universal: el inicio de una vela SIEMPRE cae en una
    frontera exacta de su intervalo contada desde epoch. Un timeframe
    semanal o mensual romperia esa invariante (su frontera no es un
    divisor del dia) y exigiria una regla de alineacion distinta: entra
    cuando se necesite, no antes.
    """

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"

    @property
    def duration_ms(self) -> int:
        """Duracion del intervalo en milisegundos."""
        return _TIMEFRAME_DURATION_MS[self]


_TIMEFRAME_DURATION_MS: dict[Timeframe, int] = {
    Timeframe.M1: 60_000,
    Timeframe.M5: 300_000,
    Timeframe.M15: 900_000,
    Timeframe.H1: 3_600_000,
    Timeframe.H4: 14_400_000,
    Timeframe.D1: 86_400_000,
}

# Identificador de exchange: minusculas, sin espacios. Es un identificador
# tecnico estable, no un nombre comercial.
EXCHANGE_PATTERN = r"^[a-z][a-z0-9_]{1,31}$"

# Simbolo CANONICO BASE-QUOTE en mayusculas (BTC-USDT). NUNCA la forma
# nativa del exchange.
# min 1 (Binance tiene el ticker 'T', Threshold); max 20 por meme-tokens largos. El
# {2,15} original era una suposicion sin verificar que la validacion en caliente sobre
# datos reales de Binance desmintio (par TUSDT -> T-USDT).
SYMBOL_PATTERN = r"^[A-Z0-9]{1,20}-[A-Z0-9]{1,20}$"


class MarketStreamKey(BaseModel):
    """Identidad de un flujo publico de mercado (ADR-014).

    exchange + tipo de mercado + simbolo canonico + clase de dato +
    granularidad aplicable. Es la unidad de la que el subscription manager
    lleva ref-count: UN stream por MarketStreamKey, compartido cross-tenant
    (scope=public_market, sin tenant_id, ADR-011).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    exchange: str = Field(pattern=EXCHANGE_PATTERN)
    market_type: MarketType
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    data_kind: MarketDataKind
    timeframe: Timeframe | None = None

    @model_validator(mode="after")
    def _granularidad_aplicable(self) -> "MarketStreamKey":
        # candles y footprint van bucketeados por barra: exigen timeframe. El
        # flujo de trades es continuo (no se bucketea a nivel de stream): lo
        # prohibe. Asi la clave de cada clase de dato es inequivoca (ADR-014).
        needs_timeframe = self.data_kind in (
            MarketDataKind.CANDLES,
            MarketDataKind.FOOTPRINT,
        )
        if needs_timeframe and self.timeframe is None:
            msg = f"data_kind={self.data_kind.value} exige timeframe (ADR-014)."
            raise ValueError(msg)
        if self.data_kind is MarketDataKind.TRADES and self.timeframe is not None:
            msg = (
                "data_kind=trades no admite timeframe: el flujo de trades no se "
                "bucketea a nivel de stream (el footprint si, ADR-014)."
            )
            raise ValueError(msg)
        return self

    def as_stream_key(self) -> str:
        """stream_key DETERMINISTA del envelope (ADR-003/ADR-014).

        Misma MarketStreamKey -> mismo stream_key, siempre. De ahi que dos
        tenants interesados en el mismo flujo compartan un unico stream.
        """
        parts = [
            "market",
            self.data_kind.value,
            self.exchange,
            self.market_type.value,
            self.symbol,
        ]
        if self.timeframe is not None:
            parts.append(self.timeframe.value)
        return ":".join(parts)

    @classmethod
    def parse(cls, stream_key: str) -> "MarketStreamKey":
        """Inverso de as_stream_key(): de la clave textual a la identidad tipada.

        La ventanilla de demanda (CA-P07-D) devuelve la CLAVE, no la identidad:
        no puede devolver mas, porque devolver mas seria revelar mas. Para
        suscribirse a un exchange hay que volver a la identidad tipada.

        ESTRICTO A PROPOSITO: un prefijo desconocido, un numero de partes que no
        cuadra, un valor fuera de los enums cerrados o un simbolo no canonico se
        RECHAZAN. La clave llega de la base, pero un parser permisivo es un
        parser que un dia acepta basura y abre un stream que nadie pidio.
        """
        parts = stream_key.split(":")
        if len(parts) not in (5, 6):
            msg = (
                f"stream_key invalido: {stream_key!r}. Formato esperado "
                "market:<data_kind>:<exchange>:<market_type>:<symbol>[:<timeframe>]."
            )
            raise ValueError(msg)
        if parts[0] != "market":
            msg = f"stream_key invalido: {stream_key!r}. No empieza por 'market'."
            raise ValueError(msg)

        # Cada parte se valida contra su enum CERRADO; un valor desconocido lanza
        # ValueError por si solo. El modelo revalida ademas los patrones.
        timeframe = Timeframe(parts[5]) if len(parts) == 6 else None
        return cls(
            data_kind=MarketDataKind(parts[1]),
            exchange=parts[2],
            market_type=MarketType(parts[3]),
            symbol=parts[4],
            timeframe=timeframe,
        )


def candle_idempotency_key(
    *,
    event_type: MarketCandleEventType,
    stream_key: str,
    open_time: int,
    maturity_state: MaturityState,
    correction_revision: int | None = None,
) -> str:
    """idempotency_key de una vela: UNICA GLOBALMENTE POR CONSTRUCCION.

    Formula (dictamen de Central, P07-A): tipo de evento + stream_key (que
    ya lleva dentro exchange, tipo de mercado, simbolo y timeframe) +
    ventana de la vela (open_time) + discriminador de madurez, mas el
    numero de revision cuando es una correccion.

    POR QUE LA REVISION: si una vela se corrige DOS veces (dos backfills
    distintos), sin revision ambas correcciones producirian la MISMA clave
    y el indice UNIQUE de la outbox (P02b) se tragaria la segunda EN
    SILENCIO. Con revision, cada correccion es un hecho distinto.

    Los publicos no llevan tenant (scope=public_market, ADR-011): la clave
    no necesita cualificarse por tenant, y no debe.
    """
    if maturity_state is MaturityState.CORRECTION and correction_revision is None:
        msg = "una correccion exige correction_revision (>=1)."
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


class CandlePayload(MaturityAwarePayload):
    """Payload OHLCV de una vela (ADR-007). Base de los tres tipos.

    No se registra por si misma en EVENT_PAYLOAD_REGISTRY: cada event_type
    apunta a su subclase concreta, que FIJA su maturity_state. Asi, una
    vela cerrada que llegase marcada como provisional la rechaza el
    CONTRATO, no un if perdido en el codigo.
    """

    model_config = ConfigDict(extra="forbid")

    exchange: str = Field(pattern=EXCHANGE_PATTERN)
    market_type: MarketType
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    timeframe: Timeframe
    open_time: EpochMillis
    close_time: EpochMillis
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    correction_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _dato_de_tercero_coherente(self) -> "CandlePayload":
        # Precios: finitos y positivos. Un NaN o un Infinity de un exchange
        # NO puede entrar en el sistema (ADR-006: entrada no confiable).
        for name in ("open", "high", "low", "close"):
            value: Decimal = getattr(self, name)
            if not value.is_finite():
                msg = f"{name}: precio no finito (NaN/Infinity) rechazado."
                raise ValueError(msg)
            if value <= 0:
                msg = f"{name}: precio no positivo rechazado ({value})."
                raise ValueError(msg)
        if not self.volume.is_finite() or self.volume < 0:
            msg = f"volume: volumen no finito o negativo rechazado ({self.volume})."
            raise ValueError(msg)

        # Rango OHLC coherente.
        if self.high < self.low:
            msg = f"high ({self.high}) < low ({self.low}): rango incoherente."
            raise ValueError(msg)
        if self.high < max(self.open, self.close):
            msg = "high es menor que open/close: rango incoherente."
            raise ValueError(msg)
        if self.low > min(self.open, self.close):
            msg = "low es mayor que open/close: rango incoherente."
            raise ValueError(msg)

        # Alineacion de la ventana: el inicio cae en frontera exacta del
        # intervalo (los seis timeframes dividen el dia). Una vela
        # desalineada es un dato corrupto o de otro flujo.
        duration = self.timeframe.duration_ms
        if self.open_time % duration != 0:
            msg = (
                f"open_time {self.open_time} no esta alineado con el intervalo "
                f"{self.timeframe.value} ({duration} ms)."
            )
            raise ValueError(msg)
        if not (self.open_time < self.close_time <= self.open_time + duration):
            msg = (
                f"close_time {self.close_time} fuera de la ventana de la vela "
                f"[{self.open_time}, {self.open_time + duration}]."
            )
            raise ValueError(msg)

        # La revision solo existe en una correccion.
        if (
            self.correction_revision is not None
            and self.maturity_state is not MaturityState.CORRECTION
        ):
            msg = "correction_revision solo aplica a maturity_state=correction."
            raise ValueError(msg)
        return self

    def stream_key(self) -> str:
        """stream_key del flujo al que pertenece esta vela (ADR-003/014)."""
        return MarketStreamKey(
            exchange=self.exchange,
            market_type=self.market_type,
            symbol=self.symbol,
            data_kind=MarketDataKind.CANDLES,
            timeframe=self.timeframe,
        ).as_stream_key()

    def idempotency_key(self, event_type: MarketCandleEventType) -> str:
        """idempotency_key de esta vela para su tipo de evento (ADR-003)."""
        return candle_idempotency_key(
            event_type=event_type,
            stream_key=self.stream_key(),
            open_time=self.open_time,
            maturity_state=self.maturity_state,
            correction_revision=self.correction_revision,
        )


class CandleUpdatedPayload(CandlePayload):
    """market.candle_updated: vela PROVISIONAL, en formacion (ADR-007).

    Vista viva: NO es historico canonico y puede perderse sin dano (la
    autoridad es candle_closed). Las reglas y senales NUNCA se evaluan
    sobre ella (invariante firmado en el dictamen P07-A).
    """

    @model_validator(mode="after")
    def _madurez_del_tipo(self) -> "CandleUpdatedPayload":
        if self.maturity_state is not MaturityState.PROVISIONAL:
            msg = "market.candle_updated exige maturity_state=provisional."
            raise ValueError(msg)
        return self


class CandleClosedPayload(CandlePayload):
    """market.candle_closed: vela CERRADA, hecho canonico del intervalo.

    Es el unico dato sobre el que se evaluan reglas y senales
    (determinista y reproducible). Se persiste en el historico append-only
    y se publica por OUTBOX en la MISMA transaccion (dictamen P07-A).
    """

    @model_validator(mode="after")
    def _madurez_del_tipo(self) -> "CandleClosedPayload":
        if self.maturity_state is not MaturityState.CLOSED:
            msg = "market.candle_closed exige maturity_state=closed."
            raise ValueError(msg)
        return self


class CandleCorrectedPayload(CandlePayload):
    """market.candle_corrected: correccion de una vela ya cerrada (ADR-007).

    No muta el original (append-only): es un hecho NUEVO que referencia por
    corrects_idempotency_key la vela corregida (regla heredada de
    MaturityAwarePayload) y numera su revision.

    correction_revision es OBLIGATORIO (>=1) en este tipo (CA-P08-09): estrecha el
    int|None de CandlePayload a un int requerido. Sin el, dos correcciones de la misma
    vela colisionarian en la idempotency_key y la outbox (indice UNIQUE, P02b) se
    tragaria la segunda EN SILENCIO. La obligatoriedad la impone ahora el TIPO del campo
    -- no un validador aparte -- de modo que el schema generado lo refleja y ningun
    consumidor la recibe como null. Correccion pre-consumidor (None nunca fue un evento
    valido: ningun productor lo emitio ni ningun consumidor lo acepto).
    """

    correction_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def _madurez_del_tipo(self) -> "CandleCorrectedPayload":
        if self.maturity_state is not MaturityState.CORRECTION:
            msg = "market.candle_corrected exige maturity_state=correction."
            raise ValueError(msg)
        return self


class IntentSourceType(StrEnum):
    """Origen de un SubscriptionIntent (ADR-014).

    Taxonomia FIJADA POR ADR-014 ("watchlists, widgets/layouts, AlertRules,
    TradingSignalRules, ExecutionPlans, DataSources y tareas de backfill/replay").
    Se declara entera, igual que P01 declaro las once familias de evento antes de
    que existieran sus productores: es VOCABULARIO, no codigo muerto. Los
    productores reales llegan en P08 (reglas), P10b (ejecucion) y P12b (widgets).
    """

    ALERT_RULE = "alert_rule"
    TRADING_SIGNAL_RULE = "trading_signal_rule"
    WATCHLIST = "watchlist"
    WIDGET = "widget"
    EXECUTION_PLAN = "execution_plan"
    DATASOURCE = "datasource"
    BACKFILL = "backfill"


class StreamScope(StrEnum):
    """Alcance del flujo al que apunta un interes (ADR-011/ADR-014).

    Espeja el scope del envelope (ADR-003): PUBLIC_MARKET es un flujo publico
    COMPARTIDO cross-tenant (un solo stream para todos los interesados); USER es
    un flujo PRIVADO por-usuario (BYOC), aislado por RLS y gateado por geo/policy.
    """

    PUBLIC_MARKET = "public_market"
    USER = "user"


# Tope TECNICO de intereses por sujeto. NO es la cuota comercial por plan (eso es
# P11 + el gate): es el limite de SUPERVIVENCIA de la plataforma. Sin el, "todos
# los pares seleccionables" significa que un solo usuario puede pedir miles de
# streams y tumbar la ingesta: es un DoS gratis.
MAX_INTENTS_PER_SUBJECT = 200


class SubscriptionIntent(BaseModel):
    """Un interes declarado por un sujeto sobre un flujo de mercado (ADR-014).

    Es la FUENTE DE VERDAD de la demanda. El ref-count del subscription manager
    se RECONSTRUYE desde estos intereses tras un reinicio; el ref-count es estado
    operativo, no fuente de verdad (ADR-014).

    expires_at NULL = interes PERSISTENTE (una regla o una alerta no caduca porque
    el usuario cierre el navegador). Con valor = interes EFIMERO (un widget), para
    que no queden suscripciones zombis consumiendo una conexion al exchange.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent_id: UUID
    tenant_id: UUID
    user_id: UUID
    stream_scope: StreamScope
    stream_key: MarketStreamKey
    source_type: IntentSourceType
    source_ref: str = Field(min_length=1, max_length=200)
    priority: int = Field(default=100, ge=1, le=1000)
    expires_at: EpochMillis | None = None
    created_at: EpochMillis
    updated_at: EpochMillis

    def market_stream_key(self) -> str:
        """Clave derivada del flujo (identica para todos los que piden lo mismo)."""
        return self.stream_key.as_stream_key()


@dataclass(frozen=True, slots=True)
class RawCandle:
    """Una vela TAL COMO LLEGA del exchange: NEUTRAL y NO VALIDADA.

    RawCandle NO es un contrato validado: es el dato CRUDO del borde, tal como lo
    escupe un exchange, y por definicion puede ser basura. Vive en contracts porque
    lo produce infra (los adaptadores de exchange) y lo consume platform (la
    normalizacion), y esas dos capas NO PUEDEN VERSE entre si (hermanos
    independientes). Su validacion ocurre en platform/market/normalize.py, que es la
    unica frontera de confianza.

    Por eso es una dataclass y NO un modelo Pydantic: validar aqui daria la falsa
    impresion de que el dato ya es de fiar.

    Los precios viajan como TEXTO, que es como los publican los exchanges y como hay
    que conservarlos (un float binario no representa 0.1 exacto; en M5 esto es
    dinero). El adaptador de cada exchange traduce SU formato a esta forma comun, y
    nada mas: NO valida, NO decide, NO limpia. Si cada adaptador validara lo suyo,
    tendriamos una validacion por exchange y una de ellas seria la mas floja.
    """

    exchange: str
    market_type: str
    symbol: str  # CANONICO ya (BTC-USDT): el adaptador ya tradujo el nativo.
    timeframe: str
    open_time_ms: int
    close_time_ms: int
    open: str
    high: str
    low: str
    close: str
    volume: str
    is_closed: bool  # lo dice el exchange (Binance 'x', OKX/Bybit 'confirm').
    # ADR-007: event_time LO FIJA EL ORIGEN DEL HECHO, nunca lo inventa quien procesa.
    # Es el instante que el EXCHANGE pone en su mensaje (Binance 'E'). Sin este campo,
    # una vela PROVISIONAL no tendria event_time legitimo y habria que inventarselo:
    # el sistema estaria fechando como suyo un hecho que no ocurrio cuando el dice.
    event_time_ms: int
    source_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class RawTrade:
    """Un trade individual TAL COMO LLEGA del exchange: NEUTRAL y NO VALIDADO.

    Gemelo de RawCandle para la familia trades (P07b). Lo produce infra (los
    adaptadores de exchange) y lo consume platform (normalize), la unica frontera
    de confianza; por eso vive aqui y es dataclass, no un modelo Pydantic (validar
    aqui daria la falsa impresion de que el dato ya es de fiar).

    price y qty viajan como TEXTO, como los publica el exchange (un float binario
    no representa 0.1 exacto; en M5 esto es dinero). aggressor_side llega como
    'buy'|'sell' YA TRADUCIDO por el adaptador desde el flag del exchange (Binance
    `m`, Bybit `S`, OKX `side`), igual que is_closed se traduce de 'x': el adaptador
    traduce, NO decide, NO valida. trade_id es el identificador del exchange y es la
    base del orden determinista entre trades del mismo milisegundo (P07b Tanda 3).
    """

    exchange: str
    market_type: str
    symbol: str  # CANONICO ya (BTC-USDT): el adaptador ya tradujo el nativo.
    trade_id: str
    price: str
    qty: str
    aggressor_side: str  # 'buy' | 'sell', ya traducido del flag del exchange.
    # ADR-007: event_time LO FIJA EL ORIGEN DEL HECHO. Es el ts del propio trade en
    # el mensaje del exchange (Binance 'T', Bybit 'T', OKX 'ts').
    event_time_ms: int
    source_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class LastSeenTrade:
    """El ultimo trade que YA tenemos persistido de un flujo: el punto desde el que hay
    que rellenar tras una reconexion.

    Vive aqui por el MISMO motivo que RawTrade, no por comodidad: lo PRODUCE platform
    (el motor se lo pide al store) y lo CONSUME infra (cada conector decide con el si su
    relleno llego a tocar lo que ya teniamos), y esas dos capas NO PUEDEN VERSE entre si
    (hermanas independientes). Ponerlo en el puerto de platform obligaria a los
    adaptadores de exchange a importar platform, que es justo lo que el contrato de
    capas prohibe.

    Campos a None cuando el flujo no tiene ni una fila persistida: es la PRIMERA
    conexion y no hay hueco que cubrir, porque no se puede haber perdido lo que nunca
    se tuvo.
    """

    trade_id: str | None
    event_time_ms: int | None


@dataclass(frozen=True, slots=True)
class TradeBackfillResult:
    """Lo que un conector devuelve tras rellenar el hueco de una reconexion (ADR-014).

    BACKFILL ACOTADO + HUECO EXPLICITO. Cada exchange rellena por REST publico hasta el
    techo de SU endpoint, que no es negociable ni configurable: es lo que su API da. Si
    el hueco fue mas grande que ese techo, la parte antigua NO se recupera JAMAS, y
    fingir lo contrario seria mentir sobre el historico. Por eso el resultado no es solo
    "los trades del relleno": es tambien la RESPUESTA HONESTA a si el hueco quedo
    cubierto.

    Cada conector calcula la cobertura con el criterio que su exchange permite (Binance
    por id monotono; otros por event_time), pero al nucleo le llega SIEMPRE esta forma
    comun: por eso el motor no sabe -- ni tiene que saber -- de que exchange viene.

    FAIL-SAFE: covered=False ante cualquier incertidumbre. Un hueco declarado de mas
    marca barras como incompletas sin motivo, lo cual es feo; un hueco NO declarado
    publica una barra de footprint a la que le faltan trades como si estuviera completa,
    lo cual es una mentira sobre el mercado.

    Cuando covered=True los dos limites van a None: no hay hueco que delimitar.
    """

    raw_trades: Sequence[RawTrade]
    covered: bool
    gap_from_event_time_ms: int | None
    gap_to_event_time_ms: int | None


@dataclass(frozen=True, slots=True)
class Instrument:
    """Un par del catalogo de un exchange, tal como lo declara el exchange.

    Mismo motivo que RawCandle para vivir aqui: lo produce infra y lo consume
    platform. Tampoco es un contrato validado.
    """

    exchange: str
    market_type: str
    symbol: str  # canonico
    native_symbol: str  # como lo llama el exchange
    active: bool


@dataclass(frozen=True, slots=True)
class StoredCandle:
    """Lo que YA hay guardado para una ventana (para decidir dedup vs correccion).

    Vive aqui por el mismo motivo que RawCandle: lo produce infra (lee el historico)
    y lo consume platform (el motor de ingesta decide con el), y esas dos capas NO
    pueden verse entre si.
    """

    idempotency_key: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    max_correction_revision: int  # 0 si nunca se corrigio

    def same_values_as(self, payload: CandlePayload) -> bool:
        """Mismos OHLCV: el exchange no corrigio nada, es un DUPLICADO.

        Es la pregunta que separa el caso NORMAL (una reconexion trae de nuevo velas
        que ya teniamos) del caso GRAVE (el exchange cambio el pasado).
        """
        return (
            self.open == payload.open
            and self.high == payload.high
            and self.low == payload.low
            and self.close == payload.close
            and self.volume == payload.volume
        )
