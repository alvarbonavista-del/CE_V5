"""Connector PRIVADO por-usuario (BYOC), version FAKE (P07, ADR-010/012/014).

QUE ES: el CAMINO privado. Un flujo de datos que NO es publico (no se comparte
cross-tenant), pertenece a UN sujeto y exige conectarse a su cuenta del exchange. Por
eso su manifest declara la capacidad SENSIBLE connect_broker, una de las cinco de la
lista cerrada de P06 (D1), y por eso el gate de politica lo evalua ANTES de INITIALIZE:
un sujeto sin entitlement explicito (D6) queda DENEGADO fail-closed y la instancia va a
QUARANTINED sin que su initialize() llegue a ejecutarse.

QUE NO ES, Y NO POR CASUALIDAD:

- NO tiene credenciales. Ni las pide, ni las guarda, ni las acepta. Las credenciales
  BYOC reales son P10a: viven cifradas, con su propio rol de DB y su propio gate.

- NO ejecuta ordenes. Eso es P10b.

- NO EMITE NINGUN EVENTO DE DOMINIO. Y esto es lo importante: el hecho privado real
  (un fill, un cambio de balance) pertenece a la familia execution.*, que la define y
  la produce P10b. Fabricar aqui un execution.* seria INVENTAR UN CONTRATO AJENO y
  poner en el bus un hecho que nadie ha ocurrido: exactamente lo que CA-04 prohibe
  ("el operador puede denegar de mas; jamas fabricar hechos"). Un connector fake que
  emitiera fills falsos alimentaria reglas y, en M5, ordenes reales sobre datos que no
  existen. Asi que este componente demuestra que EL CAMINO esta gateado y aislado, y
  se calla.

NO IMPORTA platform NI infra (capas hermanas): declara aqui los puertos minimos que
consume y recibe su cerebro ya construido. Lo cablea el composition root.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class PrivateFeedPort(Protocol):
    """Lo unico que el connector necesita del feed privado FAKE.

    Es un doble: no hace IO, no habla con ningun broker y no tiene credenciales.
    """

    def connect(self) -> None:
        """'Conecta' con la cuenta del sujeto. En P07 no hace IO."""
        ...

    def disconnect(self) -> None:
        """Cierra la conexion."""
        ...

    def connected(self) -> bool:
        """Si el camino privado esta vivo."""
        ...


@dataclass(frozen=True, slots=True)
class ConnectorStatus:
    """Estado observable del camino privado. NO es un evento de dominio."""

    initialized: bool
    running: bool
    connected: bool


class PrivateMarketConnectorFake:
    """El camino privado BYOC, gateado por politica. FAKE: sin credenciales ni IO."""

    def __init__(self, feed: PrivateFeedPort) -> None:
        self._feed = feed
        self._initialized = False
        self._running = False

    # --- Los seis enganches del lifecycle (ADR-010) ---

    def initialize(self) -> None:
        """Prepara el camino privado y 'conecta' al feed FAKE.

        SOLO SE LLEGA AQUI SI EL GATE PERMITIO (ADR-012): el supervisor evalua
        connect_broker ANTES de invocar este enganche. Si el sujeto no tiene
        entitlement, este metodo NO se ejecuta nunca y la instancia va a QUARANTINED.
        Que la conexion viva aqui, y no en el constructor, es lo que hace que el gate
        pueda impedirla.
        """
        self._feed.connect()
        self._initialized = True

    def start(self) -> None:
        self._running = True

    def pause(self) -> None:
        """Deja de trabajar, pero NO desconecta (ADR-010: pause conserva el estado)."""
        self._running = False

    def resume(self) -> None:
        self._running = True

    def stop(self) -> None:
        """Apagado ordenado: se desconecta del feed privado."""
        self._running = False
        self._feed.disconnect()

    def unload(self) -> None:
        self._feed.disconnect()
        self._initialized = False
        self._running = False

    # --- Trabajo (no es del contrato de lifecycle) ---

    def status(self) -> ConnectorStatus:
        """Refleja que el camino esta vivo. NO produce eventos de dominio.

        El hecho privado real (fill, balance) es execution.*, familia de P10b.
        Inventarlo aqui seria fabricar un contrato ajeno.
        """
        return ConnectorStatus(
            initialized=self._initialized,
            running=self._running,
            connected=self._feed.connected(),
        )

    @property
    def running(self) -> bool:
        return self._running


def build(feed: PrivateFeedPort) -> PrivateMarketConnectorFake:
    """Factory declarada como entrypoint en el manifest (ADR-009).

    Exige su dependencia por parametro, y puede: el discovery de P04 resuelve el
    atributo (getattr) pero NO lo invoca; quien construye es el composition root.
    """
    return PrivateMarketConnectorFake(feed=feed)
