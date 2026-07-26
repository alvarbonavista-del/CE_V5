"""EL MOTOR DEL LIBRO L2 CON ESTADO, PURO Y ORDER-DEPENDIENTE (ADR-014, ADR-006).

Primo del motor de trades, y donde se PARECE importa menos que donde se DIFERENCIA. El
motor de trades es CONMUTATIVO y SIN estado: un trade es un hecho unico que se deduplica
por su id y da igual el orden. Este motor es lo contrario: CON estado y
order-dependiente.

- ARRANCA DE UNA FOTO. Un libro no se construye trade a trade: se parte de una FOTO
  completa (seed) y se avanza aplicando DELTAS incrementales. Sin la foto, un delta no
  significa nada (¿cambio respecto a que?).

- EL ORDEN ES LA VERDAD. Los deltas se aplican EN SECUENCIA y la continuidad se valida
  por el numero de secuencia del exchange. Un delta fuera de orden NO es un dato mas: es
  la prueba de que se perdio algo por el medio. Cada exchange encadena a su manera
  (Binance por U/u, OKX por prevSeqId, Bybit por u), asi que la regla de continuidad es
  POR EXCHANGE; el motor no sabe -- ni tiene que saber -- de red ni de formato nativo.

- ANTE UN HUECO, FAIL-SAFE. Si la cadena se rompe, el motor NO adivina lo que falta ni
  sigue como si nada: marca el libro INCOMPLETO (is_complete=False) y SENALA que hace
  falta un resync. NO pide la foto por la red (eso es del cableado, y esta bloqueado):
  la peticion de red no es asunto del motor puro. Un libro con un agujero dentro
  publicado como completo es una mentira sobre el mercado, y en M5 alimenta ordenes
  reales.

- LA CORRECCION ES SUYA. Un resync es el HECHO PROPIO del libro, no una copia de
  candle_corrected: aqui no hay revisiones ni referencias al original. La unica salida
  es el ESTADO EN MEMORIA (bids/asks) + is_complete + la senal de resync.

SIN IO, SIN RED, SIN BASE, SIN RELOJ, SIN HILOS. No importa infra ni components. La foto
y los deltas se los entrega quien llama (el cableado, por el puerto); este modulo solo
mantiene el estado y decide. Es la frontera de confianza del libro (ADR-006): un precio
no numerico, no finito o no positivo, o un tamano negativo, NO entran; o el nivel es
integro, o se rechaza. Jamas se "arregla" un nivel: un dato corregido a ojo es una
mentira con formato correcto.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation
from enum import Enum, StrEnum, auto

from ce_v5.platform.market.errors import MarketError
from source.families.market import (
    RawOrderbookDelta,
    RawOrderbookLevel,
    RawOrderbookSeed,
)


class RawOrderbookRejectionReason(StrEnum):
    """Por que se rechaza una foto o un delta crudo. Conjunto CERRADO (ADR-016).

    Los mismos motivos que un trade, porque son las mismas formas de que un dato de
    tercero sea inaceptable: no es mio (otro flujo), no es un numero, o no cumple el
    contrato. El reason_code es DATO, no texto libre: la UI lo renderiza por i18n y las
    metricas cuentan por motivo.
    """

    SYMBOL_MISMATCH = "symbol_mismatch"  # el mensaje no pertenece a este libro
    MALFORMED_NUMBER = "malformed_number"  # 'abc', '', None en precio o tamano
    # no finito, no positivo, o secuencia ausente:
    CONTRACT_VIOLATION = "contract_violation"


class RawOrderbookRejected(MarketError):
    """El mensaje crudo NO entra en el libro. El motivo va como DATO, no como texto."""

    def __init__(
        self,
        reason: RawOrderbookRejectionReason,
        detail: str,
        expected: str,
    ) -> None:
        super().__init__(f"{reason.value}: {detail} (flujo esperado: {expected})")
        self.reason = reason
        self.detail = detail
        self.expected = expected


class _Continuity(Enum):
    """El veredicto de la regla de continuidad de un delta, ya interpretada por
    exchange.

    Traduce el galimatias de secuencias de cada exchange a un vocabulario COMUN que el
    motor entiende sin saber de que exchange viene:
    """

    APPLY = auto()  # encadena: aplicar los niveles y avanzar la secuencia.
    DUPLICATE = auto()  # ya aplicado (reenvio): ignorar, el libro sigue completo.
    RESET = auto()  # es una FOTO nueva (Bybit): reconstruir el libro desde el.
    NOOP = auto()  # keepalive/mantenimiento OKX: ignorar, NO es hueco.
    GAP = auto()  # la cadena se rompio: hueco -> incompleto + resync.


# La firma comun de las tres reglas de continuidad: dado un delta, la ultima secuencia
# aplicada y si es el PRIMER delta tras la foto, devuelve el veredicto y la nueva
# secuencia. El motor las usa sin saber cual. first_after_seed solo lo mira Binance (su
# primer delta ABARCA la foto, U<=base+1<=u); OKX/Bybit encadenan exacto por WS y lo
# ignoran.
_Classifier = Callable[[RawOrderbookDelta, int, str, bool], "tuple[_Continuity, int]"]


def _stream_id(exchange: str, market_type: str, symbol: str) -> str:
    """La clave textual del flujo del libro, para diagnostico del rechazo (ADR-014)."""
    return ":".join(["market", "orderbook", exchange, market_type, symbol])


def _decimal(valor: str, campo: str, expected: str) -> Decimal:
    """Texto -> Decimal. NUNCA float: un float binario no representa 0.1 exacto.

    Y en el libro esto es la base del precio de ejecucion (M5): un nivel con el precio
    redondeado no es el mismo nivel.
    """
    try:
        return Decimal(valor)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RawOrderbookRejected(
            RawOrderbookRejectionReason.MALFORMED_NUMBER,
            f"el campo '{campo}' no es un numero decimal: {valor!r}",
            expected,
        ) from exc


def _require_seq(valor: int | None, campo: str, expected: str) -> int:
    """Una secuencia AUSENTE es un contrato roto: sin ella no se puede encadenar.

    Cada exchange trae SUS campos de secuencia; que falte el que su regla exige (Binance
    sin U/u, OKX sin prevSeqId, Bybit sin u) no es un delta valido al que le sobre un
    dato: es un delta con el que no se puede decidir si hay hueco, y decidir a ciegas es
    justo lo que este motor existe para no hacer.
    """
    if valor is None:
        raise RawOrderbookRejected(
            RawOrderbookRejectionReason.CONTRACT_VIOLATION,
            f"falta la secuencia '{campo}' que este exchange exige para encadenar",
            expected,
        )
    return valor


def _validated_levels(
    pairs: Sequence[RawOrderbookLevel], lado: str, expected: str
) -> list[tuple[Decimal, Decimal]]:
    """Convierte los niveles crudos en (precio, tamano) Decimal VALIDADOS, o los
    RECHAZA.

    Valida TODOS los niveles ANTES de que el motor toque el libro: asi una foto o un
    delta con un nivel podrido se rechaza ENTERO y el libro se queda como estaba
    (atomicidad). Un tamano 0 es legitimo -- en un delta significa BORRAR el nivel -- y
    se conserva tal cual para que quien aplique decida; lo que no pasa es un precio no
    positivo, un numero no finito (NaN/Infinity) o un tamano negativo.
    """
    validos: list[tuple[Decimal, Decimal]] = []
    for nivel in pairs:
        try:
            raw_price, raw_size = nivel
        except (TypeError, ValueError) as exc:
            raise RawOrderbookRejected(
                RawOrderbookRejectionReason.CONTRACT_VIOLATION,
                f"nivel {lado} malformado (no es [precio, tamano]): {nivel!r}",
                expected,
            ) from exc
        price = _decimal(raw_price, f"{lado}.price", expected)
        size = _decimal(raw_size, f"{lado}.size", expected)
        if not price.is_finite() or price <= 0:
            raise RawOrderbookRejected(
                RawOrderbookRejectionReason.CONTRACT_VIOLATION,
                f"{lado}: precio no finito o no positivo rechazado ({price})",
                expected,
            )
        if not size.is_finite() or size < 0:
            raise RawOrderbookRejected(
                RawOrderbookRejectionReason.CONTRACT_VIOLATION,
                f"{lado}: tamano no finito o negativo rechazado ({size})",
                expected,
            )
        validos.append((price, size))
    return validos


def _book_from_levels(
    levels: list[tuple[Decimal, Decimal]],
) -> dict[Decimal, Decimal]:
    """Construye un lado del libro desde una FOTO: tamano 0 no es un nivel."""
    return {price: size for price, size in levels if size > 0}


def _classify_binance(
    delta: RawOrderbookDelta, last_seq: int, expected: str, first_after_seed: bool
) -> tuple[_Continuity, int]:
    """Binance: el PRIMER delta ABARCA la foto; luego U == u_previo + 1 encadena.

    Procedimiento oficial (I-02): tras la foto REST con lastUpdateId, se descartan los
    eventos con u <= lastUpdateId (reenvios ya cubiertos por la foto). El PRIMER evento
    a aplicar es el que ABARCA la foto: U <= lastUpdateId+1 <= u -- su U puede ser MENOR
    que lastUpdateId+1 (empezo antes de la foto y la cruza), asi que exigir U == last+1
    exacto lo rechazaria como hueco cuando es justo el puente. Desde el primero aplicado
    la continuidad es estricta: U == u_previo + 1. Un evento cuyo U no encadena (ni
    abarca, en el primero) es un salto: hueco (fail-safe).
    """
    u_ini = _require_seq(delta.first_update_id, "first_update_id (U)", expected)
    u_fin = _require_seq(delta.final_update_id, "final_update_id (u)", expected)
    if u_fin <= last_seq:
        return _Continuity.DUPLICATE, last_seq
    # Aqui u_fin > last_seq (u >= lastUpdateId+1): el extremo superior del abarque ya se
    # cumple. El PRIMER delta tras la foto solo necesita que su U no sea POSTERIOR a
    # lastUpdateId+1 (U <= base+1, ABARCA); a partir de ahi, encadenado estricto.
    if first_after_seed:
        encadena = u_ini <= last_seq + 1
    else:
        encadena = u_ini == last_seq + 1
    if encadena:
        return _Continuity.APPLY, u_fin
    return _Continuity.GAP, last_seq


def _classify_okx(
    delta: RawOrderbookDelta, last_seq: int, expected: str, first_after_seed: bool
) -> tuple[_Continuity, int]:
    """OKX: prevSeqId del mensaje == seqId del anterior encadena.

    DOS EXCEPCIONES DE OKX QUE NO SON HUECO, y confundirlas con uno dispararia resyncs
    inutiles: el keepalive (seqId == prevSeqId: el libro no cambio) y el mantenimiento
    (seqId < prevSeqId: OKX reinicio su contador). Ninguna marca el libro incompleto ni
    pide resync. Solo un mensaje que AVANZA (seqId > prevSeqId) cuyo prevSeqId NO
    encadena con lo ultimo aplicado es un hueco de verdad.

    first_after_seed se IGNORA: la foto de OKX llega por el MISMO WS y su seqId es el
    ancla exacta (prevSeqId del primero == seqId de la foto). No hay abarque (Binance).
    """
    del first_after_seed
    seq = _require_seq(delta.seq_id, "seq_id", expected)
    prev = _require_seq(delta.prev_seq_id, "prev_seq_id", expected)
    if seq <= prev:
        # keepalive (==) o mantenimiento (<): NO es hueco, no se toca el libro.
        return _Continuity.NOOP, last_seq
    if prev == last_seq:
        return _Continuity.APPLY, seq
    return _Continuity.GAP, last_seq


def _classify_bybit(
    delta: RawOrderbookDelta, last_seq: int, expected: str, first_after_seed: bool
) -> tuple[_Continuity, int]:
    """Bybit: continuidad de u; un u == 1 (o is_snapshot) es un RESET.

    Bybit reinicia su updateId a 1 y reenvia una FOTO cuando su servicio se reinicia:
    ese mensaje NO encadena, RECONSTRUYE. Fuera de eso, u tiene que ser el siguiente
    exacto; un u ya visto es un reenvio y un salto es un hueco. El campo seq (secuencia
    cruzada) se conserva sin usar: la continuidad del libro va por u.

    first_after_seed se IGNORA: la foto de Bybit llega por el MISMO WS (su u es el
    ancla) y el primer delta encadena exacto (u == base+1). No hay abarque (Binance).
    """
    del first_after_seed
    u = _require_seq(delta.update_id, "update_id (u)", expected)
    if delta.is_snapshot or u == 1:
        return _Continuity.RESET, u
    if u <= last_seq:
        return _Continuity.DUPLICATE, last_seq
    if u == last_seq + 1:
        return _Continuity.APPLY, u
    return _Continuity.GAP, last_seq


_CLASSIFIERS: dict[str, _Classifier] = {
    "binance": _classify_binance,
    "okx": _classify_okx,
    "bybit": _classify_bybit,
}


class OrderbookBook:
    """El libro L2 con estado de UN flujo (exchange, market_type, symbol).

    Se construye vacio, se ARRANCA con una foto (seed) y avanza con deltas (apply). El
    cableado (bloqueado) tiene un libro de estos por stream suscrito; el motor en si no
    sabe de streams ni de red. Su identidad la ADOPTA de la primera foto: a partir de
    ahi, una foto o un delta de OTRO flujo se rechazan (anti-suplantacion), como en
    trades.
    """

    def __init__(self, *, identity: tuple[str, str, str] | None = None) -> None:
        self._bids: dict[Decimal, Decimal] = {}
        self._asks: dict[Decimal, Decimal] = {}
        # Identidad del flujo, adoptada de la primera foto. None mientras no hay foto.
        # OPCIONAL AL CONSTRUIR: un libro puede conocer su (exchange, market_type,
        # symbol) ANTES de sembrar -- el cableado sabe QUE stream es aunque aun no llego
        # la foto --. Sirve para EMITIR una frontera sin semilla (is_complete=False,
        # niveles vacios, opcion B): la incompletitud va en el canon, y para eso la foto
        # necesita a quien pertenece. NO altera el sembrado: seed() sobrescribe esta
        # identidad con la de la foto (y como _seeded sigue False, no la verifica).
        self._exchange: str | None = None
        self._market_type: str | None = None
        self._symbol: str | None = None
        if identity is not None:
            self._exchange, self._market_type, self._symbol = identity
        # La ultima secuencia aplicada, en el vocabulario del exchange (Binance u, OKX
        # seqId, Bybit u). El ancla contra la que encadena el proximo delta.
        self._last_seq: int = 0
        self._seeded = False
        self._complete = False
        # Antes de la primera foto el libro NO es de fiar: necesita un seed. La senal
        # arranca ENCENDIDA y la apaga la foto.
        self._resync_required = True
        # El PROXIMO delta es el PRIMERO tras una foto. Solo Binance lo mira: su primer
        # delta ABARCA la foto (U<=base+1<=u) en vez de encadenar exacto. Lo apaga el
        # primer delta aplicado; lo enciende cada seed().
        self._first_after_seed = False

    # -- Estado observable --------------------------------------------------

    @property
    def is_complete(self) -> bool:
        """El libro refleja el mercado sin agujeros conocidos. FAIL-SAFE: False ante la
        menor duda (sin foto todavia, o tras un hueco no resuelto).
        """
        return self._complete

    @property
    def resync_required(self) -> bool:
        """La SENAL: hace falta pedir una foto nueva. El motor NO la pide (eso es del
        cableado); solo la levanta. Se apaga con el proximo seed() (o un RESET de
        Bybit).
        """
        return self._resync_required

    @property
    def seeded(self) -> bool:
        """Si ya se arranco con al menos una foto."""
        return self._seeded

    @property
    def sequence(self) -> int:
        """La ultima secuencia aplicada, en el vocabulario del exchange (Binance u, OKX
        seqId, Bybit u). Es la secuencia AS-OF del estado actual del libro, que el motor
        de snapshot copia al payload. 0 antes de la primera foto.
        """
        return self._last_seq

    @property
    def exchange(self) -> str | None:
        """El exchange del flujo, adoptado de la primera foto (None si aun no hay)."""
        return self._exchange

    @property
    def market_type(self) -> str | None:
        """El tipo de mercado del flujo (None si aun no hay foto)."""
        return self._market_type

    @property
    def symbol(self) -> str | None:
        """El simbolo canonico del flujo (None si aun no hay foto)."""
        return self._symbol

    def stream_id(self) -> str | None:
        """La clave textual del flujo, o None si aun no se ha arrancado."""
        if self._exchange is None or self._market_type is None or self._symbol is None:
            return None
        return _stream_id(self._exchange, self._market_type, self._symbol)

    def bids(self) -> dict[Decimal, Decimal]:
        """Copia del lado comprador (precio -> tamano); el estado no se muta fuera."""
        return dict(self._bids)

    def asks(self) -> dict[Decimal, Decimal]:
        """Copia del lado vendedor (precio -> tamano)."""
        return dict(self._asks)

    def best_bid(self) -> tuple[Decimal, Decimal] | None:
        """El mejor bid (precio mas alto) y su tamano, o None si el lado esta vacio."""
        if not self._bids:
            return None
        price = max(self._bids)
        return price, self._bids[price]

    def best_ask(self) -> tuple[Decimal, Decimal] | None:
        """El mejor ask (precio mas bajo) y su tamano, o None si el lado esta vacio."""
        if not self._asks:
            return None
        price = min(self._asks)
        return price, self._asks[price]

    # -- Motor --------------------------------------------------------------

    def seed(self, raw: RawOrderbookSeed) -> None:
        """(Re)ARRANCA el libro desde una FOTO completa. Es como se resuelve un resync.

        ATOMICA: valida la foto ENTERA antes de tocar el estado. Una foto corrupta se
        rechaza y el libro se queda EXACTAMENTE como estaba (si habia uno bueno, sigue
        bueno); el motor NUNCA construye un libro invalido a medias. Al terminar, el
        libro esta COMPLETO y la senal de resync APAGADA: es un punto de partida limpio.
        """
        expected = self._expected_for(raw.exchange, raw.market_type, raw.symbol)
        if self._seeded:
            self._verificar_pertenencia(
                raw.exchange, raw.market_type, raw.symbol, expected
            )
        bids = _validated_levels(raw.bids, "bid", expected)
        asks = _validated_levels(raw.asks, "ask", expected)
        # Todo validado: recien ahora se compromete el estado (ni un raise por el
        # medio).
        self._exchange = raw.exchange
        self._market_type = raw.market_type
        self._symbol = raw.symbol
        self._bids = _book_from_levels(bids)
        self._asks = _book_from_levels(asks)
        self._last_seq = raw.base_sequence
        self._seeded = True
        self._complete = True
        self._resync_required = False
        # El proximo delta es el PRIMERO tras esta foto: en Binance ABARCA la foto
        # (U<=base+1<=u) en vez de encadenar exacto (regla oficial I-02).
        self._first_after_seed = True

    def apply(self, raw: RawOrderbookDelta) -> None:
        """Aplica un delta EN ORDEN, o senala un hueco. El corazon del motor.

        Sin foto previa no hay nada que actualizar: se levanta la senal de resync y se
        vuelve. Con foto, se comprueba la pertenencia y se clasifica el delta con la
        regla del exchange. Una FOTO reenviada (RESET de Bybit) reconstruye el libro y
        RECUPERA de un resync; el resto de mensajes, estando en resync, se ignoran hasta
        que llegue una foto (por aqui o por seed()): un libro roto no se recompone
        encadenando a ciegas. En operacion normal: APPLY encadena, DUPLICATE/NOOP se
        ignoran sin perder la completitud, y GAP marca incompleto y pide resync.
        """
        if not self._seeded:
            self._complete = False
            self._resync_required = True
            return

        expected = self._expected_for(raw.exchange, raw.market_type, raw.symbol)
        self._verificar_pertenencia(raw.exchange, raw.market_type, raw.symbol, expected)

        classify = self._classifier_for(raw.exchange)
        outcome, new_seq = classify(
            raw, self._last_seq, expected, self._first_after_seed
        )

        if outcome is _Continuity.RESET:
            self._reset_from(raw, new_seq, expected)
            self._first_after_seed = False
            return
        if self._resync_required:
            # Ya hay un hueco abierto: solo una foto (RESET arriba, o un seed())
            # recupera. Encadenar aqui podria "recuperar" por azar y dejar un agujero
            # DENTRO del libro publicado como completo. Se queda incompleto, esperando
            # la foto.
            return
        if outcome is _Continuity.APPLY:
            self._merge_delta(raw, new_seq, expected)
            # Ya enganchamos el primer delta (el abarque en Binance): a partir de aqui,
            # continuidad estricta. DUPLICATE/NOOP no lo apagan (se espera el puente).
            self._first_after_seed = False
        elif outcome is _Continuity.GAP:
            self._complete = False
            self._resync_required = True
        # DUPLICATE / NOOP: no se toca el libro y sigue completo.

    # -- Interno ------------------------------------------------------------

    def _expected_for(self, exchange: str, market_type: str, symbol: str) -> str:
        """La clave del flujo para el diagnostico: la del libro si ya existe, si no la
        del propio mensaje (aun no hay con quien contrastar).
        """
        actual = self.stream_id()
        return (
            actual if actual is not None else _stream_id(exchange, market_type, symbol)
        )

    def _verificar_pertenencia(
        self, exchange: str, market_type: str, symbol: str, expected: str
    ) -> None:
        """ANTI-SUPLANTACION: el mensaje debe pertenecer AL LIBRO que se esta
        manteniendo.

        Un delta de OTRO simbolo colado por este stream meteria los niveles de una
        moneda en el libro de OTRA, y una regla de orderflow leeria una profundidad que
        no es la suya. Se comprueba contra la identidad adoptada en la primera foto.
        """
        desajustes = [
            ("exchange", exchange, self._exchange),
            ("market_type", market_type, self._market_type),
            ("symbol", symbol, self._symbol),
        ]
        for campo, recibido, propio in desajustes:
            if recibido != propio:
                raise RawOrderbookRejected(
                    RawOrderbookRejectionReason.SYMBOL_MISMATCH,
                    f"el mensaje dice {campo}={recibido!r} pero este libro es "
                    f"{campo}={propio!r}: no pertenece a este flujo",
                    expected,
                )

    def _classifier_for(self, exchange: str) -> _Classifier:
        clasificador = _CLASSIFIERS.get(exchange)
        if clasificador is None:
            # Exchange sin regla de continuidad: no es un dato podrido, es un stream que
            # el motor no deberia haber recibido (no hay adaptador). FAIL-LOUD: que se
            # vea.
            msg = (
                f"exchange {exchange!r} sin regla de continuidad del libro: el motor "
                f"solo sabe encadenar {sorted(_CLASSIFIERS)}. Es un fallo de cableado."
            )
            raise ValueError(msg)
        return clasificador

    def _reset_from(self, raw: RawOrderbookDelta, new_seq: int, expected: str) -> None:
        """Reconstruye el libro desde una FOTO de Bybit. Atomica, como seed()."""
        bids = _validated_levels(raw.bids, "bid", expected)
        asks = _validated_levels(raw.asks, "ask", expected)
        self._bids = _book_from_levels(bids)
        self._asks = _book_from_levels(asks)
        self._last_seq = new_seq
        self._complete = True
        self._resync_required = False

    def _merge_delta(self, raw: RawOrderbookDelta, new_seq: int, expected: str) -> None:
        """Funde un delta en el libro: tamano 0 BORRA el nivel, cualquier otro lo fija.

        Valida AMBOS lados antes de mutar nada: un delta con un nivel podrido se rechaza
        entero y el libro no queda a medias. Como no se avanza la secuencia si el delta
        se rechaza, el proximo delta encadenara mal y el hueco saltara por si solo.
        """
        bids = _validated_levels(raw.bids, "bid", expected)
        asks = _validated_levels(raw.asks, "ask", expected)
        _aplicar_lado(self._bids, bids)
        _aplicar_lado(self._asks, asks)
        self._last_seq = new_seq


def _aplicar_lado(
    book: dict[Decimal, Decimal], levels: list[tuple[Decimal, Decimal]]
) -> None:
    """Funde niveles ya validados en un lado del libro. Tamano 0 = borrar el nivel."""
    for price, size in levels:
        if size == 0:
            book.pop(price, None)
        else:
            book[price] = size
