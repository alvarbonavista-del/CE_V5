"""Registro minimo de connectors de market data por convencion (T-03-A).

Sustituye al if-chain de seleccion que T-03 descubrio en el paso 0. Vive en el
COMPOSITION ROOT (la unica capa que puede ver a la vez el puerto MarketDataSourcePort,
de platform, y los adaptadores concretos, de infra; el contrato de capas prohibe que
infra importe platform, asi que el registro no puede vivir junto a los adaptadores).
No es un framework de plugins: sin discovery por carpeta, sin imports dinamicos
opacos, sin service locator global. Es un registro explicito, tipado y fail-loud, que
el composition root construye y consulta.

Anadir un exchange nuevo NO edita ninguna estructura de ramas: se crea su carpeta en
infra/connectors/<exchange>/ con su connector y su registro local (KIND + create), y
se enchufa con una sola linea plana en build_default_registry.
"""

from __future__ import annotations

from collections.abc import Callable

from ce_v5.platform.market.datasource import MarketDataSourcePort

ConnectorFactory = Callable[[], MarketDataSourcePort]


class DuplicateConnectorKindError(RuntimeError):
    """Se intento registrar dos factories bajo el mismo 'kind'. Fail-loud a proposito:
    una colision silenciosa dejaria una de las dos en pie sin saber cual, y el proceso
    podria ingerir del exchange equivocado.
    """


class UnknownConnectorKindError(RuntimeError):
    """Se pidio resolver un 'kind' que nadie registro. Fail-loud a proposito: un
    default silencioso arrancaria el worker contra un exchange no pedido, o sin datos,
    aparentando salud.
    """


class ConnectorRegistry:
    """Mapa explicito de 'kind' -> factory de MarketDataSourcePort.

    register: da de alta un 'kind'; colision -> DuplicateConnectorKindError.
    resolve: da el datasource del 'kind'; desconocido -> UnknownConnectorKindError.
    Devuelve el PUERTO (MarketDataSourcePort), nunca una clase concreta filtrada al
    motor: el ingestor sigue dependiendo solo del puerto.
    """

    def __init__(self) -> None:
        self._factories: dict[str, ConnectorFactory] = {}

    def register(self, kind: str, factory: ConnectorFactory) -> None:
        if kind in self._factories:
            msg = (
                f"kind de connector {kind!r} ya registrado: colision. Cada exchange "
                "aporta su registro local una sola vez."
            )
            raise DuplicateConnectorKindError(msg)
        self._factories[kind] = factory

    def resolve(self, kind: str) -> MarketDataSourcePort:
        try:
            factory = self._factories[kind]
        except KeyError:
            registrados = ", ".join(sorted(self._factories)) or "(ninguno)"
            msg = (
                f"kind de connector {kind!r} desconocido. Registrados: {registrados}. "
                "No hay default: un exchange no pedido no se arranca en silencio."
            )
            raise UnknownConnectorKindError(msg) from None
        return factory()

    def kinds(self) -> frozenset[str]:
        """Los 'kind' registrados (observabilidad y tests)."""
        return frozenset(self._factories)


def build_default_registry() -> ConnectorRegistry:
    """El registro con los connectors que P07/T-03 traen de serie.

    Cada linea es un registro PLANO (no una rama): anadir un exchange es su carpeta en
    infra/connectors/<exchange>/ mas una sola linea aqui.
    """
    from ce_v5.infra.connectors import fake_registration
    from ce_v5.infra.connectors.binance import registration as binance_registration
    from ce_v5.infra.connectors.okx import registration as okx_registration

    registry = ConnectorRegistry()
    registry.register(binance_registration.KIND, binance_registration.create)
    registry.register(fake_registration.KIND, fake_registration.create)
    registry.register(okx_registration.KIND, okx_registration.create)
    return registry
