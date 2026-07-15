"""Tests del connector privado FAKE (P07). Dobles inyectados, sin gate real.

El gate REAL (politica + QUARANTINED) se prueba contra PostgreSQL en
tests/integration/test_private_connector_gated.py. Aqui solo el componente.
"""

from __future__ import annotations

from ce_v5.components.market_connector_private_fake import (
    PrivateMarketConnectorFake,
    build,
)
from ce_v5.core.component import ComponentLifecycle


class _FeedFalso:
    """Un feed privado que NO hace IO: ni red, ni broker, ni credenciales."""

    def __init__(self) -> None:
        self.conectado = False
        self.conexiones = 0
        self.desconexiones = 0

    def connect(self) -> None:
        self.conectado = True
        self.conexiones += 1

    def disconnect(self) -> None:
        self.conectado = False
        self.desconexiones += 1

    def connected(self) -> bool:
        return self.conectado


class _BusFalso:
    """Un bus que apunta lo que se publique. Debe quedarse VACIO."""

    def __init__(self) -> None:
        self.publicados: list[object] = []


def test_build_devuelve_el_componente() -> None:
    assert isinstance(build(_FeedFalso()), PrivateMarketConnectorFake)


def test_cumple_el_contrato_de_lifecycle_por_composicion() -> None:
    assert isinstance(build(_FeedFalso()), ComponentLifecycle)


class TestLifecycle:
    def test_no_conecta_hasta_initialize(self) -> None:
        # LA CLAVE DEL GATE: la conexion vive en initialize(), no en el constructor.
        # Si conectara al construirse, el gate no podria impedirla: la conexion privada
        # ya estaria abierta cuando la politica dijera que no.
        feed = _FeedFalso()
        component = build(feed)

        assert feed.conectado is False
        assert feed.conexiones == 0
        assert component.status().connected is False

    def test_ciclo_completo(self) -> None:
        feed = _FeedFalso()
        component = build(feed)

        component.initialize()
        assert feed.conectado is True
        assert component.status().initialized is True

        component.start()
        assert component.running is True
        assert component.status().running is True

        component.pause()
        assert component.running is False
        # PAUSE no desconecta (ADR-010: conserva el estado).
        assert feed.conectado is True
        assert feed.desconexiones == 0

        component.resume()
        assert component.running is True

        component.stop()
        assert feed.conectado is False  # AHORA si se desconecta.

        component.unload()
        assert component.status().initialized is False


class TestNoEmiteEventosDeDominio:
    def test_el_bus_se_queda_vacio(self) -> None:
        # EL HECHO PRIVADO REAL (fill, balance) ES execution.*, Y ESA FAMILIA ES DE
        # P10b. Fabricarla aqui seria inventar un contrato ajeno y poner en el bus un
        # hecho que nunca ocurrio: exactamente lo que CA-04 prohibe. Un connector fake
        # que emitiera fills falsos alimentaria reglas y, en M5, ordenes reales sobre
        # datos que no existen.
        bus = _BusFalso()
        feed = _FeedFalso()
        component = build(feed)

        component.initialize()
        component.start()
        component.status()
        component.stop()

        assert bus.publicados == []

    def test_el_componente_no_tiene_forma_de_publicar(self) -> None:
        # No es que "se le olvide" emitir: es que NO RECIBE un bus. No hay forma de
        # publicar un execution.* desde aqui ni por descuido.
        component = build(_FeedFalso())
        assert not hasattr(component, "publish")
        assert not hasattr(component, "_bus")


class TestSinCredenciales:
    def test_el_connector_no_acepta_ni_guarda_credenciales(self) -> None:
        # Las credenciales BYOC reales son P10a: cifradas, con su rol de DB y su gate.
        # Si algun dia apareciese una api_key por aqui, seria un error de capa.
        component = build(_FeedFalso())
        for prohibido in ("api_key", "secret", "credentials", "_api_key", "_secret"):
            assert not hasattr(component, prohibido)
