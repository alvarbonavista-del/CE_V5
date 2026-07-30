"""Tests del registro de materializadores y del dispatch por source_id (MAT-06).

Sin BD: lo que se prueba aqui es el BINDING (que source_id -> funcion pura es el que
toca) y el FALLO RUIDOSO de una fuente servible sin materializador cableado. La
composicion real lector+materializador contra PostgreSQL vive en
tests/integration/test_market_footprint.py.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Never
from uuid import uuid4

import pytest

from ce_v5.entrypoints.worker_rules.composition import _materialize
from ce_v5.entrypoints.worker_rules.materializers import (
    FOOTPRINT_MATERIALIZERS,
    PROFILE_WINDOW_BARS,
    UnwiredSourceError,
)
from ce_v5.platform.rules.compiler import ExecutionPlan, ResolvedSource
from ce_v5.platform.rules.orderflow import (
    ORDERFLOW_DELTA_SOURCE_ID,
    orderflow_delta_declaration,
)
from ce_v5.platform.rules.volume_profile import (
    DEFAULT_BIN_COUNT,
    VP_POC_SOURCE_ID,
    VP_VAH_SOURCE_ID,
    VP_VAL_SOURCE_ID,
    compute_volume_profile,
)
from source.families.footprint import FootprintCell, FootprintClosedPayload
from source.families.market import MarketType, Timeframe
from source.time import MaturityState

if TYPE_CHECKING:
    from source.families.footprint import FootprintPayload

_TF = Timeframe.M1
_OPEN = 1_784_073_600_000


def _footprint(open_time: int, offset: Decimal) -> FootprintClosedPayload:
    """Un footprint de juguete con dos niveles, desplazado por offset.

    offset hace cada barra DISTINGUIBLE: si el materializador usara la ventana
    equivocada, el perfil saldria de otros precios y la igualdad Decimal fallaria.
    """
    cells = (
        FootprintCell(
            price=Decimal("100") + offset,
            buy_volume=Decimal("2"),
            sell_volume=Decimal("1"),
            delta=Decimal("1"),
        ),
        FootprintCell(
            price=Decimal("101") + offset,
            buy_volume=Decimal("5"),
            sell_volume=Decimal("3"),
            delta=Decimal("2"),
        ),
    )
    buy = sum((c.buy_volume for c in cells), Decimal(0))
    sell = sum((c.sell_volume for c in cells), Decimal(0))
    return FootprintClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol="BTC-USDT",
        timeframe=_TF,
        open_time=open_time,
        close_time=open_time + _TF.duration_ms,
        cells=cells,
        bar_buy_volume=buy,
        bar_sell_volume=sell,
        bar_delta=buy - sell,
        trade_count=4,
        is_complete=True,
    )


def _ventana(cuantas: int = 4) -> tuple[FootprintPayload, ...]:
    return tuple(
        _footprint(_OPEN + i * _TF.duration_ms, Decimal(i)) for i in range(cuantas)
    )


class TestRegistroPorSourceId:
    """4.1: el binding source_id -> funcion pura es el correcto, y la ventana es 100."""

    def test_las_tres_fuentes_vp_estan_cableadas(self) -> None:
        assert set(FOOTPRINT_MATERIALIZERS) == {
            VP_POC_SOURCE_ID,
            VP_VAH_SOURCE_ID,
            VP_VAL_SOURCE_ID,
        }

    @pytest.mark.parametrize(
        "source_id",
        [VP_POC_SOURCE_ID, VP_VAH_SOURCE_ID, VP_VAL_SOURCE_ID],
    )
    def test_la_ventana_rodante_es_la_de_paridad_v4(self, source_id: str) -> None:
        # [PARIDAD v4 _WINDOW] = 100 barras. Es constante FIJA de materializacion
        # (MAT-05 Q3), no parametro por regla: si alguien la moviera, el perfil dejaria
        # de ser el de v4 sin que ninguna declaracion cambiase.
        assert FOOTPRINT_MATERIALIZERS[source_id].window_bars == 100
        assert FOOTPRINT_MATERIALIZERS[source_id].window_bars == PROFILE_WINDOW_BARS

    def test_el_transform_de_poc_es_el_poc_del_perfil(self) -> None:
        ventana = _ventana()
        esperado = compute_volume_profile(ventana, bin_count=DEFAULT_BIN_COUNT).poc
        assert FOOTPRINT_MATERIALIZERS[VP_POC_SOURCE_ID].transform(ventana) == esperado

    def test_el_transform_de_vah_es_el_vah_del_perfil(self) -> None:
        ventana = _ventana()
        esperado = compute_volume_profile(ventana, bin_count=DEFAULT_BIN_COUNT).vah
        assert FOOTPRINT_MATERIALIZERS[VP_VAH_SOURCE_ID].transform(ventana) == esperado

    def test_el_transform_de_val_es_el_val_del_perfil(self) -> None:
        ventana = _ventana()
        esperado = compute_volume_profile(ventana, bin_count=DEFAULT_BIN_COUNT).val
        assert FOOTPRINT_MATERIALIZERS[VP_VAL_SOURCE_ID].transform(ventana) == esperado

    def test_cada_transform_lee_su_propia_salida(self) -> None:
        # Los tres NO son el mismo numero en esta ventana: si el registro cruzara los
        # bindings (poc -> vah, p.ej.), los tests de arriba seguirian pasando por
        # casualidad solo si POC=VAH=VAL. Aqui se comprueba que no lo son.
        ventana = _ventana()
        perfil = compute_volume_profile(ventana, bin_count=DEFAULT_BIN_COUNT)
        assert len({perfil.poc, perfil.vah, perfil.val}) > 1


class _SesionQueFalla:
    """Doble de Session que REVIENTA si alguien la usa: el dispatch no debe tocar BD."""

    def fetchall(self, *args: object, **kwargs: object) -> Never:
        msg = "el dispatch no debe consultar la BD para una fuente sin cablear."
        raise AssertionError(msg)

    def fetchone(self, *args: object, **kwargs: object) -> Never:
        msg = "el dispatch no debe consultar la BD para una fuente sin cablear."
        raise AssertionError(msg)

    def execute(self, *args: object, **kwargs: object) -> Never:
        msg = "el dispatch no debe consultar la BD para una fuente sin cablear."
        raise AssertionError(msg)


def _plan() -> ExecutionPlan:
    """Un ExecutionPlan minimo: al dispatch solo le importan exchange/symbol."""
    return ExecutionPlan(
        rule_id=uuid4(),
        tenant_id=uuid4(),
        product="alert",
        exchange="binance",
        symbol="BTC-USDT",
        trigger_keys=frozenset(),
        resolved_sources=(),
        fingerprint="0" * 64,
    )


class TestDispatchFalloRuidoso:
    """4.2: una servible sin materializador LANZA, no recibe una serie por defecto."""

    def test_orderflow_delta_sin_cablear_lanza_unwired(self) -> None:
        # orderflow.delta es SERVIBLE y esta en el catalogo vivo, pero en v5.0 no tiene
        # materializador. Servirle la ventana de cierres (el comportamiento viejo de
        # _series_for, que leia read_close_window para TODA fuente) le daria PRECIOS
        # donde espera DELTAS: un hecho falso con aspecto de correcto.
        source = ResolvedSource(
            source_id=ORDERFLOW_DELTA_SOURCE_ID,
            declaration=orderflow_delta_declaration(),
            history_bars=5,
        )
        with pytest.raises(UnwiredSourceError, match="no tiene materializador"):
            _materialize(
                _SesionQueFalla(),
                _plan(),
                _TF.value,
                _OPEN,
                source,
            )

    def test_el_mensaje_nombra_la_fuente(self) -> None:
        # El fallo tiene que decir QUE fuente falta, o el operador no sabe que cablear.
        source = ResolvedSource(
            source_id=ORDERFLOW_DELTA_SOURCE_ID,
            declaration=orderflow_delta_declaration(),
            history_bars=5,
        )
        with pytest.raises(UnwiredSourceError, match=ORDERFLOW_DELTA_SOURCE_ID):
            _materialize(
                _SesionQueFalla(),
                _plan(),
                _TF.value,
                _OPEN,
                source,
            )
