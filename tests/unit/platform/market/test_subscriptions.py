"""Unit tests del SubscriptionManager (ADR-014): ref-count, histeresis y reconstruccion.

Con una demanda falsa (un dict que el test controla), un controlador falso que APUNTA
cada open/close, y un SimulatedClock. Sin hilos y sin temporizadores: el paso del
tiempo lo decide QUIEN LLAMA a reconcile(), no un reloj oculto dentro del manager.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Set as AbstractSet

import pytest

from ce_v5.core.clock import SimulatedClock
from ce_v5.platform.market.subscriptions import (
    HysteresisConfig,
    SubscriptionManager,
)
from source.families.market import MarketStreamKey

_AHORA = 1_784_073_600_000
_OFF_DELAY = 30_000

_BTC = "market:candles:binance:spot:BTC-USDT:1m"
_ETH = "market:candles:binance:spot:ETH-USDT:1m"
_DOGE = "market:candles:binance:spot:DOGE-USDT:1h"


class _DemandaFalsa:
    """La demanda agregada que devolveria la ventanilla. El test la maneja."""

    def __init__(self, inicial: Mapping[str, int] | None = None) -> None:
        self.actual: dict[str, int] = dict(inicial or {})

    def snapshot(self) -> Mapping[str, int]:
        return dict(self.actual)


class _ControladorFalso:
    """El mundo real, fingido: apunta cada open y cada close que recibe."""

    def __init__(self, activos: AbstractSet[str] | None = None) -> None:
        self._activos: set[str] = set(activos or set())
        self.opened: list[str] = []
        self.closed: list[str] = []

    def open(self, key: MarketStreamKey) -> None:
        clave = key.as_stream_key()
        self.opened.append(clave)
        self._activos.add(clave)

    def close(self, key: MarketStreamKey) -> None:
        clave = key.as_stream_key()
        self.closed.append(clave)
        self._activos.discard(clave)

    def active(self) -> AbstractSet[str]:
        return set(self._activos)


@pytest.fixture
def reloj() -> SimulatedClock:
    return SimulatedClock(start_ms=_AHORA)


def _manager(
    demanda: _DemandaFalsa, controlador: _ControladorFalso, reloj: SimulatedClock
) -> SubscriptionManager:
    return SubscriptionManager(
        demand=demanda,
        controller=controlador,
        clock=reloj,
        hysteresis=HysteresisConfig(off_delay_ms=_OFF_DELAY),
    )


class TestLosPublicosNoSeDuplican:
    def test_a_dos_tenants_un_solo_stream(self, reloj: SimulatedClock) -> None:
        # ADICION (a) de la validacion en caliente, a nivel de logica: dos sujetos
        # piden el MISMO flujo y se abre UNA sola conexion al exchange. Si se abriese
        # una por tenant, volveria la explosion N x M que ADR-014 existe para evitar.
        demanda = _DemandaFalsa({_BTC: 2})
        controlador = _ControladorFalso()
        manager = _manager(demanda, controlador, reloj)

        resultado = manager.reconcile()

        assert controlador.opened == [_BTC]  # UNA vez, no dos.
        assert resultado.ref_counts == {_BTC: 2}
        assert manager.state() == {_BTC: 2}

    def test_g_el_encendido_es_inmediato(self, reloj: SimulatedClock) -> None:
        # Si alguien pide datos, los datos NO esperan: se abre en el MISMO ciclo, sin
        # que haga falta avanzar el reloj. Un retardo al abrir seria latencia pura.
        demanda = _DemandaFalsa()
        controlador = _ControladorFalso()
        manager = _manager(demanda, controlador, reloj)
        manager.reconcile()
        assert controlador.opened == []

        demanda.actual[_BTC] = 1
        manager.reconcile()

        assert controlador.opened == [_BTC]


class TestElStreamSigueVivoMientrasAlguienLoQuiera:
    def test_b_uno_se_retira_y_el_stream_no_se_cierra(
        self, reloj: SimulatedClock
    ) -> None:
        # De 2 interesados a 1: el stream NO se toca. Cerrarlo seria dejar sin datos a
        # quien sigue mirando.
        demanda = _DemandaFalsa({_BTC: 2})
        controlador = _ControladorFalso()
        manager = _manager(demanda, controlador, reloj)
        manager.reconcile()

        demanda.actual[_BTC] = 1
        reloj.advance(_OFF_DELAY * 10)  # aunque pase MUCHO tiempo.
        resultado = manager.reconcile()

        assert controlador.closed == []
        assert resultado.ref_counts == {_BTC: 1}

    def test_c_el_ultimo_se_retira_y_el_cierre_espera_su_plazo(
        self, reloj: SimulatedClock
    ) -> None:
        demanda = _DemandaFalsa({_BTC: 1})
        controlador = _ControladorFalso()
        manager = _manager(demanda, controlador, reloj)
        manager.reconcile()

        # Se va el ultimo: NO se cierra todavia, queda pendiente.
        del demanda.actual[_BTC]
        resultado = manager.reconcile()
        assert controlador.closed == []
        assert resultado.pending_close == (_BTC,)

        # Aun no ha vencido el plazo: sigue abierto.
        reloj.advance(_OFF_DELAY - 1)
        resultado = manager.reconcile()
        assert controlador.closed == []
        assert resultado.pending_close == (_BTC,)

        # Vence el plazo y sigue sin demanda: AHORA si se cierra.
        reloj.advance(1)
        resultado = manager.reconcile()
        assert controlador.closed == [_BTC]
        assert resultado.closed == (_BTC,)
        assert resultado.pending_close == ()


class TestAntiFlapping:
    def test_d_la_demanda_parpadea_y_el_stream_nunca_se_cierra(
        self, reloj: SimulatedClock
    ) -> None:
        # ADICION (c): la demanda va 1 -> 0 -> 1 -> 0 -> 1, siempre por debajo del
        # off_delay. Un stream que se cierra y se reabre cinco veces en diez segundos
        # castiga al exchange (rate limits, baneos de IP), pierde datos en cada hueco
        # y obliga a un bootstrap REST cada vez. Resultado exigido: CERO cierres.
        demanda = _DemandaFalsa({_BTC: 1})
        controlador = _ControladorFalso()
        manager = _manager(demanda, controlador, reloj)
        manager.reconcile()

        for _ in range(2):
            del demanda.actual[_BTC]
            reloj.advance(_OFF_DELAY // 3)
            manager.reconcile()

            demanda.actual[_BTC] = 1
            reloj.advance(_OFF_DELAY // 3)
            manager.reconcile()

        assert controlador.closed == []
        # Y no se reabrio: nunca llego a cerrarse, asi que el open fue uno solo.
        assert controlador.opened == [_BTC]

    def test_al_volver_la_demanda_se_cancela_el_cierre_pendiente(
        self, reloj: SimulatedClock
    ) -> None:
        # El cronometro se REINICIA: si vuelve la demanda y se va otra vez, el plazo
        # empieza de cero. Sin esto, un stream que estuvo a punto de cerrarse se
        # cerraria en cuanto se fuese el interes, aunque hubiese vuelto a usarse.
        demanda = _DemandaFalsa({_BTC: 1})
        controlador = _ControladorFalso()
        manager = _manager(demanda, controlador, reloj)
        manager.reconcile()

        del demanda.actual[_BTC]
        manager.reconcile()  # queda pendiente de cierre.

        demanda.actual[_BTC] = 1
        reloj.advance(_OFF_DELAY * 2)
        resultado = manager.reconcile()  # vuelve la demanda: se cancela el cierre.
        assert controlador.closed == []
        assert resultado.pending_close == ()

        del demanda.actual[_BTC]
        reloj.advance(_OFF_DELAY - 1)
        manager.reconcile()
        # El plazo cuenta desde AHORA, no desde el primer intento de cierre.
        assert controlador.closed == []


class TestReconstruccionTrasReinicio:
    def test_e_manager_nuevo_sin_memoria_abre_exactamente_la_demanda(
        self, reloj: SimulatedClock
    ) -> None:
        # ADICION (b): el ref-count NO se persiste. Un manager RECIEN NACIDO, sin
        # memoria de nada, lee la demanda persistida y reconstruye el mundo. Ni una
        # mas, ni una menos, sin duplicar.
        demanda = _DemandaFalsa({_BTC: 3, _ETH: 1, _DOGE: 2})
        controlador = _ControladorFalso()  # el proceso murio: cero streams vivos.
        manager = _manager(demanda, controlador, reloj)

        resultado = manager.reconcile()

        assert sorted(controlador.opened) == sorted([_BTC, _ETH, _DOGE])
        assert len(controlador.opened) == 3  # sin duplicados.
        assert resultado.ref_counts == {_BTC: 3, _ETH: 1, _DOGE: 2}

    def test_e_no_reabre_los_streams_que_seguian_vivos(
        self, reloj: SimulatedClock
    ) -> None:
        # Reinicio del WORKER sin caida de las conexiones: dos de los tres flujos ya
        # estaban abiertos. Reabrirlos seria tirar la conexion buena y castigar al
        # exchange. Solo se abre el que falta.
        demanda = _DemandaFalsa({_BTC: 1, _ETH: 1, _DOGE: 1})
        controlador = _ControladorFalso(activos={_BTC, _ETH})
        manager = _manager(demanda, controlador, reloj)

        manager.reconcile()

        assert controlador.opened == [_DOGE]
        assert controlador.closed == []


class TestFaultIsolation:
    def test_f_una_clave_corrupta_no_impide_abrir_las_demas(
        self, reloj: SimulatedClock
    ) -> None:
        # Una clave corrupta NO puede dejar sin datos a los otros 200 streams. Se
        # registra como invalida y el ciclo sigue.
        demanda = _DemandaFalsa({_BTC: 1, "basura::no-es-una-clave": 5, _ETH: 1})
        controlador = _ControladorFalso()
        manager = _manager(demanda, controlador, reloj)

        resultado = manager.reconcile()

        assert sorted(controlador.opened) == sorted([_BTC, _ETH])
        assert resultado.invalid == ("basura::no-es-una-clave",)
        # La corrupta NO entra en el ref-count: no existe para el sistema.
        assert resultado.ref_counts == {_BTC: 1, _ETH: 1}
