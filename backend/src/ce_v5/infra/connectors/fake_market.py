"""FakeMarketDataSource: simulador ADVERSARIAL de un exchange (ADR-006, ADR-014).

NO ES UN JUGUETE. Es lo que permite que el CI sea HERMETICO (cero red, cero
dependencia de un tercero que puede estar caido, cambiar su formato o banearnos la
IP) y, sobre todo, es lo que permite probar lo que un exchange REAL hace y que nadie
puede provocar a voluntad contra el de verdad: mandar una vela de otro simbolo,
mandar un NaN, desconectarse a mitad, o soltar una avalancha.

DETERMINISTA POR CONSTRUCCION: cero aleatoriedad, cero hilos, cero red, cero reloj.
El test escribe el GUION y el fake lo recita. Si un test fuese verde unas veces y
rojo otras, no probaria nada.

Cumple MarketDataSourcePort de forma ESTRUCTURAL (Protocol): este modulo no importa
platform, ni platform importa infra.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from collections.abc import Set as AbstractSet

from source.families.market import (
    Instrument,
    MarketStreamKey,
    RawCandle,
    Timeframe,
)


class FakeMarketDataSource:
    """Un exchange de mentira que hace todo lo que hace uno de verdad, incluido
    portarse mal.

    El GUION lo escribe el test con emit(); poll() lo entrega respetando el tope, sin
    perder nada entre llamadas. disconnect() simula la caida del feed.
    """

    def __init__(
        self,
        instruments: Sequence[Instrument] = (),
        timeframes: Iterable[Timeframe] = (),
        history: Sequence[RawCandle] = (),
    ) -> None:
        self._instruments = list(instruments)
        self._timeframes = frozenset(timeframes)
        self._history = list(history)
        self._active: set[str] = set()
        # El guion pendiente de entregar. Una deque porque poll() saca por la
        # izquierda y lo no entregado SIGUE AHI para el siguiente poll.
        self._pending: deque[RawCandle] = deque()
        # Observabilidad para los tests: que se abrio y que se cerro, en orden.
        self.opened: list[str] = []
        self.closed: list[str] = []

    # -- Guion del test -----------------------------------------------------

    def emit(self, *candles: RawCandle) -> None:
        """Encola velas para que las devuelva el proximo poll (o los siguientes)."""
        self._pending.extend(candles)

    def load_history(self, *candles: RawCandle) -> None:
        """Carga el historico que devolvera fetch_recent (bootstrap REST)."""
        self._history = list(candles)

    def disconnect(self) -> None:
        """Se cae el feed: todos los streams dejan de estar suscritos.

        El ingestor debe DARSE CUENTA (active() lo delata) y volver a abrir, con su
        bootstrap REST para rellenar el hueco. Un feed que se cae en silencio y nadie
        reabre es un stream zombi: vivo en el codigo, muerto en la realidad.
        """
        self._active.clear()

    def pending_count(self) -> int:
        """Cuantos mensajes quedan sin entregar (para comprobar que no se pierden)."""
        return len(self._pending)

    # -- MarketDataSourcePort -----------------------------------------------

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

    def poll(self, timeout_ms: int) -> Sequence[RawCandle]:
        """Entrega hasta max_batch mensajes. Lo que no cabe, ESPERA.

        Asi se simula la AVALANCHA: el exchange puede soltar mil velas de golpe, pero
        el ingestor solo se lleva las que puede digerir. Nada se pierde: el resto
        sigue en la cola para el siguiente poll (backpressure de B6).
        """
        lote: list[RawCandle] = []
        while self._pending and len(lote) < self.max_batch:
            lote.append(self._pending.popleft())
        return lote

    def fetch_recent(self, key: MarketStreamKey, limit: int) -> Sequence[RawCandle]:
        """El bootstrap REST tras una reconexion. Datos TAMPOCO validados: el REST de
        un exchange no es mas confiable que su WebSocket.
        """
        clave = key.as_stream_key()
        del clave  # el fake devuelve el historico cargado, sea cual sea la clave.
        return self._history[-limit:] if limit > 0 else []

    def list_instruments(self, market_type: str) -> Sequence[Instrument]:
        return [i for i in self._instruments if i.market_type == market_type]

    def supported_timeframes(self) -> frozenset[Timeframe]:
        return self._timeframes

    # Tope de mensajes por poll. Atributo publico para que el test provoque la
    # avalancha con un tope pequeno sin tocar nada mas.
    max_batch: int = 100
