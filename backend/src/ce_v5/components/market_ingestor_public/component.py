"""Ingestor de market data PUBLICO como Componente (ADR-001/008/009/010, CE-14).

Es el PRIMER componente real sobre el sustrato de P04: hasta hoy solo existia el
demostrador 'sample'. Se descubre por carpeta, su manifest se valida ANTES de cargar
su codigo, y el supervisor lo lleva por el lifecycle emitiendo component.* observables.

CUMPLE ComponentLifecycle POR COMPOSICION (ADR-001): no hereda ninguna clase base.
Satisface el contrato por FORMA, con los seis enganches.

NO IMPORTA platform NI infra. Son capas HERMANAS e independientes en el contrato de
capas, y components es una de ellas: no puede ver a las otras dos. Por eso el
componente declara AQUI los puertos MINIMOS que consume (Protocol estructural) y
recibe su cerebro YA CONSTRUIDO por el constructor. Quien lo cablea es el composition
root (entrypoints), que es la unica capa autorizada a ver a todas.

Esto no es una contorsion para contentar a un linter: es lo que hace que el
componente sea SUSTITUIBLE. El dia que haya un segundo exchange, o un ingestor
privado BYOC, no hay que tocar este fichero.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Protocol

from source.families.market import MarketStreamKey


class SubscriptionReconcilerPort(Protocol):
    """Lo unico que el componente necesita del SubscriptionManager (B4)."""

    def reconcile(self) -> object:
        """Ajusta los streams abiertos a la demanda persistida."""
        ...


class CandleIngestorPort(Protocol):
    """Lo unico que el componente necesita del IngestionEngine (B6a)."""

    def drain_once(self) -> object:
        """Procesa un lote de lo que haya llegado del feed."""
        ...


class StreamSourcePort(Protocol):
    """Lo unico que el componente necesita del feed: apagarlo con orden."""

    def active(self) -> AbstractSet[str]:
        """Las claves realmente suscritas ahora mismo."""
        ...

    def close(self, key: MarketStreamKey) -> None:
        """Cancela la suscripcion a ese flujo."""
        ...


@dataclass(frozen=True, slots=True)
class TickReport:
    """Lo que hizo un ciclo. OBSERVABLE: sin esto, un worker que no ingiere nada
    parece sano porque no falla.
    """

    processed: bool  # False si el componente estaba en pausa o parado.
    reconcile: object | None = None
    ingestion: object | None = None
    degraded: bool = False
    error: str | None = None


class PublicMarketIngestorComponent:
    """El ingestor publico, envuelto como Componente.

    El cerebro (SubscriptionManager + IngestionEngine + feed) se INYECTA: este objeto
    no construye nada, solo orquesta el lifecycle y el ciclo de trabajo.
    """

    def __init__(
        self,
        subscription_manager: SubscriptionReconcilerPort,
        engine: CandleIngestorPort,
        source: StreamSourcePort,
    ) -> None:
        self._subscriptions = subscription_manager
        self._engine = engine
        self._source = source
        self._initialized = False
        self._running = False

    # --- Los seis enganches del lifecycle (ADR-010) ---

    def initialize(self) -> None:
        """Idempotente. Deja el componente listo, pero NO abre streams todavia.

        Abrir aqui seria conectarse al exchange antes de que nadie haya dicho que el
        componente debe trabajar. Los streams los abre el primer tick(), y solo segun
        la DEMANDA REAL (ADR-014): nunca "por si acaso".
        """
        self._initialized = True

    def start(self) -> None:
        self._running = True

    def pause(self) -> None:
        """Deja de consumir, pero NO cierra streams ni pierde los watermarks.

        ADR-010: PAUSE conserva el offset. Cerrar los streams al pausar obligaria a un
        bootstrap REST completo al reanudar, castigaria al exchange con una reconexion
        y dejaria un hueco de datos. Pausar es dejar de trabajar, no desconectarse.
        """
        self._running = False

    def resume(self) -> None:
        self._running = True

    def stop(self) -> None:
        """Apagado ORDENADO: cierra todos los streams abiertos.

        Aqui SI se cierran, porque el componente deja de existir para el sistema. Un
        stream que sobrevive a su componente es una conexion zombi contra el exchange.
        """
        self._running = False
        for clave in sorted(self._source.active()):
            self._source.close(MarketStreamKey.parse(clave))

    def unload(self) -> None:
        self._running = False
        self._initialized = False

    # --- El trabajo (no es del contrato de lifecycle) ---

    def tick(self) -> TickReport:
        """Un ciclo de trabajo, invocado por el worker en su bucle.

        FAULT ISOLATION: una excepcion al reconciliar o al drenar NO puede matar el
        componente. Un exchange que falla un poll, o una base que parpadea, degradan
        ESTE ciclo; el siguiente lo reintenta. Un worker que muere por un fallo
        transitorio deja de ingerir para TODOS los usuarios, que es infinitamente peor
        que perder un ciclo.
        """
        if not (self._initialized and self._running):
            return TickReport(processed=False)

        try:
            reconcile = self._subscriptions.reconcile()
            ingestion = self._engine.drain_once()
        except Exception as exc:  # noqa: BLE001 - la aislacion es el objetivo.
            return TickReport(
                processed=False,
                degraded=True,
                error=f"{type(exc).__name__}: {exc}",
            )
        return TickReport(processed=True, reconcile=reconcile, ingestion=ingestion)

    @property
    def running(self) -> bool:
        """Si el componente esta trabajando (observable)."""
        return self._running


def build(
    subscription_manager: SubscriptionReconcilerPort,
    engine: CandleIngestorPort,
    source: StreamSourcePort,
) -> PublicMarketIngestorComponent:
    """Factory declarada como entrypoint en el manifest (ADR-009).

    EXIGE sus dependencias por parametro, y puede hacerlo: el discovery de P04 NO
    LLAMA al entrypoint. import_entrypoint() solo importa el modulo y resuelve el
    atributo (getattr); quien decide construir el componente, y con que, es el
    composition root. Por eso no hace falta ningun mecanismo de "wire()" posterior:
    seria un objeto a medio construir circulando por el sistema, y un componente sin
    cerebro es justo el tipo de estado invalido que un constructor bien hecho hace
    IMPOSIBLE.
    """
    return PublicMarketIngestorComponent(
        subscription_manager=subscription_manager,
        engine=engine,
        source=source,
    )
