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
from ce_v5.entrypoints.worker_rules.divergence_materializer import (
    DivergenceRecursiveSpec,
)
from ce_v5.entrypoints.worker_rules.fib_materializer import FibRecursiveSpec
from ce_v5.entrypoints.worker_rules.materializers import (
    CVD_RESET_POLICY_V5,
    PROFILE_WINDOW_BARS,
    SOURCE_MATERIALIZERS,
    SWING_WINDOW_BARS,
    CvdIntegratorSpec,
    DerivedSeriesSpec,
    EmaRecursiveSpec,
    FootprintPointLocalSpec,
    FootprintWindowedSpec,
    MacdRecursiveSpec,
    RsiRecursiveSpec,
    SwingWindowedSpec,
    UnwiredSourceError,
    _cvd_session_step,
    _cvd_step,
)
from ce_v5.platform.rules.compiler import ExecutionPlan, ResolvedSource
from ce_v5.platform.rules.cvd import CVD_SOURCE_ID, ResetPolicy
from ce_v5.platform.rules.indicators.candle import (
    CANDLE_BODY_PCT_SOURCE_ID,
    CANDLE_HIGH_SOURCE_ID,
    CANDLE_LOW_SOURCE_ID,
    CANDLE_LOWER_SHADOW_PCT_SOURCE_ID,
    CANDLE_OPEN_SOURCE_ID,
    CANDLE_UPPER_SHADOW_PCT_SOURCE_ID,
)
from ce_v5.platform.rules.indicators.divergence import (
    DIVERGENCE_HIDDEN_BEAR_SOURCE_ID,
    DIVERGENCE_HIDDEN_BULL_SOURCE_ID,
    DIVERGENCE_KIND_SOURCE_ID,
    DIVERGENCE_REGULAR_BEAR_SOURCE_ID,
    DIVERGENCE_REGULAR_BULL_SOURCE_ID,
    DivergenceOutput,
)
from ce_v5.platform.rules.indicators.ema import (
    EMA_PERIOD_DEFAULT,
    EMA_SOURCE_ID,
)
from ce_v5.platform.rules.indicators.fib import (
    FIB_DIRECTION_SOURCE_ID,
    FIB_LEVEL_PCT_SOURCE_ID,
    FIB_NEAREST_LEVEL_SOURCE_ID,
    FibOutput,
)
from ce_v5.platform.rules.indicators.macd import (
    MACD_FAST_DEFAULT,
    MACD_HISTOGRAM_SOURCE_ID,
    MACD_LINE_SOURCE_ID,
    MACD_SIGNAL_DEFAULT,
    MACD_SIGNAL_SOURCE_ID,
    MACD_SLOW_DEFAULT,
    MacdOutput,
    macd,
    macd_seed,
    macd_step,
    select_output,
)
from ce_v5.platform.rules.indicators.rsi import (
    RSI_PERIOD_DEFAULT,
    RSI_SOURCE_ID,
)
from ce_v5.platform.rules.indicators.swing import (
    SWING_HIGH_SOURCE_ID,
    SWING_LOW_SOURCE_ID,
    SWING_STRENGTH_DEFAULT,
    PivotKind,
)
from ce_v5.platform.rules.indicators.volume import VOLUME_RATIO_VS_AVG_SOURCE_ID
from ce_v5.platform.rules.indicators.vwap import (
    VWAP_DISTANCE_PCT_SOURCE_ID,
    VWAP_VALUE_SOURCE_ID,
)
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
            CANDLE_BODY_PCT_SOURCE_ID,
            CANDLE_UPPER_SHADOW_PCT_SOURCE_ID,
            CANDLE_LOWER_SHADOW_PCT_SOURCE_ID,
            CANDLE_OPEN_SOURCE_ID,
            CANDLE_HIGH_SOURCE_ID,
            CANDLE_LOW_SOURCE_ID,
            VOLUME_RATIO_VS_AVG_SOURCE_ID,
            VWAP_VALUE_SOURCE_ID,
            VWAP_DISTANCE_PCT_SOURCE_ID,
            SWING_HIGH_SOURCE_ID,
            SWING_LOW_SOURCE_ID,
            EMA_SOURCE_ID,
            RSI_SOURCE_ID,
            MACD_LINE_SOURCE_ID,
            MACD_SIGNAL_SOURCE_ID,
            MACD_HISTOGRAM_SOURCE_ID,
            FIB_NEAREST_LEVEL_SOURCE_ID,
            FIB_LEVEL_PCT_SOURCE_ID,
            FIB_DIRECTION_SOURCE_ID,
            DIVERGENCE_KIND_SOURCE_ID,
            DIVERGENCE_REGULAR_BULL_SOURCE_ID,
            DIVERGENCE_REGULAR_BEAR_SOURCE_ID,
            DIVERGENCE_HIDDEN_BULL_SOURCE_ID,
            DIVERGENCE_HIDDEN_BEAR_SOURCE_ID,
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


def _cierres(*vals: int) -> list[Decimal]:
    return [Decimal(str(v)) for v in vals]


class TestSwingWindowedRegistrada:
    """5: swing.high/swing.low cableadas como WINDOWED sobre cierres (P08b-SWING-01).

    Sin BD: el binding, el transform puro (pivote confirmado o fallback max/min) y
    with_params. La lectura real via read_close_window vive en el test de integracion.
    """

    def test_esta_cableada_con_un_spec_windowed_high(self) -> None:
        spec = SOURCE_MATERIALIZERS[SWING_HIGH_SOURCE_ID]
        assert isinstance(spec, SwingWindowedSpec)
        assert spec.kind is PivotKind.HIGH
        assert spec.window_bars == SWING_WINDOW_BARS == 100
        assert spec.strength == SWING_STRENGTH_DEFAULT == 2

    def test_esta_cableada_con_un_spec_windowed_low(self) -> None:
        spec = SOURCE_MATERIALIZERS[SWING_LOW_SOURCE_ID]
        assert isinstance(spec, SwingWindowedSpec)
        assert spec.kind is PivotKind.LOW
        assert spec.window_bars == SWING_WINDOW_BARS == 100
        assert spec.strength == SWING_STRENGTH_DEFAULT == 2

    def test_el_transform_con_pivote_es_el_value_del_ultimo_pivote(self) -> None:
        high = SwingWindowedSpec(kind=PivotKind.HIGH, strength=2)
        assert high._transform(_cierres(1, 2, 3, 2, 1)) == Decimal(3)
        low = SwingWindowedSpec(kind=PivotKind.LOW, strength=2)
        assert low._transform(_cierres(3, 2, 1, 2, 3)) == Decimal(1)

    def test_el_transform_sin_pivote_cae_al_fallback_max_min(self) -> None:
        # Serie estrictamente creciente: symmetric_pivots no confirma ningun pivote.
        ventana = _cierres(1, 2, 3, 4, 5)
        high = SwingWindowedSpec(kind=PivotKind.HIGH, strength=2)
        assert high._transform(ventana) == Decimal(5)
        low = SwingWindowedSpec(kind=PivotKind.LOW, strength=2)
        assert low._transform(ventana) == Decimal(1)

    def test_el_transform_con_pivote_no_devuelve_el_extremo_de_la_ventana(self) -> None:
        # "Muerde": el pivote confirmado NO coincide con el max/min de la ventana, asi
        # que si el transform devolviera el extremo por error este test lo atrapa.
        high = SwingWindowedSpec(kind=PivotKind.HIGH, strength=1)
        ventana_high = _cierres(1, 3, 1, 4)
        assert high._transform(ventana_high) == Decimal(3)
        assert max(ventana_high) == Decimal(4)

        low = SwingWindowedSpec(kind=PivotKind.LOW, strength=1)
        ventana_low = _cierres(4, 2, 4, 1)
        assert low._transform(ventana_low) == Decimal(2)
        assert min(ventana_low) == Decimal(1)

    def test_with_params_liga_el_strength_efectivo_sin_mutar_el_registro(self) -> None:
        spec = SOURCE_MATERIALIZERS[SWING_HIGH_SOURCE_ID]
        assert isinstance(spec, SwingWindowedSpec)
        ligada = spec.with_params(
            {"strength": ScalarValue(scalar_type=ScalarType.INTEGER, integer_value=3)}
        )
        assert isinstance(ligada, SwingWindowedSpec)
        assert ligada.strength == 3
        assert spec.strength == SWING_STRENGTH_DEFAULT

    def test_with_params_sin_el_param_devuelve_el_mismo_spec(self) -> None:
        spec = SOURCE_MATERIALIZERS[SWING_LOW_SOURCE_ID]
        assert isinstance(spec, SwingWindowedSpec)
        assert spec.with_params({}) is spec

    def test_with_params_rechaza_un_strength_fuera_de_dominio(self) -> None:
        # Como CvdIntegratorSpec.with_params: ultima linea de defensa para quien llame
        # with_params directamente (fuera de un plan compilado). El compilador ya exige
        # value_type INTEGER; aqui se valida el DOMINIO (strength >= 1).
        spec = SwingWindowedSpec(kind=PivotKind.HIGH)
        with pytest.raises(UnwiredSourceError, match="fuerza simetrica"):
            spec.with_params(
                {
                    "strength": ScalarValue(
                        scalar_type=ScalarType.INTEGER, integer_value=0
                    )
                }
            )


def _period(valor: int) -> dict[str, ScalarValue]:
    return {"period": ScalarValue(scalar_type=ScalarType.INTEGER, integer_value=valor)}


class TestEmaRecursivaRegistrada:
    """6: ema.value cableada como RECURSIVE con replay desde snapshot (P08b-LOTE3-01).

    Aqui solo el BINDING y la propagacion de period: el replay real (bootstrap desde el
    origen, ancla y el GATE bit-exacto de ADR-007) exige BD y vive en
    tests/integration/test_ema_materializer.py, porque el snapshot que lo siembra es una
    fila de ema_snapshot (0023).
    """

    def test_esta_cableada_con_un_spec_recursivo(self) -> None:
        spec = SOURCE_MATERIALIZERS[EMA_SOURCE_ID]
        assert isinstance(spec, EmaRecursiveSpec)

    def test_el_period_por_defecto_es_veinte(self) -> None:
        # El REGISTRO guarda la instancia con el default DECLARADO (ParamSpec de
        # ema_declaration): es compartida por todas las reglas y no se muta. Una regla
        # que pida otro period recibe una COPIA ligada (with_params).
        spec = SOURCE_MATERIALIZERS[EMA_SOURCE_ID]
        assert isinstance(spec, EmaRecursiveSpec)
        assert spec.period == EMA_PERIOD_DEFAULT == 20

    def test_with_params_liga_el_period_efectivo_sin_mutar_el_registro(self) -> None:
        # MAT-05 Q2: el param efectivo produce una copia con otro period, y la del
        # registro sigue en 20 (si se mutara, una regla contaminaria a las demas). Y no
        # es cosmetico: period entra en la PK de ema_snapshot, asi que ligarlo mal
        # cruzaria
        # el ancla de ema(9) con la serie de ema(20).
        spec = SOURCE_MATERIALIZERS[EMA_SOURCE_ID]
        assert isinstance(spec, EmaRecursiveSpec)
        ligada = spec.with_params(_period(9))
        assert isinstance(ligada, EmaRecursiveSpec)
        assert ligada.period == 9
        assert spec.period == EMA_PERIOD_DEFAULT

    def test_with_params_sin_el_param_devuelve_el_mismo_spec(self) -> None:
        # ADITIVIDAD D7: sin override, nada cambia.
        spec = SOURCE_MATERIALIZERS[EMA_SOURCE_ID]
        assert isinstance(spec, EmaRecursiveSpec)
        assert spec.with_params({}) is spec

    def test_with_params_rechaza_un_period_fuera_de_dominio(self) -> None:
        # Mismo dominio que el CHECK de la 0023 (period >= 1): un period < 1 seria una
        # identidad fantasma de snapshot. Falla RUIDOSO, no cae al default en silencio.
        spec = SOURCE_MATERIALIZERS[EMA_SOURCE_ID]
        assert isinstance(spec, EmaRecursiveSpec)
        with pytest.raises(UnwiredSourceError, match="periodo de EMA valido"):
            spec.with_params(_period(0))
        with pytest.raises(UnwiredSourceError, match="periodo de EMA valido"):
            spec.with_params(_period(-3))


class TestRsiRecursivaRegistrada:
    """7: rsi.value cableada como RECURSIVE Wilder con replay desde snapshot
    (P08b-LOTE3-01).

    Aqui solo el BINDING y la propagacion de period: el replay real (bootstrap con
    rsi_seed, warm-up, ancla y el GATE bit-exacto de ADR-007) exige BD y vive en
    tests/integration/test_rsi_materializer.py, porque el estado que lo siembra es una
    fila de rsi_snapshot (0025).
    """

    def test_esta_cableada_con_un_spec_recursivo(self) -> None:
        spec = SOURCE_MATERIALIZERS[RSI_SOURCE_ID]
        assert isinstance(spec, RsiRecursiveSpec)

    def test_el_period_por_defecto_es_catorce(self) -> None:
        # El REGISTRO guarda la instancia con el default DECLARADO (ParamSpec de
        # rsi_value_declaration): el 14 de Wilder. Es compartida por todas las reglas y
        # no se muta; una regla que pida otro period recibe una COPIA ligada.
        spec = SOURCE_MATERIALIZERS[RSI_SOURCE_ID]
        assert isinstance(spec, RsiRecursiveSpec)
        assert spec.period == RSI_PERIOD_DEFAULT == 14

    def test_with_params_liga_el_period_efectivo_sin_mutar_el_registro(self) -> None:
        # MAT-05 Q2: el param efectivo produce una copia con otro period, y la del
        # registro sigue en 14. Y no es cosmetico: period entra en la PK de
        # rsi_snapshot, asi que ligarlo mal cruzaria el ancla de rsi(7) con rsi(14).
        spec = SOURCE_MATERIALIZERS[RSI_SOURCE_ID]
        assert isinstance(spec, RsiRecursiveSpec)
        ligada = spec.with_params(_period(7))
        assert isinstance(ligada, RsiRecursiveSpec)
        assert ligada.period == 7
        assert spec.period == RSI_PERIOD_DEFAULT

    def test_with_params_sin_el_param_devuelve_el_mismo_spec(self) -> None:
        # ADITIVIDAD D7: sin override, nada cambia.
        spec = SOURCE_MATERIALIZERS[RSI_SOURCE_ID]
        assert isinstance(spec, RsiRecursiveSpec)
        assert spec.with_params({}) is spec

    def test_with_params_rechaza_un_period_fuera_de_dominio(self) -> None:
        # Mismo dominio que el CHECK de la 0025 (period >= 1). Falla RUIDOSO, no cae al
        # default en silencio.
        spec = SOURCE_MATERIALIZERS[RSI_SOURCE_ID]
        assert isinstance(spec, RsiRecursiveSpec)
        with pytest.raises(UnwiredSourceError, match="periodo de RSI valido"):
            spec.with_params(_period(0))
        with pytest.raises(UnwiredSourceError, match="periodo de RSI valido"):
            spec.with_params(_period(-3))


def _entero(nombre: str, valor: int) -> dict[str, ScalarValue]:
    return {nombre: ScalarValue(scalar_type=ScalarType.INTEGER, integer_value=valor)}


def _macd_spec(source_id: str) -> MacdRecursiveSpec:
    spec = SOURCE_MATERIALIZERS[source_id]
    assert isinstance(spec, MacdRecursiveSpec)
    return spec


class TestMacdRecursivaRegistrada:
    """8: las TRES fuentes macd.* cableadas como RECURSIVE con replay desde snapshot
    (P08b-LOTE3-01).

    Comparten estado, snapshot y paso de calculo; lo unico que las distingue es que
    proyeccion publica cada una (`output`). El replay real (bootstrap, ancla y el GATE
    bit-exacto de ADR-007) exige BD y vive en el test de integracion del MACD.
    """

    def test_las_tres_estan_cableadas_con_su_propia_salida(self) -> None:
        # Si dos entradas del registro compartieran output, dos fuentes distintas
        # servirian la MISMA serie y nadie se enteraria: el catalogo diria tres cosas y
        # el motor solo sabria dos.
        assert _macd_spec(MACD_LINE_SOURCE_ID).output is MacdOutput.LINE
        assert _macd_spec(MACD_SIGNAL_SOURCE_ID).output is MacdOutput.SIGNAL
        assert _macd_spec(MACD_HISTOGRAM_SOURCE_ID).output is MacdOutput.HISTOGRAM
        salidas = {
            _macd_spec(sid).output
            for sid in (
                MACD_LINE_SOURCE_ID,
                MACD_SIGNAL_SOURCE_ID,
                MACD_HISTOGRAM_SOURCE_ID,
            )
        }
        assert len(salidas) == 3

    @pytest.mark.parametrize(
        "source_id",
        [MACD_LINE_SOURCE_ID, MACD_SIGNAL_SOURCE_ID, MACD_HISTOGRAM_SOURCE_ID],
    )
    def test_los_defaults_son_doce_veintiseis_nueve(self, source_id: str) -> None:
        spec = _macd_spec(source_id)
        assert (spec.fast, spec.slow, spec.signal) == (
            MACD_FAST_DEFAULT,
            MACD_SLOW_DEFAULT,
            MACD_SIGNAL_DEFAULT,
        )
        assert (spec.fast, spec.slow, spec.signal) == (12, 26, 9)

    def test_with_params_liga_solo_lo_que_llega_sin_mutar_el_registro(self) -> None:
        # ADITIVIDAD: un override de fast NO toca slow ni signal. Y la instancia del
        # registro sigue intacta (si se mutara, una regla contaminaria a las demas).
        spec = _macd_spec(MACD_LINE_SOURCE_ID)
        ligada = spec.with_params(_entero("fast", 5))
        assert isinstance(ligada, MacdRecursiveSpec)
        assert (ligada.fast, ligada.slow, ligada.signal) == (5, MACD_SLOW_DEFAULT, 9)
        assert ligada.output is spec.output
        assert (spec.fast, spec.slow, spec.signal) == (12, 26, 9)

    def test_with_params_liga_los_tres_a_la_vez(self) -> None:
        ligada = _macd_spec(MACD_SIGNAL_SOURCE_ID).with_params(
            {
                **_entero("fast", 5),
                **_entero("slow", 35),
                **_entero("signal", 5),
            }
        )
        assert isinstance(ligada, MacdRecursiveSpec)
        assert (ligada.fast, ligada.slow, ligada.signal) == (5, 35, 5)

    @pytest.mark.parametrize(
        "source_id",
        [MACD_LINE_SOURCE_ID, MACD_SIGNAL_SOURCE_ID, MACD_HISTOGRAM_SOURCE_ID],
    )
    def test_with_params_sin_params_no_cambia_nada(self, source_id: str) -> None:
        # ADITIVIDAD D7: sin override, la copia es equivalente a la del registro.
        spec = _macd_spec(source_id)
        assert spec.with_params({}) == spec

    @pytest.mark.parametrize("nombre", ["fast", "slow", "signal"])
    def test_with_params_rechaza_un_periodo_fuera_de_dominio(self, nombre: str) -> None:
        # Mismo dominio que los CHECK de la 0026 (>= 1), en los TRES params: ninguno
        # puede colarse. Falla RUIDOSO, no cae al default en silencio.
        spec = _macd_spec(MACD_LINE_SOURCE_ID)
        with pytest.raises(UnwiredSourceError, match="periodo de MACD valido"):
            spec.with_params(_entero(nombre, 0))
        with pytest.raises(UnwiredSourceError, match=nombre):
            spec.with_params(_entero(nombre, -2))

    def test_las_tres_salidas_reconstruyen_las_tres_series_de_macd(self) -> None:
        # El binding COMPLETO en frio: recorrer el mismo estado con macd_seed y
        # macd_step y proyectar por el output de cada spec da EXACTAMENTE las tres
        # series de macd(). Si un spec publicara la proyeccion equivocada, aqui se ve.
        closes = [Decimal(100) + Decimal(i % 7) - Decimal(i % 3) for i in range(60)]
        esperado = macd(closes)
        state = macd_seed(closes[0])
        series: dict[MacdOutput, list[Decimal]] = {salida: [] for salida in MacdOutput}
        for salida in MacdOutput:
            series[salida].append(select_output(state, salida))
        for close in closes[1:]:
            state = macd_step(state[0], state[1], state[2], close)
            for salida in MacdOutput:
                series[salida].append(select_output(state, salida))
        assert tuple(series[_macd_spec(MACD_LINE_SOURCE_ID).output]) == esperado.macd
        assert (
            tuple(series[_macd_spec(MACD_SIGNAL_SOURCE_ID).output]) == esperado.signal
        )
        assert (
            tuple(series[_macd_spec(MACD_HISTOGRAM_SOURCE_ID).output])
            == esperado.histogram
        )


def _fib_spec(source_id: str) -> FibRecursiveSpec:
    spec = SOURCE_MATERIALIZERS[source_id]
    assert isinstance(spec, FibRecursiveSpec)
    return spec


class TestFibRecursivaRegistrada:
    """9: fib.nearest_level/fib.level_pct cableadas como RECURSIVE (P08b-FIB-01).

    Comparten el rango con histeresis, el snapshot y el calculo; lo unico que las
    distingue es que proyeccion publican. El replay real (bootstrap, ancla, alineacion
    con swing y el GATE bit-exacto de ADR-007) exige BD y vive en el test de integracion
    del grid fib.
    """

    def test_las_dos_estan_cableadas_con_su_propia_salida(self) -> None:
        # Si las dos entradas compartieran output, el catalogo ofreceria dos fuentes y
        # el motor solo sabria servir una.
        assert _fib_spec(FIB_NEAREST_LEVEL_SOURCE_ID).output is FibOutput.NEAREST_LEVEL
        assert _fib_spec(FIB_LEVEL_PCT_SOURCE_ID).output is FibOutput.LEVEL_PCT

    @pytest.mark.parametrize(
        "source_id", [FIB_NEAREST_LEVEL_SOURCE_ID, FIB_LEVEL_PCT_SOURCE_ID]
    )
    def test_el_strength_por_defecto_es_el_de_swing(self, source_id: str) -> None:
        # No es un default propio: se HEREDA de swing.*, de donde salen los pivotes.
        assert _fib_spec(source_id).strength == SWING_STRENGTH_DEFAULT == 2

    def test_with_params_liga_el_strength_sin_mutar_el_registro(self) -> None:
        # El strength no solo parametriza el grid: viaja a las series de swing que lo
        # alimentan. Ligar mal serviria un rango decantado de OTROS pivotes.
        spec = _fib_spec(FIB_NEAREST_LEVEL_SOURCE_ID)
        ligada = spec.with_params(_entero("strength", 3))
        assert isinstance(ligada, FibRecursiveSpec)
        assert ligada.strength == 3
        assert ligada.output is spec.output
        assert spec.strength == SWING_STRENGTH_DEFAULT

    @pytest.mark.parametrize(
        "source_id", [FIB_NEAREST_LEVEL_SOURCE_ID, FIB_LEVEL_PCT_SOURCE_ID]
    )
    def test_with_params_sin_el_param_devuelve_el_mismo_spec(
        self, source_id: str
    ) -> None:
        # ADITIVIDAD D7: sin override, nada cambia.
        assert _fib_spec(source_id).with_params({}) is _fib_spec(source_id)

    def test_with_params_rechaza_un_strength_fuera_de_dominio(self) -> None:
        # Mismo dominio que el CHECK de la 0027 y que swing (>= 1). Falla RUIDOSO.
        spec = _fib_spec(FIB_LEVEL_PCT_SOURCE_ID)
        with pytest.raises(UnwiredSourceError, match="fuerza simetrica"):
            spec.with_params(_entero("strength", 0))
        with pytest.raises(UnwiredSourceError, match="fuerza simetrica"):
            spec.with_params(_entero("strength", -2))

    def test_las_fuentes_que_consume_estan_cableadas(self) -> None:
        # CONDICION MAT-08 para el consumidor mas compuesto del catalogo: fib se apoya
        # en swing.high y swing.low POR SOURCE_ID contra este registro. Si alguna
        # desapareciera, el grid no podria materializarse y hay que verlo en frio.
        for base in (SWING_HIGH_SOURCE_ID, SWING_LOW_SOURCE_ID):
            assert base in SOURCE_MATERIALIZERS


def _divergence_spec(source_id: str) -> DivergenceRecursiveSpec:
    spec = SOURCE_MATERIALIZERS[source_id]
    assert isinstance(spec, DivergenceRecursiveSpec)
    return spec


_DIVERGENCE_SALIDAS = (
    (DIVERGENCE_KIND_SOURCE_ID, DivergenceOutput.KIND),
    (DIVERGENCE_REGULAR_BULL_SOURCE_ID, DivergenceOutput.REGULAR_BULL),
    (DIVERGENCE_REGULAR_BEAR_SOURCE_ID, DivergenceOutput.REGULAR_BEAR),
    (DIVERGENCE_HIDDEN_BULL_SOURCE_ID, DivergenceOutput.HIDDEN_BULL),
    (DIVERGENCE_HIDDEN_BEAR_SOURCE_ID, DivergenceOutput.HIDDEN_BEAR),
)


class TestDivergenceRecursivaRegistrada:
    """10: las CINCO divergence.* cableadas como RECURSIVE (P08b-D1-05, LOTE 5).

    Comparten la cadena de pivotes, el snapshot (0028) y el calculo; lo unico que las
    distingue es que proyeccion publican. El replay real (bootstrap, ancla, proyeccion
    densa y el GATE bit-exacto de ADR-007) exige BD y vive en el test de integracion.
    """

    @pytest.mark.parametrize(("source_id", "output"), _DIVERGENCE_SALIDAS)
    def test_cada_una_esta_cableada_con_su_propia_salida(
        self, source_id: str, output: DivergenceOutput
    ) -> None:
        # Si dos entradas compartieran output, el catalogo ofreceria dos fuentes y el
        # motor solo sabria servir una -- y las otras tres mentirian en silencio.
        assert _divergence_spec(source_id).output is output

    def test_las_cinco_salidas_son_distintas(self) -> None:
        salidas = {_divergence_spec(sid).output for sid, _ in _DIVERGENCE_SALIDAS}
        assert len(salidas) == 5

    @pytest.mark.parametrize("source_id", [sid for sid, _ in _DIVERGENCE_SALIDAS])
    def test_los_defaults_son_los_heredados(self, source_id: str) -> None:
        # Ninguno de los dos es un default propio: strength se HEREDA de swing (la
        # fuerza simetrica del pivote) y rsi_period de rsi.value (el 14 de Wilder).
        spec = _divergence_spec(source_id)
        assert spec.strength == SWING_STRENGTH_DEFAULT == 2
        assert spec.rsi_period == RSI_PERIOD_DEFAULT == 14

    def test_with_params_liga_los_dos_sin_mutar_el_registro(self) -> None:
        # rsi_period no solo parametriza el fold: VIAJA a la serie de rsi.value que lo
        # alimenta. Ligar mal leeria el RSI de otro periodo en los mismos pivotes.
        spec = _divergence_spec(DIVERGENCE_KIND_SOURCE_ID)
        ligada = spec.with_params(_entero("strength", 3) | _entero("rsi_period", 21))
        assert isinstance(ligada, DivergenceRecursiveSpec)
        assert (ligada.strength, ligada.rsi_period) == (3, 21)
        assert ligada.output is spec.output
        assert (spec.strength, spec.rsi_period) == (SWING_STRENGTH_DEFAULT, 14)

    def test_with_params_solo_cambia_el_que_llega(self) -> None:
        # ADITIVIDAD D7: lo que la regla no pide, no cambia.
        spec = _divergence_spec(DIVERGENCE_REGULAR_BEAR_SOURCE_ID)
        ligada = spec.with_params(_entero("rsi_period", 7))
        assert isinstance(ligada, DivergenceRecursiveSpec)
        assert ligada.rsi_period == 7
        assert ligada.strength == SWING_STRENGTH_DEFAULT

    @pytest.mark.parametrize("source_id", [sid for sid, _ in _DIVERGENCE_SALIDAS])
    def test_with_params_sin_params_devuelve_un_spec_equivalente(
        self, source_id: str
    ) -> None:
        assert _divergence_spec(source_id).with_params({}) == _divergence_spec(
            source_id
        )

    @pytest.mark.parametrize("nombre", ["strength", "rsi_period"])
    def test_with_params_rechaza_un_valor_fuera_de_dominio(self, nombre: str) -> None:
        # Mismo dominio que los CHECK de la 0028 (>= 1). Falla RUIDOSO en vez de
        # materializar con el default en silencio.
        spec = _divergence_spec(DIVERGENCE_HIDDEN_BULL_SOURCE_ID)
        with pytest.raises(UnwiredSourceError, match="no se materializa"):
            spec.with_params(_entero(nombre, 0))
        with pytest.raises(UnwiredSourceError, match="no se materializa"):
            spec.with_params(_entero(nombre, -3))

    def test_la_fuente_que_consume_esta_cableada(self) -> None:
        # CONDICION MAT-08: divergence pide rsi.value POR SOURCE_ID contra este
        # registro en vez de recalcular Wilder. Si desapareciera, la divergencia no
        # podria materializarse y hay que verlo en frio, no en produccion.
        assert RSI_SOURCE_ID in SOURCE_MATERIALIZERS
