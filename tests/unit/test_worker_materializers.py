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
    DerivedSeriesSpec,
    FootprintPointLocalSpec,
    FootprintWindowedSpec,
    UnwiredSourceError,
    _cvd_session_step,
    _cvd_step,
)
from ce_v5.platform.rules.compiler import ExecutionPlan, ResolvedSource
from ce_v5.platform.rules.cvd import CVD_SOURCE_ID, ResetPolicy
from ce_v5.platform.rules.orderflow import (
    ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID,
    ORDERFLOW_DELTA_SOURCE_ID,
    compute_delta_momentum,
    orderflow_delta_momentum_declaration,
)
from ce_v5.platform.rules.pivotphase import (
    PIVOTPHASE_CONFIDENCE_SOURCE_ID,
    PIVOTPHASE_PHASE_SOURCE_ID,
)
from ce_v5.platform.rules.volume_profile import (
    DEFAULT_BIN_COUNT,
    VP_HVN_SOURCE_ID,
    VP_LVN_SOURCE_ID,
    VP_POC_SOURCE_ID,
    VP_VAH_SOURCE_ID,
    VP_VAL_SOURCE_ID,
    compute_volume_profile,
    select_hvn_price,
    select_lvn_price,
)
from source.families.footprint import FootprintCell, FootprintClosedPayload
from source.families.market import MarketType, Timeframe
from source.rules.scalar import ScalarType, ScalarValue
from source.time import MaturityState

if TYPE_CHECKING:
    from source.families.footprint import FootprintPayload

_TF = Timeframe.M1
_OPEN = 1_784_073_600_000

# Fuente-ejemplo SINTETICA del fallo ruidoso: no existe en el catalogo ni en el
# registro, y no debe existir nunca (lo vigila un test). Cumple el patron de source_id
# del contrato (dominio.campo en snake_case) para que sea un id valido, no un absurdo.
_FUENTE_SINTETICA = "test.fuente_sin_materializador"


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
            VP_HVN_SOURCE_ID,
            VP_LVN_SOURCE_ID,
            ORDERFLOW_DELTA_SOURCE_ID,
            CVD_SOURCE_ID,
            ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID,
            "footprint.price_range",
            PIVOTPHASE_PHASE_SOURCE_ID,
            PIVOTPHASE_CONFIDENCE_SOURCE_ID,
        }

    @pytest.mark.parametrize(
        "source_id",
        [
            VP_POC_SOURCE_ID,
            VP_VAH_SOURCE_ID,
            VP_VAL_SOURCE_ID,
            VP_HVN_SOURCE_ID,
            VP_LVN_SOURCE_ID,
        ],
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

    def test_el_transform_de_hvn_es_el_select_hvn_price_del_perfil(self) -> None:
        ventana = _ventana()
        esperado = select_hvn_price(ventana, bin_count=DEFAULT_BIN_COUNT)
        assert _windowed(VP_HVN_SOURCE_ID).transform(ventana) == esperado

    def test_el_transform_de_lvn_es_el_select_lvn_price_del_perfil(self) -> None:
        ventana = _ventana()
        esperado = select_lvn_price(ventana, bin_count=DEFAULT_BIN_COUNT)
        assert _windowed(VP_LVN_SOURCE_ID).transform(ventana) == esperado

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

    def test_la_politica_de_reset_por_defecto_es_rolling(self) -> None:
        # El REGISTRO guarda la instancia con el default: es compartida por todas las
        # reglas y no se muta. Una regla que pida session_utc recibe una COPIA ligada
        # (with_params), nunca cambia la del registro.
        spec = SOURCE_MATERIALIZERS[CVD_SOURCE_ID]
        assert isinstance(spec, CvdIntegratorSpec)
        assert spec.reset_policy == "rolling"
        assert spec.reset_policy == CVD_RESET_POLICY_V5

    def test_with_params_liga_session_utc_sin_mutar_el_registro(self) -> None:
        # MAT-05 Q2: el param efectivo produce una copia con otra politica, y la del
        # registro sigue en rolling (si se mutara, una regla contaminaria a las demas).
        spec = SOURCE_MATERIALIZERS[CVD_SOURCE_ID]
        assert isinstance(spec, CvdIntegratorSpec)
        ligada = spec.with_params(
            {
                "reset_policy": ScalarValue(
                    scalar_type=ScalarType.STRING,
                    string_value=ResetPolicy.SESSION_UTC.value,
                )
            }
        )
        assert isinstance(ligada, CvdIntegratorSpec)
        assert ligada.reset_policy == ResetPolicy.SESSION_UTC.value
        assert spec.reset_policy == ResetPolicy.ROLLING.value

    def test_with_params_sin_el_param_devuelve_el_mismo_spec(self) -> None:
        # ADITIVIDAD D7: sin override, nada cambia.
        spec = SOURCE_MATERIALIZERS[CVD_SOURCE_ID]
        assert isinstance(spec, CvdIntegratorSpec)
        assert spec.with_params({}) is spec

    def test_with_params_rechaza_una_politica_fuera_del_enum(self) -> None:
        # Desde el fix de la seccion 34 el compilador YA rechaza este dominio para
        # cualquier override que pase por compile(); este test cubre la llamada
        # DIRECTA a with_params (fuera de un plan compilado), su ultima linea de
        # defensa. Un texto valido pero sin sentido falla RUIDOSO, no cae a rolling.
        spec = SOURCE_MATERIALIZERS[CVD_SOURCE_ID]
        assert isinstance(spec, CvdIntegratorSpec)
        with pytest.raises(UnwiredSourceError, match="no es una politica de cvd"):
            spec.with_params(
                {
                    "reset_policy": ScalarValue(
                        scalar_type=ScalarType.STRING, string_value="semanal"
                    )
                }
            )

    def test_la_recurrencia_acumula(self) -> None:
        # cvd[T] = cvd[T-1] + delta[T], con signo: un delta negativo BAJA el acumulado.
        assert _cvd_step(Decimal(3), Decimal(-5)) == Decimal(-2)
        assert _cvd_step(Decimal(0), Decimal("1.5")) == Decimal("1.5")
        assert _cvd_step(Decimal("-2.25"), Decimal("0.25")) == Decimal(-2)

    def test_la_recurrencia_de_sesion_reinicia_en_la_barra_que_abre_dia(self) -> None:
        # La barra que ABRE dia UTC nuevo arranca en su propio delta (ignora el previo);
        # el resto acumula como rolling.
        assert _cvd_session_step(Decimal(100), (True, Decimal("4"))) == Decimal("4")
        assert _cvd_session_step(Decimal(100), (False, Decimal("4"))) == Decimal("104")


class TestDeltaMomentumDerivada:
    """4.1/4.2: delta_momentum cableada como DAG de 2o NIVEL (MAT-08).

    Es la primera fuente que consume otra fuente DERIVADA (orderflow.delta) en vez del
    footprint crudo. Aqui solo el BINDING y la consistencia del registro; el recorrido
    real con BD (y la prueba de que lookback=1 da a la primera barra de la ventana su
    prior REAL) vive en el test de integracion del footprint.
    """

    def test_esta_cableada_con_un_spec_derivado(self) -> None:
        spec = SOURCE_MATERIALIZERS[ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID]
        assert isinstance(spec, DerivedSeriesSpec)

    def test_el_binding_es_delta_con_lookback_uno(self) -> None:
        # base = orderflow.delta (no el footprint), transform = la funcion pura de
        # paridad v4, lookback = 1 porque un diff barra-a-barra necesita UNA barra
        # previa para que el primer valor de la ventana no salga de la nada.
        spec = SOURCE_MATERIALIZERS[ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID]
        assert isinstance(spec, DerivedSeriesSpec)
        assert spec.base_source_id == ORDERFLOW_DELTA_SOURCE_ID
        assert spec.transform is compute_delta_momentum
        assert spec.lookback == 1

    def test_la_base_declarada_existe_en_el_registro(self) -> None:
        # CONDICION MAT-08: un DerivedSeriesSpec cuya base NO este cableada es un
        # registro incoherente que solo se descubriria al evaluar una regla real. Se
        # comprueba en frio, en construccion.
        spec = SOURCE_MATERIALIZERS[ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID]
        assert isinstance(spec, DerivedSeriesSpec)
        assert spec.base_source_id in SOURCE_MATERIALIZERS

    def test_toda_base_de_un_spec_derivado_esta_cableada(self) -> None:
        # Generalizacion del anterior: vale para los DerivedSeriesSpec que entren
        # despues, sin tener que anadir un test por fuente.
        for source_id, spec in SOURCE_MATERIALIZERS.items():
            if isinstance(spec, DerivedSeriesSpec):
                assert spec.base_source_id in SOURCE_MATERIALIZERS, source_id

    def test_una_base_sin_cablear_lanza_unwired(self) -> None:
        # El fallo ruidoso tambien protege el SEGUNDO nivel: si la base desapareciera
        # del registro, la derivada NO puede materializarse a medias ni caer a otra
        # cosa. Y revienta ANTES de tocar la BD (la sesion doble lo demuestra).
        huerfana = DerivedSeriesSpec(
            base_source_id="no.existe",
            transform=compute_delta_momentum,
            lookback=1,
        )
        with pytest.raises(UnwiredSourceError, match="no.existe"):
            huerfana.materialize(
                _SesionQueFalla(), "binance", "BTC-USDT", _TF.value, _OPEN, 5
            )


class TestDispatchFalloRuidoso:
    """4.2: una servible sin materializador LANZA, no recibe una serie por defecto.

    La fuente-ejemplo es ahora SINTETICA. Con MAT-08 (delta_momentum) NO queda ninguna
    fuente servible del catalogo vivo sin materializador, asi que ya no hay una real que
    prestar como ejemplo. El invariante sigue vivo y hay que seguir probandolo: es lo
    que protege de que una fuente declarada manana entre sin su materializador y reciba
    una serie por defecto. (El ejemplo fue orderflow.delta hasta T5b-1, cvd.value hasta
    T5b-2b y delta_momentum hasta esta tanda; las tres estan ya cableadas.)
    """

    def test_una_fuente_sin_materializador_lanza_unwired(self) -> None:
        # Servirle la ventana de cierres (el comportamiento viejo de _series_for, que
        # leia read_close_window para TODA fuente) le daria PRECIOS donde espera otra
        # cosa: un hecho falso con aspecto de correcto.
        source = ResolvedSource(
            source_id=_FUENTE_SINTETICA,
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
            source_id=_FUENTE_SINTETICA,
            declaration=orderflow_delta_momentum_declaration(),
            history_bars=5,
        )
        with pytest.raises(UnwiredSourceError, match=_FUENTE_SINTETICA):
            _materialize(
                _SesionQueFalla(),
                _plan(),
                _TF.value,
                _OPEN,
                source,
            )

    def test_la_fuente_sintetica_no_esta_en_el_registro(self) -> None:
        # Si alguien la cableara, los dos tests de arriba dejarian de probar nada en
        # silencio. Esto lo impide.
        assert _FUENTE_SINTETICA not in SOURCE_MATERIALIZERS
