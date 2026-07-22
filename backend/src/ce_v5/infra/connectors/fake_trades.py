"""FakeTradeSource: simulador ADVERSARIAL de un feed de trades (ADR-006, ADR-014).

Gemelo de FakeMarketDataSource para la clase de dato `trades`, y por el mismo motivo:
es lo que permite que el CI sea HERMETICO (cero red, cero dependencia de un tercero que
puede estar caido, cambiar su formato o banearnos la IP) y, sobre todo, es lo que
permite probar lo que un exchange REAL hace y que nadie puede provocar a voluntad
contra el de verdad: mandar un trade de otro simbolo, mandar un qty a cero, un lado que
no existe, desconectarse a mitad, reenviar trades ya vistos, o soltar una avalancha.

DETERMINISTA POR CONSTRUCCION: cero aleatoriedad, cero hilos, cero red, cero reloj. El
test escribe el GUION y el fake lo recita. Si un test fuese verde unas veces y rojo
otras, no probaria nada.

Cumple TradeDataSourcePort de forma ESTRUCTURAL (Protocol): este modulo no importa
platform, ni platform importa infra.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from collections.abc import Set as AbstractSet

from source.families.market import MarketStreamKey, RawTrade


class FakeTradeSource:
    """Un feed de trades de mentira que hace todo lo que hace uno de verdad, incluido
    portarse mal.

    El GUION lo escribe el test con emit(); poll_trades() lo entrega respetando el tope,
    sin perder nada entre llamadas. disconnect() simula la caida del feed.
    """

    def __init__(self, history: Sequence[RawTrade] = ()) -> None:
        self._history = list(history)
        self._active: set[str] = set()
        # El guion pendiente de entregar. Una deque porque poll_trades() saca por la
        # izquierda y lo no entregado SIGUE AHI para el siguiente poll.
        self._pending: deque[RawTrade] = deque()
        # Claves que "reconectaron" segun el guion del test (simulate_reconnect). El
        # motor las recoge en drain_reconnected y dispara su bootstrap REST.
        self._reconnected: set[str] = set()
        # Observabilidad para los tests: que se abrio y que se cerro, en orden.
        self.opened: list[str] = []
        self.closed: list[str] = []

    # -- Guion del test -----------------------------------------------------

    def emit(self, *trades: RawTrade) -> None:
        """Encola trades para que los devuelva el proximo poll (o los siguientes)."""
        self._pending.extend(trades)

    def load_history(self, *trades: RawTrade) -> None:
        """Carga el historico que devolvera fetch_recent_trades (bootstrap REST)."""
        self._history = list(trades)

    def disconnect(self) -> None:
        """Se cae el feed: todos los streams dejan de estar suscritos.

        El motor debe DARSE CUENTA (active() lo delata) y volver a abrir, con su
        bootstrap REST para rellenar el hueco. Un feed que se cae en silencio y nadie
        reabre es un stream zombi: vivo en el codigo, muerto en la realidad.
        """
        self._active.clear()

    def simulate_reconnect(self, keys: Sequence[str]) -> None:
        """Guion del test: mete esas claves canonicas en el set de reconectados, como si
        el socket se hubiera caido y vuelto para esos streams. El proximo
        drain_reconnected las entrega y el MOTOR dispara su bootstrap REST.
        """
        self._reconnected.update(keys)

    def pending_count(self) -> int:
        """Cuantos mensajes quedan sin entregar (para comprobar que no se pierden)."""
        return len(self._pending)

    # -- TradeDataSourcePort ------------------------------------------------

    def open(self, key: MarketStreamKey) -> None:
        clave = key.as_stream_key()
        self.opened.append(clave)
        self._active.add(clave)

    def close(self, key: MarketStreamKey) -> None:
        clave = key.as_stream_key()
        self.closed.append(clave)
        self._active.discard(clave)

    def active(self) -> AbstractSet[str]:
        return set(self._active)

    def poll_trades(self, timeout_ms: int) -> Sequence[RawTrade]:
        """Entrega hasta max_batch mensajes. Lo que no cabe, ESPERA.

        Asi se simula la AVALANCHA: el exchange puede soltar diez mil trades de golpe en
        un pico de volatilidad, pero el motor solo se lleva los que puede digerir. Nada
        se pierde: el resto sigue en la cola para el siguiente poll (backpressure).
        """
        lote: list[RawTrade] = []
        while self._pending and len(lote) < self.max_batch:
            lote.append(self._pending.popleft())
        return lote

    def fetch_recent_trades(
        self, key: MarketStreamKey, limit: int
    ) -> Sequence[RawTrade]:
        """El bootstrap REST tras una reconexion. Datos TAMPOCO validados: el REST de un
        exchange no es mas confiable que su WebSocket.
        """
        clave = key.as_stream_key()
        del clave  # el fake devuelve el historico cargado, sea cual sea la clave.
        return self._history[-limit:] if limit > 0 else []

    def drain_reconnected(self) -> AbstractSet[str]:
        """Devuelve y limpia las claves que reconectaron segun el guion del test."""
        copia = set(self._reconnected)
        self._reconnected.clear()
        return copia

    # Tope de mensajes por poll. Atributo publico para que el test provoque la avalancha
    # con un tope pequeno sin tocar nada mas.
    max_batch: int = 100
