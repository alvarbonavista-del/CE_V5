"""SubscriptionManager (ADR-014): de la demanda agregada a los streams abiertos.

Aqui se cierra el circulo de la pieza: los intereses de los sujetos ya estan
agregados por la ventanilla ({clave: cuantos}), y este objeto los convierte en
streams REALMENTE abiertos y cerrados contra el exchange.

EL REF-COUNT NO ES FUENTE DE VERDAD. Es estado operativo RECONSTRUIBLE: la fuente de
verdad son los SubscriptionIntent persistidos. Por eso este objeto nace SIN memoria y
en cada reconcile() vuelve a leer la demanda y a reconciliar el mundo real contra
ella. Un reinicio no pierde ni duplica streams porque no hay nada que "recordar",
solo algo que "reconstruir": exactamente donde v4 se habria roto.

NO importa infra ni components: solo el Clock del nucleo y los contratos.
"""

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from typing import Protocol

from ce_v5.core.clock import Clock
from source.families.market import MarketStreamKey


class PublicDemandPort(Protocol):
    """La demanda agregada de flujos publicos (CA-P07-D)."""

    def snapshot(self) -> Mapping[str, int]:
        """{market_stream_key: cuantos intereses vivos lo piden}.

        Es lo UNICO que el worker sabe de la demanda: CUANTOS, jamas QUIENES.
        """
        ...


class StreamControllerPort(Protocol):
    """El mundo real: las conexiones al exchange."""

    def open(self, key: MarketStreamKey) -> None:
        """Abre el stream de ese flujo."""
        ...

    def close(self, key: MarketStreamKey) -> None:
        """Cierra el stream de ese flujo."""
        ...

    def active(self) -> AbstractSet[str]:
        """Las claves REALMENTE abiertas ahora mismo."""
        ...


@dataclass(frozen=True, slots=True)
class HysteresisConfig:
    """Anti-flapping (ADR-014).

    ASIMETRICA A PROPOSITO:
    - ENCENDER es INMEDIATO: si alguien pide datos, los datos no esperan. Un
      retardo al abrir seria latencia pura para el usuario.
    - APAGAR lleva RETARDO (off_delay_ms): un stream que se cierra y se reabre
      cinco veces en diez segundos castiga al exchange (rate limits, baneos de
      IP), pierde datos en cada hueco y obliga a un bootstrap REST cada vez. Mas
      barato mantenerlo abierto unos segundos de mas que abrirlo de nuevo.
    """

    off_delay_ms: int = 30_000


# Singleton de modulo: es inmutable (frozen), asi que compartirlo es seguro y ademas
# deja el valor por defecto VISIBLE en un solo sitio.
DEFAULT_HYSTERESIS = HysteresisConfig()


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """Lo que hizo un ciclo. OBSERVABLE: sin esto, un stream zombi es invisible."""

    opened: tuple[str, ...] = ()
    closed: tuple[str, ...] = ()
    pending_close: tuple[str, ...] = ()
    ref_counts: Mapping[str, int] = field(default_factory=dict)
    invalid: tuple[str, ...] = ()


class SubscriptionManager:
    """Union de intereses -> ref-counts -> streams abiertos (ADR-014).

    EL REF-COUNT NO ES FUENTE DE VERDAD: es estado operativo RECONSTRUIBLE. Este
    objeto nace SIN memoria; en cada reconcile() lee la demanda persistida y
    reconcilia el mundo real contra ella. Por eso un reinicio no pierde ni duplica
    streams: no hay nada que "recordar", solo algo que "reconstruir". Ahi es
    exactamente donde v4 se habria roto.
    """

    def __init__(
        self,
        demand: PublicDemandPort,
        controller: StreamControllerPort,
        clock: Clock,
        hysteresis: HysteresisConfig = DEFAULT_HYSTERESIS,
    ) -> None:
        self._demand = demand
        self._controller = controller
        self._clock = clock
        self._hysteresis = hysteresis
        # Lo UNICO que se recuerda entre ciclos: desde cuando un stream esta sin
        # demanda. No es el ref-count (ese se relee siempre); es el cronometro del
        # anti-flapping, y si se perdiera en un reinicio lo peor que pasaria es que
        # el retardo de apagado empezase de cero. Nada se corrompe.
        self._closing_since: dict[str, int] = {}
        # Ref-count del ULTIMO ciclo, solo para observabilidad (state()).
        self._ref_counts: dict[str, int] = {}

    def reconcile(self) -> ReconcileResult:
        """Un ciclo: lee la demanda y reconcilia el mundo real contra ella."""
        now = self._clock.now_ms()

        deseado: dict[str, int] = {}
        invalidas: list[str] = []
        for clave, cuantos in self._demand.snapshot().items():
            if cuantos < 1:
                continue
            try:
                # FAULT ISOLATION POR STREAM: una clave corrupta NO puede dejar sin
                # datos a los otros 200 streams. Se registra y se salta; el ciclo
                # sigue.
                MarketStreamKey.parse(clave)
            except ValueError:
                invalidas.append(clave)
                continue
            deseado[clave] = cuantos

        abierto = set(self._controller.active())

        # ENCENDER es INMEDIATO: si alguien pide datos, los datos no esperan.
        abiertas: list[str] = []
        for clave in sorted(deseado):
            # Vuelve la demanda: se CANCELA cualquier cierre pendiente. Esto es el
            # anti-flapping: el stream que iba a apagarse simplemente no se apaga.
            self._closing_since.pop(clave, None)
            if clave not in abierto:
                self._controller.open(MarketStreamKey.parse(clave))
                abiertas.append(clave)

        # APAGAR lleva RETARDO. Lo que ya no se desea NO se cierra al instante: se
        # marca, y solo se cierra si sigue sin demanda cuando vence el plazo.
        cerradas: list[str] = []
        pendientes: list[str] = []
        for clave in sorted(abierto - set(deseado)):
            marcado = self._closing_since.setdefault(clave, now)
            if now - marcado < self._hysteresis.off_delay_ms:
                pendientes.append(clave)
                continue
            try:
                # Misma fault isolation al cerrar: si el controlador reportase como
                # activa una clave corrupta, no puede tumbar el ciclo de los demas.
                key = MarketStreamKey.parse(clave)
            except ValueError:
                invalidas.append(clave)
                self._closing_since.pop(clave, None)
                continue
            self._controller.close(key)
            self._closing_since.pop(clave, None)
            cerradas.append(clave)

        self._ref_counts = deseado
        return ReconcileResult(
            opened=tuple(abiertas),
            closed=tuple(cerradas),
            pending_close=tuple(pendientes),
            ref_counts=dict(deseado),
            invalid=tuple(invalidas),
        )

    def state(self) -> Mapping[str, int]:
        """Ref-count vigente del ultimo ciclo (solo lectura, observabilidad)."""
        return dict(self._ref_counts)
