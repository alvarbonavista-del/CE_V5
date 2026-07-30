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
    CVD_RESET_POLICY_V5,
    PROFILE_WINDOW_BARS,
    SOURCE_MATERIALIZERS,
    CvdIntegratorSpec,
    FootprintPointLocalSpec,
    FootprintWindowedSpec,
    UnwiredSourceError,
    _cvd_step,
)
from ce_v5.platform.rules.compiler import ExecutionPlan, ResolvedSource
from ce_v5.platform.rules.cvd import CVD_SOURCE_ID
from ce_v5.platform.rules.orderflow import (
    ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID,
    ORDERFLOW_DELTA_SOURCE_ID,
    orderflow_delta_momentum_declaration,
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

    offset hace cada barra DISTINGUIBLE en PRECIO y en DELTA: si el materializador usara
    la ventana equivocada, el perfil saldria de otros precios y la igualdad Decimal
    fallaria; y como el bar_delta tambien varia, una serie POINT_LOCAL desplazada
    tampoco puede coincidir por casualidad.
    """
    cells = (
        FootprintCell(
            price=Decimal("100") + offset,
            buy_volume=Decimal("2") + offset,
            sell_volume=Decimal("1"),
            delta=Decimal("2") + offset - Decimal("1"),
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


def _windowed(source_id: str) -> FootprintWindowedSpec:
    """El spec WINDOWED de una fuente. El isinstance NARROWS y a la vez ASEGURA el tipo.

    El registro esta tipado como el Protocol SourceMaterializer, que no expone transform
    ni window_bars: si un dia vp.poc pasara a otra clase de materializador, esto falla
    aqui en vez de dar un AttributeError en produccion.
    """
    spec = SOURCE_MATERIALIZERS[source_id]
    assert isinstance(spec, FootprintWindowedSpec)
    return spec


class TestRegistroPorSourceId:
    """4.1: el binding source_id -> funcion pura es el correcto, y la ventana es 100."""

    def test_el_registro_tiene_exactamente_las_fuentes_cableadas(self) -> None:
        # Exacto, no "al menos": una fuente que aparezca sin dictamen (o desaparezca en
        # un refactor) cambia lo que el motor sabe servir, y eso se ve aqui.
        assert set(SOURCE_MATERIALIZERS) == {
            VP_POC_SOURCE_ID,
            VP_VAH_SOURCE_ID,
            VP_VAL_SOURCE_ID,
            ORDERFLOW_DELTA_SOURCE_ID,
            CVD_SOURCE_ID,
        }

    @pytest.mark.parametrize(
        "source_id",
        [VP_POC_SOURCE_ID, VP_VAH_SOURCE_ID, VP_VAL_SOURCE_ID],
    )
    def test_la_ventana_rodante_es_la_de_paridad_v4(self, source_id: str) -> None:
        # [PARIDAD v4 _WINDOW] = 100 barras. Es constante FIJA de materializacion
        # (MAT-05 Q3), no parametro por regla: si alguien la moviera, el perfil dejaria
        # de ser el de v4 sin que ninguna declaracion cambiase.
        assert _windowed(source_id).window_bars == 100
        assert _windowed(source_id).window_bars == PROFILE_WINDOW_BARS

    def test_el_transform_de_poc_es_el_poc_del_perfil(self) -> None:
        ventana = _ventana()
        esperado = compute_volume_profile(ventana, bin_count=DEFAULT_BIN_COUNT).poc
        assert _windowed(VP_POC_SOURCE_ID).transform(ventana) == esperado

    def test_el_transform_de_vah_es_el_vah_del_perfil(self) -> None:
        ventana = _ventana()
        esperado = compute_volume_profile(ventana, bin_count=DEFAULT_BIN_COUNT).vah
        assert _windowed(VP_VAH_SOURCE_ID).transform(ventana) == esperado

    def test_el_transform_de_val_es_el_val_del_perfil(self) -> None:
        ventana = _ventana()
        esperado = compute_volume_profile(ventana, bin_count=DEFAULT_BIN_COUNT).val
        assert _windowed(VP_VAL_SOURCE_ID).transform(ventana) == esperado

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


class TestOrderflowDeltaPointLocal:
    """5.1: orderflow.delta esta cableada como POINT_LOCAL sobre footprint (MAT-07).

    El valor de la barra T es el bar_delta del footprint de T: ni ventana acotada ni
    recurrencia. Es la BASE que cvd.value acumulara en T5b-2, asi que el DAG se cablea
    bottom-up: si esta serie fuera la equivocada, el CVD entero mentiria.
    """

    def test_esta_cableada_con_un_spec_point_local(self) -> None:
        spec = SOURCE_MATERIALIZERS[ORDERFLOW_DELTA_SOURCE_ID]
        assert isinstance(spec, FootprintPointLocalSpec)

    def test_el_extract_es_el_bar_delta_de_la_barra(self) -> None:
        spec = SOURCE_MATERIALIZERS[ORDERFLOW_DELTA_SOURCE_ID]
        assert isinstance(spec, FootprintPointLocalSpec)
        footprint = _footprint(_OPEN, Decimal(3))
        assert spec.extract(footprint) == footprint.bar_delta

    def test_el_extract_no_es_una_constante(self) -> None:
        # Dos barras con delta DISTINTO dan valores distintos: si extract devolviera un
        # cero fijo (o el volumen en vez del delta), esto lo caza.
        spec = SOURCE_MATERIALIZERS[ORDERFLOW_DELTA_SOURCE_ID]
        assert isinstance(spec, FootprintPointLocalSpec)
        uno = _footprint(_OPEN, Decimal(1))
        otro = _footprint(_OPEN, Decimal(9))
        assert uno.bar_delta != otro.bar_delta
        assert spec.extract(uno) != spec.extract(otro)


class TestCvdIntegratorRegistrada:
    """3: cvd.value cableada como INTEGRATOR con replay desde snapshot (MAT-07).

    Aqui solo el BINDING y la recurrencia: el replay real (bootstrap, ancla y el GATE
    bit-exacto de ADR-007) exige BD y vive en el test de integracion del footprint,
    porque el snapshot que lo siembra es una fila de cvd_snapshot.
    """

    def test_esta_cableada_con_un_spec_integrator(self) -> None:
        spec = SOURCE_MATERIALIZERS[CVD_SOURCE_ID]
        assert isinstance(spec, CvdIntegratorSpec)

    def test_la_politica_de_reset_es_rolling(self) -> None:
        # v5.0 solo materializa rolling: la guarda del compilador (MAT-05 Q4) rechaza
        # params de fuente, asi que cvd.value llega siempre con su default. Si esto
        # cambiara a session_utc sin propagar params, el motor serviria un acumulado
        # reseteado a quien pidio el continuo.
        spec = SOURCE_MATERIALIZERS[CVD_SOURCE_ID]
        assert isinstance(spec, CvdIntegratorSpec)
        assert spec.reset_policy == "rolling"
        assert spec.reset_policy == CVD_RESET_POLICY_V5

    def test_la_recurrencia_acumula(self) -> None:
        # cvd[T] = cvd[T-1] + delta[T], con signo: un delta negativo BAJA el acumulado.
        assert _cvd_step(Decimal(3), Decimal(-5)) == Decimal(-2)
        assert _cvd_step(Decimal(0), Decimal("1.5")) == Decimal("1.5")
        assert _cvd_step(Decimal("-2.25"), Decimal("0.25")) == Decimal(-2)


class TestDispatchFalloRuidoso:
    """4.2: una servible sin materializador LANZA, no recibe una serie por defecto.

    La fuente-ejemplo es orderflow.delta_momentum: la UNICA servible del catalogo vivo
    que sigue sin materializador. (El ejemplo ha ido cambiando conforme el DAG se
    cablea: era orderflow.delta hasta MAT-07 T5b-1, luego cvd.value hasta T5b-2b; ambas
    ya estan cableadas y dejarian de probar nada.)
    """

    def test_delta_momentum_sin_cablear_lanza_unwired(self) -> None:
        # orderflow.delta_momentum es SERVIBLE y esta en el catalogo vivo, pero en v5.0
        # no tiene materializador. Servirle la ventana de cierres (el comportamiento
        # viejo de _series_for, que leia read_close_window para TODA fuente) le daria
        # PRECIOS donde espera un CAMBIO de delta: un hecho falso con aspecto correcto.
        source = ResolvedSource(
            source_id=ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID,
            declaration=orderflow_delta_momentum_declaration(),
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
            source_id=ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID,
            declaration=orderflow_delta_momentum_declaration(),
            history_bars=5,
        )
        with pytest.raises(
            UnwiredSourceError, match=ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID
        ):
            _materialize(
                _SesionQueFalla(),
                _plan(),
                _TF.value,
                _OPEN,
                source,
            )
