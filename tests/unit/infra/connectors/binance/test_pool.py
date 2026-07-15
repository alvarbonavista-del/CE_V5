"""Reparto de streams entre conexiones. SIN RED: la logica del pool es pura."""

from __future__ import annotations

import pytest

from ce_v5.infra.connectors.binance.pool import (
    BinanceLimits,
    ConnectionPlanner,
    ExchangeLimitExceeded,
)

_LIMITES_PEQUENOS = BinanceLimits(max_streams_per_connection=3, max_connections=2)


def _nombres(cuantos: int, desde: int = 0) -> set[str]:
    return {f"sym{i}@kline_1m" for i in range(desde, desde + cuantos)}


class TestReparto:
    def test_cabe_todo_en_una_conexion(self) -> None:
        planner = ConnectionPlanner(_LIMITES_PEQUENOS)
        plan = planner.assign(_nombres(3))
        assert list(plan) == [0]
        assert len(plan[0]) == 3

    def test_se_reparte_en_varias_conexiones(self) -> None:
        planner = ConnectionPlanner(_LIMITES_PEQUENOS)
        plan = planner.assign(_nombres(5))

        assert sorted(plan) == [0, 1]
        assert len(plan[0]) == 3  # la primera se llena...
        assert len(plan[1]) == 2  # ...y la segunda recoge el resto.
        # Ningun stream se pierde ni se duplica.
        todos = [s for streams in plan.values() for s in streams]
        assert sorted(todos) == sorted(_nombres(5))

    def test_es_determinista(self) -> None:
        uno = ConnectionPlanner(_LIMITES_PEQUENOS).assign(_nombres(5))
        otro = ConnectionPlanner(_LIMITES_PEQUENOS).assign(_nombres(5))
        assert uno == otro


class TestLimiteDeSupervivencia:
    def test_pasarse_del_tope_no_abre_nada(self) -> None:
        # Pasarse de los limites de Binance no da un error bonito: da un BANEO DE IP, y
        # un baneo deja sin datos a TODOS los usuarios a la vez. Preferimos fallar
        # nosotros, en claro y a tiempo.
        planner = ConnectionPlanner(_LIMITES_PEQUENOS)  # capacidad = 3 * 2 = 6
        with pytest.raises(ExchangeLimitExceeded, match="exchange_limit_exceeded"):
            planner.assign(_nombres(7))

        # Y NO deja nada asignado a medias.
        assert planner.current() == {}

    def test_los_limites_por_defecto_son_los_publicados_con_margen(self) -> None:
        limites = BinanceLimits()
        assert limites.max_streams_per_connection == 1024  # el publicado por Binance.
        assert limites.max_connections == 200  # por DEBAJO de los 300/5min: margen.
        assert limites.capacity() == 204_800


class TestEstabilidad:
    def test_un_alta_no_reubica_los_existentes(self) -> None:
        # SIN ESTO, dar de alta UN stream podria recolocar los otros mil: cerrar y
        # reabrir mil suscripciones es una tormenta de reconexiones contra el exchange
        # (rate limits, riesgo de baneo) y un hueco de datos en cada una. El coste de un
        # alta debe ser proporcional AL ALTA, no al total.
        planner = ConnectionPlanner(_LIMITES_PEQUENOS)
        planner.assign(_nombres(4))
        antes = dict(planner.current())

        planner.assign(_nombres(5))  # llega uno nuevo
        despues = planner.current()

        for nombre, conexion in antes.items():
            assert despues[nombre] == conexion  # NADIE se movio.
        assert despues["sym4@kline_1m"] == 1  # el nuevo, al hueco libre.

    def test_una_baja_no_reubica_a_los_que_se_quedan(self) -> None:
        planner = ConnectionPlanner(_LIMITES_PEQUENOS)
        planner.assign(_nombres(5))
        antes = dict(planner.current())

        quedan = _nombres(5) - {"sym0@kline_1m"}
        planner.assign(quedan)
        despues = planner.current()

        assert "sym0@kline_1m" not in despues
        for nombre in quedan:
            assert despues[nombre] == antes[nombre]  # los demas, quietos.

    def test_el_hueco_que_deja_una_baja_se_reutiliza(self) -> None:
        planner = ConnectionPlanner(_LIMITES_PEQUENOS)
        planner.assign(_nombres(5))

        # Se va uno de la conexion 0 y entra otro nuevo: debe caer en ese hueco.
        quedan = (_nombres(5) - {"sym0@kline_1m"}) | {"nuevo@kline_1m"}
        plan = planner.assign(quedan)

        assert len(plan[0]) == 3
        assert planner.current()["nuevo@kline_1m"] == 0
