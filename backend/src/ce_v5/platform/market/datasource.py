"""Puerto de un feed publico de exchange (ADR-014, ADR-006).

El PUERTO pertenece a quien lo CONSUME (patron hexagonal), y quien consume un feed es
la plataforma: por eso se declara aqui. Los DTO neutrales que viajan por el puerto
(RawCandle, Instrument) viven en CONTRACTS (source.families.market), que esta FUERA
del contrato de capas y por tanto lo pueden importar todos: los produce infra (los
adaptadores de exchange) y los consume platform (la normalizacion), y esas dos capas
NO PUEDEN VERSE entre si (hermanos independientes).

Nada de esto es especifico de ningun exchange: el adaptador de cada uno traduce SU
formato a esta forma comun, y nada mas. Cambiar de exchange, o anadir un segundo, es
escribir un adaptador nuevo, no tocar el ingestor (T-03: la prueba de fuego de CE-14).
"""

from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from typing import Protocol

from source.families.market import (
    Instrument,
    MarketStreamKey,
    RawCandle,
    Timeframe,
)

__all__ = ["Instrument", "MarketDataSourcePort", "RawCandle"]


class MarketDataSourcePort(Protocol):
    """Contrato de un feed publico de exchange (ADR-014).

    Incluye StreamControllerPort (open/close/active) porque abrir un stream ES
    suscribirse en el exchange. Lo separa del ingestor: cambiar de exchange, o
    anadir un segundo, es un adaptador nuevo (T-03: la prueba de fuego de CE-14).
    """

    def open(self, key: MarketStreamKey) -> None:
        """Se suscribe a ese flujo en el exchange."""
        ...

    def close(self, key: MarketStreamKey) -> None:
        """Cancela la suscripcion a ese flujo."""
        ...

    def active(self) -> AbstractSet[str]:
        """Las claves REALMENTE suscritas ahora mismo."""
        ...

    def poll(self, timeout_ms: int) -> Sequence[RawCandle]:
        """Recoge lo que haya llegado. PULL, no push, y CON TOPE.

        Quien manda es el INGESTOR, no el exchange. Un push sin control convierte
        una avalancha del exchange en una cola infinita en memoria y tumba el
        proceso; con pull, el ingestor pide lo que puede digerir (ver el
        backpressure de B6).
        """
        ...

    def fetch_recent(self, key: MarketStreamKey, limit: int) -> Sequence[RawCandle]:
        """BOOTSTRAP REST tras una reconexion (ADR-014): rellena el hueco.

        Tambien devuelve datos NO validados: el REST del exchange no es mas
        confiable que su WebSocket.
        """
        ...

    def list_instruments(self, market_type: str) -> Sequence[Instrument]:
        """Catalogo: que pares existen.

        Es un CONTROL DE SEGURIDAD: sin el, se podrian fabricar MarketStreamKeys
        arbitrarios y abrir streams infinitos (DoS por cardinalidad).
        """
        ...

    def drain_reconnected(self) -> AbstractSet[str]:
        """Devuelve (y limpia) las claves canonicas de stream que RECONECTARON desde la
        ultima llamada. El motor las usa para disparar el bootstrap REST tras una
        reconexion (ADR-014): rellenar el hueco de datos que hubo mientras el socket
        estuvo caido. Vacio en operacion normal.
        """
        ...

    def supported_timeframes(self) -> frozenset[Timeframe]:
        """Los intervalos que ESTE exchange sirve de verdad.

        Cada exchange soporta intervalos distintos. Suponerlos iguales es el error
        que Central advirtio al prohibir copiar el barrido de un exchange a otro.
        """
        ...
