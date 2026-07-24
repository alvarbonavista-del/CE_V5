"""Puerto del feed publico del LIBRO L2 (orderbook) con estado (ADR-014, ADR-006).

Hermano de TradeDataSourcePort para la clase de dato `orderbook`. El PUERTO pertenece a
quien lo CONSUME (patron hexagonal), y quien consume un feed es la plataforma: por eso
se declara aqui. Los DTO neutrales que viajan por el (RawOrderbookSeed,
RawOrderbookDelta) viven en CONTRACTS (source.families.market), FUERA del contrato de
capas: los produce infra (los adaptadores de exchange) y los consume platform (el motor
del libro), y esas dos capas NO PUEDEN VERSE entre si (hermanos independientes).

ES UN PUERTO APARTE, NO UNA AMPLIACION DEL DE VELAS NI DEL DE TRADES, y eso es
deliberado: quien sirve el libro no tiene por que servir velas ni trades, y meter
poll_deltas en otro puerto obligaria a TODO adaptador a implementar el libro (o a fingir
que lo hace devolviendo vacio, que es peor: un stream mudo que parece sano).

LA DIFERENCIA DE FONDO CON TRADES: el libro es CON ESTADO y ORDER-DEPENDIENTE. Un trade
es un hecho conmutativo que se deduplica por su id; el libro se RECONSTRUYE desde una
FOTO (seed) y avanza aplicando deltas EN ORDEN. Por eso este puerto tiene seed(), que el
de trades no necesita: sin la foto de partida un delta no significa nada. Y por eso no
hay backfill_after_reconnect con "cobertura": un hueco en el libro no se rellena pieza a
pieza, se resuelve pidiendo una FOTO NUEVA (resync). Quien decide que hace falta un
resync es el motor (al detectar el hueco); este puerto solo lo sirve cuando se le pide
otra seed().

DELIBERADAMENTE MAS ESTRECHO que el de velas: aqui NO hay list_instruments ni
supported_timeframes. El catalogo de pares es UNO SOLO por exchange y ya lo sirve el
puerto de velas; duplicarlo aqui permitiria que un dia los catalogos discrepasen. Y no
hay timeframes porque el libro NO se bucketea por intervalo (MarketStreamKey lo PROHIBE
para data_kind=orderbook): su granularidad es depth/channel, no un timeframe.
"""

from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from typing import Protocol

from source.families.market import (
    MarketStreamKey,
    RawOrderbookDelta,
    RawOrderbookSeed,
)

# RawOrderbookSeed y RawOrderbookDelta se DECLARAN en contracts y se reexportan aqui,
# igual que RawTrade en el puerto de trades y por el mismo motivo: viajan por este
# puerto, pero los CONSTRUYE infra (los adaptadores de exchange) y los CONSUME platform,
# y esas dos capas no pueden verse. Quien consume el puerto los importa de aqui; quien
# los produce, de contracts.
__all__ = [
    "OrderbookDataSourcePort",
    "RawOrderbookDelta",
    "RawOrderbookSeed",
]


class OrderbookDataSourcePort(Protocol):
    """Contrato de un feed publico del libro L2 con estado (ADR-014).

    Incluye el control de stream (open/close/active) porque abrir un stream ES
    suscribirse en el exchange. Cambiar de exchange, o anadir un segundo, es escribir un
    adaptador nuevo, no tocar el motor del libro (la prueba de fuego de CE-14).
    """

    def seed(self, key: MarketStreamKey) -> RawOrderbookSeed:
        """Pide la FOTO COMPLETA del libro de ese flujo: el punto de partida del estado.

        Es lo que NO existe en trades: sin la foto, un delta incremental no significa
        nada (¿cambio respecto a que?). El conector la obtiene como su exchange mande
        (snapshot REST o el primer mensaje del socket) y la devuelve con su SECUENCIA
        BASE, que es el ancla contra la que el motor encadena los deltas. Se vuelve a
        llamar cuando el motor senala un RESYNC: un hueco en el libro no se parchea, se
        resuelve con una foto nueva.

        Devuelve datos NO validados: el snapshot de un exchange no es mas confiable que
        su socket. Cruza la MISMA frontera de confianza (el motor del libro).
        """
        ...

    def open(self, key: MarketStreamKey) -> None:
        """Se suscribe al flujo incremental del libro de ese stream en el exchange."""
        ...

    def close(self, key: MarketStreamKey) -> None:
        """Cancela la suscripcion a ese flujo."""
        ...

    def active(self) -> AbstractSet[str]:
        """Las claves REALMENTE suscritas ahora mismo."""
        ...

    def poll_deltas(self, timeout_ms: int) -> Sequence[RawOrderbookDelta]:
        """Recoge los deltas que hayan llegado. PULL, no push, y CON TOPE.

        Quien manda es el MOTOR, no el exchange: un libro liquido publica un torrente de
        actualizaciones por segundo, asi que un push sin control convierte cualquier
        pico de volatilidad en una cola infinita en memoria (backpressure, I-02). Con
        pull, el motor pide lo que puede digerir y el resto ESPERA en el feed.

        EL ORDEN DE ENTREGA IMPORTA, al reves que en trades: el motor aplica los deltas
        EN SECUENCIA y detecta el hueco por la continuidad. Un feed que reordene lo que
        entrega fabrica huecos falsos; el conector debe entregarlos en el orden en que
        el exchange los emitio.
        """
        ...

    def drain_reconnected(self) -> AbstractSet[str]:
        """Devuelve (y limpia) las claves canonicas de stream que RECONECTARON desde la
        ultima llamada. El motor las usa para forzar un RESYNC tras una reconexion:
        un socket que se cayo y volvio se perdio deltas, asi que el estado del libro
        ya no es de fiar y hay que pedir una FOTO nueva (seed). Vacio en operacion
        normal.
        """
        ...
