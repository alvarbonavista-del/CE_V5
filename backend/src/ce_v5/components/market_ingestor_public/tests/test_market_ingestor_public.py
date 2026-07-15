"""Tests del Componente ingestor de market data publico (P07, ADR-001/010).

Con dobles inyectados: el componente no construye nada, asi que probarlo no exige ni
PostgreSQL, ni Redis, ni un exchange. Esa es justo la ventaja de que el cerebro se
cablee fuera.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet

import pytest

from ce_v5.components.market_ingestor_public import (
    PublicMarketIngestorComponent,
    build,
)
from ce_v5.core.component import ComponentLifecycle
from source.families.market import MarketStreamKey

_BTC = "market:candles:binance:spot:BTC-USDT:1m"
_ETH = "market:candles:binance:spot:ETH-USDT:1m"


class _ManagerFalso:
    def __init__(self) -> None:
        self.reconciles = 0

    def reconcile(self) -> object:
        self.reconciles += 1
        return {"reconciled": self.reconciles}


class _EngineFalso:
    def __init__(self) -> None:
        self.drains = 0

    def drain_once(self) -> object:
        self.drains += 1
        return {"drained": self.drains}


class _SourceFalso:
    def __init__(self, activos: set[str] | None = None) -> None:
        self._activos = set(activos or set())
        self.cerrados: list[str] = []

    def active(self) -> AbstractSet[str]:
        return set(self._activos)

    def close(self, key: MarketStreamKey) -> None:
        clave = key.as_stream_key()
        self.cerrados.append(clave)
        self._activos.discard(clave)


class _SourceQueRevienta(_SourceFalso):
    """Un feed que falla: el exchange no responde, la red parpadea."""


class _EngineRoto:
    def drain_once(self) -> object:
        msg = "el exchange no responde"
        raise RuntimeError(msg)


class _EngineIntermitente:
    """Falla las primeras veces y luego se recupera: un fallo TRANSITORIO."""

    def __init__(self, fallos: int) -> None:
        self._fallos_restantes = fallos
        self.drains = 0

    def drain_once(self) -> object:
        if self._fallos_restantes > 0:
            self._fallos_restantes -= 1
            msg = "el exchange no responde"
            raise RuntimeError(msg)
        self.drains += 1
        return {"drained": self.drains}


def _componente(
    manager: _ManagerFalso | None = None,
    engine: object | None = None,
    source: _SourceFalso | None = None,
) -> PublicMarketIngestorComponent:
    return build(
        subscription_manager=manager or _ManagerFalso(),
        engine=engine or _EngineFalso(),  # type: ignore[arg-type]
        source=source or _SourceFalso(),
    )


def test_build_devuelve_el_componente() -> None:
    assert isinstance(_componente(), PublicMarketIngestorComponent)


def test_cumple_el_contrato_de_lifecycle_por_composicion() -> None:
    # Por COMPOSICION (ADR-001): no hereda ninguna clase base, satisface el contrato
    # por FORMA. Si algun dia le faltara un enganche, esto se pondria rojo.
    assert isinstance(_componente(), ComponentLifecycle)


class TestLifecycleCompleto:
    def test_de_initialize_a_unload(self) -> None:
        manager, engine = _ManagerFalso(), _EngineFalso()
        source = _SourceFalso({_BTC, _ETH})
        component = _componente(manager, engine, source)

        # Antes de arrancar no se trabaja: un tick no hace nada.
        assert component.tick().processed is False
        assert manager.reconciles == 0

        component.initialize()
        # Initialize NO abre streams: los abre el primer tick, y solo segun la DEMANDA
        # real. Conectarse al exchange "por si acaso" seria gastar una conexion para
        # nadie.
        assert component.tick().processed is False  # aun no ha arrancado.

        component.start()
        assert component.running is True

        reporte = component.tick()
        assert reporte.processed is True
        assert manager.reconciles == 1  # (1) ajusta streams a la demanda...
        assert engine.drains == 1  # (2) ...y drena lo que llego.
        assert reporte.reconcile == {"reconciled": 1}
        assert reporte.ingestion == {"drained": 1}

        component.pause()
        assert component.tick().processed is False
        assert manager.reconciles == 1  # NO trabajo estando en pausa.
        assert engine.drains == 1
        # Y NO cerro los streams: ADR-010, PAUSE conserva el offset. Cerrarlos obligaria
        # a un bootstrap REST completo al reanudar y dejaria un hueco de datos.
        assert source.cerrados == []
        assert source.active() == {_BTC, _ETH}

        component.resume()
        assert component.tick().processed is True
        assert manager.reconciles == 2  # vuelve a trabajar.
        assert engine.drains == 2

        component.stop()
        # AHORA si se cierran: el componente deja de existir para el sistema, y un
        # stream que sobrevive a su componente es una conexion zombi.
        assert sorted(source.cerrados) == sorted([_BTC, _ETH])
        assert source.active() == set()
        assert component.running is False

        component.unload()
        assert component.tick().processed is False

    def test_initialize_es_idempotente(self) -> None:
        component = _componente()
        component.initialize()
        component.initialize()
        component.start()
        assert component.tick().processed is True

    def test_stop_sin_streams_abiertos_no_falla(self) -> None:
        source = _SourceFalso()
        component = _componente(source=source)
        component.initialize()
        component.start()

        component.stop()

        assert source.cerrados == []


class TestFaultIsolation:
    def test_una_excepcion_en_el_ciclo_no_mata_el_componente(self) -> None:
        # Un exchange que falla un poll NO puede tumbar el worker: si muriera, dejaria
        # de ingerir para TODOS los usuarios, que es infinitamente peor que perder un
        # ciclo.
        manager = _ManagerFalso()
        source = _SourceFalso({_BTC})
        component = build(
            subscription_manager=manager,
            engine=_EngineRoto(),
            source=source,
        )
        component.initialize()
        component.start()

        reporte = component.tick()

        assert reporte.processed is False
        assert reporte.degraded is True
        assert reporte.error is not None
        assert "el exchange no responde" in reporte.error
        # El componente SIGUE VIVO y en marcha: no se apago solo.
        assert component.running is True

    def test_el_siguiente_tick_reintenta_y_se_recupera(self) -> None:
        # El fallo era TRANSITORIO (el exchange parpadeo). El componente no necesita
        # que nadie lo resucite: el siguiente ciclo simplemente vuelve a intentarlo.
        engine = _EngineIntermitente(fallos=1)
        component = build(
            subscription_manager=_ManagerFalso(),
            engine=engine,
            source=_SourceFalso(),
        )
        component.initialize()
        component.start()

        assert component.tick().degraded is True
        assert engine.drains == 0

        reporte = component.tick()

        assert reporte.processed is True
        assert reporte.degraded is False
        assert engine.drains == 1


@pytest.mark.parametrize("enganche", ["initialize", "start", "pause", "resume", "stop"])
def test_los_enganches_existen_y_son_invocables(enganche: str) -> None:
    component = _componente()
    getattr(component, enganche)()
