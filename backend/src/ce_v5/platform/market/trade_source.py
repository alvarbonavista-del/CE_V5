"""Puerto del feed publico de TRADES individuales (ADR-014, ADR-006).

Gemelo de MarketDataSourcePort para la clase de dato `trades`. El PUERTO pertenece a
quien lo CONSUME (patron hexagonal), y quien consume un feed es la plataforma: por eso
se declara aqui. El DTO neutral que viaja por el (RawTrade) vive en CONTRACTS
(source.families.market), que esta FUERA del contrato de capas: lo produce infra (los
adaptadores de exchange) y lo consume platform (la normalizacion), y esas dos capas NO
PUEDEN VERSE entre si (hermanos independientes).

ES UN PUERTO APARTE, NO UNA AMPLIACION DEL DE VELAS, y eso es deliberado: quien sirve
trades no tiene por que servir velas, y meter poll_trades en MarketDataSourcePort
obligaria a TODO adaptador de velas a implementar trades (o a fingir que lo hace
devolviendo vacio, que es peor: un stream mudo que parece sano).

DELIBERADAMENTE MAS ESTRECHO que el de velas: aqui NO hay list_instruments ni
supported_timeframes. El catalogo de pares es UNO SOLO por exchange y ya lo sirve el
puerto de velas; duplicarlo aqui permitiria que un dia los dos catalogos discrepasen y
que un par existiera para velas y no para trades. Y no hay timeframes porque el flujo de
trades NO se bucketea a nivel de stream (MarketStreamKey lo PROHIBE para data_kind
=trades): el bucketeo por barra es del footprint, que es dato DERIVADO.
"""

from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from typing import Protocol

from source.families.market import MarketStreamKey, RawTrade

__all__ = ["RawTrade", "TradeDataSourcePort"]


class TradeDataSourcePort(Protocol):
    """Contrato de un feed publico de trades individuales (ADR-014).

    Incluye el control de stream (open/close/active) porque abrir un stream ES
    suscribirse en el exchange. Cambiar de exchange, o anadir un segundo, es escribir
    un adaptador nuevo, no tocar el motor de trades (T-03: la prueba de fuego de CE-14).
    """

    def open(self, key: MarketStreamKey) -> None:
        """Se suscribe a ese flujo de trades en el exchange."""
        ...

    def close(self, key: MarketStreamKey) -> None:
        """Cancela la suscripcion a ese flujo."""
        ...

    def active(self) -> AbstractSet[str]:
        """Las claves REALMENTE suscritas ahora mismo."""
        ...

    def poll_trades(self, timeout_ms: int) -> Sequence[RawTrade]:
        """Recoge los trades que hayan llegado. PULL, no push, y CON TOPE.

        Quien manda es el MOTOR, no el exchange. Y en trades importa aun mas que en
        velas: un par liquido publica miles de trades por minuto, asi que un push sin
        control convierte cualquier pico de volatilidad en una cola infinita en memoria.
        Con pull, el motor pide lo que puede digerir y el resto ESPERA en el feed.
        """
        ...

    def fetch_recent_trades(
        self, key: MarketStreamKey, limit: int
    ) -> Sequence[RawTrade]:
        """BOOTSTRAP REST tras una reconexion (ADR-014): rellena el hueco.

        Devuelve datos NO validados, igual que el WebSocket: el REST de un exchange no
        es mas confiable que su socket. Pasan por la MISMA frontera de confianza.
        """
        ...

    def drain_reconnected(self) -> AbstractSet[str]:
        """Devuelve (y limpia) las claves canonicas de stream que RECONECTARON desde la
        ultima llamada. El motor las usa para disparar el bootstrap REST tras una
        reconexion (ADR-014): rellenar el hueco de trades que hubo mientras el socket
        estuvo caido. Vacio en operacion normal.
        """
        ...
