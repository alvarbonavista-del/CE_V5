"""Maquina de estados y contrato de enganches del lifecycle (ADR-001, ADR-010).

El VOCABULARIO del lifecycle (LifecycleState, HealthStatus, ReadinessStatus,
LifecycleScope) es CONTRATO y vive en contracts/source
(source.families.component, ADR-006); aqui NO se define, se importa. El
nucleo aporta lo suyo: la maquina de estados como dato (transiciones
legales) y el contrato estructural de enganches que implementa un
Componente (composicion sobre herencia, ADR-001). Solo stdlib mas
contratos; sin logica de dominio ni de supervision: el supervisor de P04
(Bloque 5) consume esta tabla para validar cada transicion.

P04 modelo la maquina base de ADR-010. P06 anade las ARISTAS DE POLITICA (D9)
como cambio explicito, ahora que existe quien decide poner en cuarentena o
liberar (la politica): denegacion del gate antes de INITIALIZE, fallo de
INITIALIZE no critico con backoff, kill switch sobre una instancia viva, y los
reintentos/liberaciones observables. Todas convergen en QUARANTINED (PROHIBIDO,
no roto) o FAILED (roto/critico) y salen por transiciones explicitas; ningun
bucle oculto.
"""

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from source.families.component import LifecycleState

# UNLOADED es el unico estado terminal: una instancia descargada no revive;
# se descubre y registra una nueva (ADR-010). Las aristas hacia QUARANTINED y
# los reintornos a INITIALIZING son ARISTAS DE POLITICA (D9, P06); van marcadas.
LEGAL_TRANSITIONS: Mapping[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.REGISTERED: frozenset(
        {
            LifecycleState.INITIALIZING,
            # Politica: el gate deniega antes del primer INITIALIZE (P06).
            LifecycleState.QUARANTINED,
        }
    ),
    LifecycleState.INITIALIZING: frozenset(
        {
            LifecycleState.INITIALIZED,
            LifecycleState.FAILED,
            # Politica: fallo de INITIALIZE no critico -> cuarentena con backoff.
            LifecycleState.QUARANTINED,
        }
    ),
    LifecycleState.INITIALIZED: frozenset(
        {
            LifecycleState.STARTING,
            LifecycleState.FAILED,
            # Politica: kill switch sobre la instancia ya inicializada (P06).
            LifecycleState.QUARANTINED,
        }
    ),
    LifecycleState.STARTING: frozenset(
        {
            LifecycleState.RUNNING,
            LifecycleState.FAILED,
            LifecycleState.QUARANTINED,  # Politica: kill switch (P06).
        }
    ),
    LifecycleState.RUNNING: frozenset(
        {
            LifecycleState.PAUSED,
            LifecycleState.STOPPING,
            LifecycleState.FAILED,
            LifecycleState.QUARANTINED,  # Politica: kill switch (P06).
        }
    ),
    LifecycleState.PAUSED: frozenset(
        {
            LifecycleState.RUNNING,
            LifecycleState.STOPPING,
            LifecycleState.FAILED,
            LifecycleState.QUARANTINED,  # Politica: kill switch (P06).
        }
    ),
    LifecycleState.STOPPING: frozenset({LifecycleState.STOPPED, LifecycleState.FAILED}),
    # STOPPED -> FAILED cubre el fallo del teardown (unload) (ADR-010).
    LifecycleState.STOPPED: frozenset({LifecycleState.UNLOADED, LifecycleState.FAILED}),
    LifecycleState.UNLOADED: frozenset(),
    LifecycleState.FAILED: frozenset(
        {
            LifecycleState.QUARANTINED,
            LifecycleState.UNLOADED,
            # Politica: reintento explicito de operador desde FAILED (P06).
            LifecycleState.INITIALIZING,
        }
    ),
    LifecycleState.QUARANTINED: frozenset(
        {
            LifecycleState.UNLOADED,
            # Politica: liberacion/reintento; re-consulta el gate e INITIALIZE.
            LifecycleState.INITIALIZING,
        }
    ),
}


def can_transition(current: LifecycleState, target: LifecycleState) -> bool:
    """True si current -> target es una transicion legal (ADR-010)."""
    return target in LEGAL_TRANSITIONS[current]


@runtime_checkable
class ComponentLifecycle(Protocol):
    """Contrato de enganches de lifecycle que implementa un Componente.

    ADR-001: el Componente es un ROL por CONTRATOS. La MAQUINA de estados
    la conduce el supervisor de P04; el Componente solo expone estos
    enganches, que el supervisor invoca en cada transicion. Cada enganche
    hace el trabajo propio del Componente (adquirir recursos, suscribirse,
    parar) y NO cambia de estado por su cuenta. Si un enganche lanza una
    excepcion, el supervisor lleva la instancia a FAILED (ADR-010).
    Contrato estructural (Protocol): el Componente no hereda ninguna clase
    base.
    """

    def initialize(self) -> None:
        """REGISTERED -> INITIALIZED: adquiere recursos y cablea deps."""
        ...

    def start(self) -> None:
        """INITIALIZED -> RUNNING: arranca el trabajo del Componente."""
        ...

    def pause(self) -> None:
        """RUNNING -> PAUSED: cesa el consumo, conserva offset (ADR-010)."""
        ...

    def resume(self) -> None:
        """PAUSED -> RUNNING: reanuda el consumo desde el offset."""
        ...

    def stop(self) -> None:
        """RUNNING/PAUSED -> STOPPED: apagado ordenado."""
        ...

    def unload(self) -> None:
        """STOPPED -> UNLOADED: libera todo; teardown final."""
        ...
