"""Tests deterministas del score no-trade (notrade.*, F7, P08c).

Cubren: guarda de MIN_CANDLES, normalizacion min-max WINDOWED, pesos ABSOLUTOS (L2
diferido = 0, max activo 65), estados, el modulador net_edge, un REFERENTE
INDEPENDIENTE del score (transcripcion aparte, no llama al modulo) y un GOLDEN atado a
NOTRADE_FORMULA_VERSION. Todo en Decimal.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ce_v5.platform.rules.notrade import (
    MIN_CANDLES,
    NOTRADE_FLOW_DISLOCATION_SOURCE_ID,
    NOTRADE_FOOTPRINT_INEFF_SOURCE_ID,
    NOTRADE_FORMULA_VERSION,
    NOTRADE_SCORE_SOURCE_ID,
    NOTRADE_STATE_SOURCE_ID,
    NoTradeCandle,
    NoTradeOutput,
    NoTradeParams,
    NoTradeSignal,
    NoTradeState,
    declarations,
    evaluate_no_trade,
    net_edge,
    notrade_decimal_output,
    notrade_state_token,
)
from source.datasource import MemoryModel, Servibility
from source.rules.scalar import ScalarType

_EPS = Decimal("1e-9")


def _c(
    delta: str, volume: str, high: str, low: str, close: str, open_: str
) -> NoTradeCandle:
    return NoTradeCandle(
        delta=Decimal(delta),
        volume=Decimal(volume),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        open=Decimal(open_),
    )


# Ventana golden de 6 velas (valores variados; lookbacks de flip/failed_break dentro).
_GOLDEN_WINDOW = [
    _c("10", "100", "101", "99", "100.5", "100"),
    _c("-8", "120", "101.5", "99.5", "100", "100.5"),
    _c("15", "90", "102", "100", "101.8", "100.2"),
    _c("-5", "150", "101", "100.5", "100.6", "100.9"),
    _c("20", "200", "103", "100.5", "101", "102.5"),
    _c("-3", "110", "101.2", "100.8", "101.1", "100.9"),
]


def _ref_score(window: list[NoTradeCandle]) -> tuple[Decimal, Decimal, Decimal]:
    """Referente INDEPENDIENTE (transcripcion aparte del AHP): (score, fp, flow).

    Recalcula las 7 features por vela, normaliza min-max sobre la ventana y aplica los
    pesos, sin llamar a evaluate_no_trade. L2 = 0 (diferido).
    """
    eps = _EPS
    n = len(window)
    di: list[Decimal] = []
    vi: list[Decimal] = []
    df: list[Decimal] = []
    dv: list[Decimal] = []
    mn: list[Decimal] = []
    ds: list[Decimal] = []
    fb: list[Decimal] = []
    for i in range(n):
        cand = window[i]
        span = cand.high - cand.low
        if span <= 0:
            span = eps
        vol = cand.volume if cand.volume > 0 else eps
        pc = cand.close - cand.open
        ad = abs(cand.delta)
        di.append(ad / span)
        vi.append(vol / span)
        dv.append(abs(cand.delta * pc) if pc != 0 else Decimal(0))
        mn.append(abs(pc) / (ad + eps))
        ds.append(ad / (abs(pc) + eps))
        # delta_flip_rate sobre las ultimas 5
        start = max(0, i - 4)
        recent = window[start : i + 1]
        flips = sum(
            1
            for k in range(1, len(recent))
            if recent[k].delta * recent[k - 1].delta < 0
        )
        df.append(Decimal(flips) / Decimal(max(len(recent) - 1, 1)))
        # failed_break sobre las ultimas 10
        fstart = max(0, i - 9)
        fslice = window[fstart : i + 1]
        if len(fslice) < 2:
            fb.append(Decimal(0))
        else:
            prev_high = max(x.high for x in fslice[:-1])
            prev_low = min(x.low for x in fslice[:-1])
            prev = window[i - 1]
            hi = cand.high > prev_high and cand.close < prev.high
            lo = cand.low < prev_low and cand.close > prev.low
            fb.append(Decimal(1) if (hi or lo) else Decimal(0))

    def norm(col: list[Decimal]) -> Decimal:
        if len(col) < 2:
            return Decimal("0.5")
        lo = min(col)
        hi = max(col)
        r = (col[-1] - lo) / (hi - lo + eps)
        return min(max(r, Decimal(0)), Decimal(1))

    fp = (
        norm(di) * Decimal("0.35")
        + norm(vi) * Decimal("0.25")
        + norm(df) * Decimal("0.20")
        + norm(dv) * Decimal("0.20")
    ) * Decimal("40")
    flow = (
        norm(mn) * Decimal("0.40")
        + norm(ds) * Decimal("0.40")
        + norm(fb) * Decimal("0.20")
    ) * Decimal("25")
    return fp + flow, fp, flow


# --------------------------------------------------------------------------- #
# Score
# --------------------------------------------------------------------------- #


def test_menos_de_min_candles_da_none() -> None:
    assert evaluate_no_trade(_GOLDEN_WINDOW[: MIN_CANDLES - 1]) is None


def test_ventana_constante_score_cero_safe() -> None:
    # Velas identicas -> cada columna es constante -> norm 0 -> score 0 -> SAFE.
    flat = [_c("5", "100", "101", "100", "100.5", "100.2") for _ in range(6)]
    signal = evaluate_no_trade(flat)
    assert signal is not None
    assert signal.no_trade_score == Decimal(0)
    assert signal.state is NoTradeState.SAFE


def test_l2_diferido_contribuye_cero() -> None:
    signal = evaluate_no_trade(_GOLDEN_WINDOW)
    assert signal is not None
    assert signal.l2_instability == Decimal(0)
    # score = fp + flow (+ 0), pesos ABSOLUTOS.
    assert signal.no_trade_score == signal.footprint_ineff + signal.flow_dislocation


def test_pesos_absolutos_sin_libro_el_maximo_sigue_siendo_65() -> None:
    # Con L2 ya vivo (P08c-CONF-05), una ventana SIN libro sigue topando en 65: el
    # bloque aporta 0 y los otros dos NO se rescalan. Es el fail-safe visto desde el
    # tope, y la razon de que el golden de abajo no haya cambiado de valor.
    signal = evaluate_no_trade(_GOLDEN_WINDOW)
    assert signal is not None
    assert signal.footprint_ineff <= Decimal("40")
    assert signal.flow_dislocation <= Decimal("25")
    assert signal.l2_instability == Decimal(0)
    assert signal.no_trade_score <= Decimal("65")


def test_determinismo() -> None:
    a = evaluate_no_trade(_GOLDEN_WINDOW)
    b = evaluate_no_trade(_GOLDEN_WINDOW)
    assert a == b


def test_referente_independiente_del_score() -> None:
    signal = evaluate_no_trade(_GOLDEN_WINDOW)
    assert signal is not None
    ref_score, ref_fp, ref_flow = _ref_score(_GOLDEN_WINDOW)
    assert signal.footprint_ineff == ref_fp
    assert signal.flow_dislocation == ref_flow
    assert signal.no_trade_score == ref_score


def test_golden_atado_a_formula_version() -> None:
    # GOLDEN [NOTRADE_FORMULA_VERSION = notrade.v2]: si la formula cambia, sube la
    # version y se regenera. 8 decimales (presentacion; la fuente no redondea).
    #
    # v1 -> v2 en P08c-CONF-05 (entra el bloque L2). LOS VALORES DE ABAJO NO CAMBIARON,
    # y eso no es casualidad ni descuido: la ventana golden no lleva libro, asi que L2
    # aporta 0 y los otros dos bloques no se rescalan. Que el golden sobreviva intacto a
    # la activacion del bloque ES la prueba de la aditividad que el diseno prometio
    # cuando reservo el peso 35.
    assert NOTRADE_FORMULA_VERSION == "notrade.v2"
    signal = evaluate_no_trade(_GOLDEN_WINDOW)
    assert signal is not None
    q = Decimal("0.00000001")
    assert signal.no_trade_score.quantize(q) == Decimal("33.42156851")
    assert signal.footprint_ineff.quantize(q) == Decimal("25.18627450")
    assert signal.flow_dislocation.quantize(q) == Decimal("8.23529401")
    assert signal.state is NoTradeState.CAUTION


def test_parametro_altera_score() -> None:
    # Subir el peso del bloque FP cambia el score (parametrizacion efectiva).
    base = evaluate_no_trade(_GOLDEN_WINDOW)
    heavy = evaluate_no_trade(
        _GOLDEN_WINDOW, NoTradeParams(fp_block_weight=Decimal("80"))
    )
    assert base is not None
    assert heavy is not None
    assert heavy.footprint_ineff == base.footprint_ineff * Decimal("2")


# --------------------------------------------------------------------------- #
# Modulador net_edge
# --------------------------------------------------------------------------- #


def test_net_edge_score_cero_no_atenua() -> None:
    # no_trade_score = 0 -> exp(0) = 1 -> net_edge = orderflow_score.
    assert net_edge(Decimal("80"), Decimal("0")) == Decimal("80")


def test_net_edge_atenua_con_toxicidad() -> None:
    # A mayor no_trade_score, menor net_edge.
    high = net_edge(Decimal("80"), Decimal("10"))
    low = net_edge(Decimal("80"), Decimal("60"))
    assert low < high < Decimal("80")


def test_net_edge_golden() -> None:
    signal = evaluate_no_trade(_GOLDEN_WINDOW)
    assert signal is not None
    value = net_edge(Decimal("80"), signal.no_trade_score)
    assert value.quantize(Decimal("0.00000001")) == Decimal("41.00095112")


def test_net_edge_clamp_100() -> None:
    # orderflow enorme con toxicidad 0 -> se recorta a 100.
    assert net_edge(Decimal("1000"), Decimal("0")) == Decimal("100")


class TestProyeccionServible:
    """La cara SERVIBLE (P08c-DET-01): del NoTradeSignal a las cuatro fuentes."""

    def _signal(self) -> NoTradeSignal:
        return NoTradeSignal(
            no_trade_score=Decimal("41"),
            footprint_ineff=Decimal("25"),
            flow_dislocation=Decimal("16"),
            l2_instability=Decimal(0),
            state=NoTradeState.CAUTION,
        )

    def test_cada_salida_decimal_publica_su_campo(self) -> None:
        senal = self._signal()
        assert notrade_decimal_output(senal, NoTradeOutput.SCORE) == Decimal("41")
        assert notrade_decimal_output(senal, NoTradeOutput.FOOTPRINT_INEFF) == Decimal(
            "25"
        )
        assert notrade_decimal_output(senal, NoTradeOutput.FLOW_DISLOCATION) == Decimal(
            "16"
        )

    def test_el_state_es_el_token_del_enum(self) -> None:
        assert notrade_state_token(self._signal()) == "caution"

    def test_los_tokens_son_los_del_enum_del_nucleo(self) -> None:
        # El vocabulario NO se reinventa en la capa servible: si alguien anadiera un
        # quinto estado al enum sin tocar nada mas, esto lo sigue cubriendo.
        for estado in NoTradeState:
            senal = NoTradeSignal(
                no_trade_score=Decimal(0),
                footprint_ineff=Decimal(0),
                flow_dislocation=Decimal(0),
                l2_instability=Decimal(0),
                state=estado,
            )
            assert notrade_state_token(senal) == estado.value

    def test_pedir_state_como_decimal_falla_ruidoso(self) -> None:
        # state no es una salida DECIMAL: pedirlo asi es un error de cableado y se ve
        # como tal, no como un 0 silencioso.
        with pytest.raises(ValueError, match="notrade_state_token"):
            notrade_decimal_output(self._signal(), NoTradeOutput.STATE)

    def test_l2_instability_no_se_sirve(self) -> None:
        # Hoy vale 0 por construccion (bloque diferido): una fuente que siempre devuelve
        # 0 no informa de nada. Cuando l2.* exista entrara como fuente propia.
        campos = {salida.value for salida in NoTradeOutput}
        assert "l2_instability" not in campos


class TestDeclaracionesServibles:
    def test_son_cuatro_con_los_source_id_esperados(self) -> None:
        assert {d.source_id for d in declarations()} == {
            NOTRADE_SCORE_SOURCE_ID,
            NOTRADE_FOOTPRINT_INEFF_SOURCE_ID,
            NOTRADE_FLOW_DISLOCATION_SOURCE_ID,
            NOTRADE_STATE_SOURCE_ID,
        }

    def test_las_tres_cifras_son_decimal_y_el_estado_string(self) -> None:
        por_id = {d.source_id: d for d in declarations()}
        for source_id in (
            NOTRADE_SCORE_SOURCE_ID,
            NOTRADE_FOOTPRINT_INEFF_SOURCE_ID,
            NOTRADE_FLOW_DISLOCATION_SOURCE_ID,
        ):
            assert por_id[source_id].value_type is ScalarType.DECIMAL
        assert por_id[NOTRADE_STATE_SOURCE_ID].value_type is ScalarType.STRING

    def test_las_cuatro_son_continuous_windowed(self) -> None:
        for declaration in declarations():
            assert declaration.servibility is Servibility.CONTINUOUS
            assert declaration.memory_model is MemoryModel.WINDOWED

    def test_solo_las_bandas_y_la_k_son_params(self) -> None:
        # Los SUB-PESOS van FIJOS en paridad v4 (decision registrada: calibrar solo las
        # bandas y K reduce grados de libertad).
        for declaration in declarations():
            nombres = {p.name for p in declaration.params}
            assert nombres == {
                "state_safe_max",
                "state_caution_max",
                "state_no_trade_max",
                "net_edge_k",
            }
            assert declaration.overridable_params == ()
            assert nombres <= set(declaration.cache_key_schema)

    def test_consumes_incluye_candle_open_al_reves_que_climax(self) -> None:
        # NoTradeCandle SI tiene open y lo usa: price_change = close - open alimenta
        # divergence, move_no_delta y delta_stall.
        #
        # El LIBRO entra en P08c-CONF-05: el bloque L2 se computa sobre el frontier, asi
        # que la arista se declara porque se LEE (DAG honesto).
        # market.orderbook_snapshot es NON_SERVIBLE, asi que no se pide por dispatch --
        # el materializador lee su ventana por su cuenta --, igual que void con
        # market.footprint para el LVN.
        for declaration in declarations():
            assert set(declaration.consumes) == {
                "market.footprint",
                "candle.open",
                "candle.high",
                "candle.low",
                "market.close",
                "market.orderbook_snapshot",
            }
