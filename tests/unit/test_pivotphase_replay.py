"""Tests de replay_from_series (P08c P5 T4c): propiedades con modulos reales + helpers.

Las propiedades (fase 0-5, confianza 0-100, determinismo, conteo, estado final) se
cumplen sea cual sea el detalle del FSM; el comportamiento fino ya lo cubren P3/P4 y el
test de integracion del replay (ci_local).
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from ce_v5.platform.rules.pivotphase import (
    BEARISH,
    BULLISH,
    Phase,
    PivotParams,
    PivotState,
    VpTouch,
)
from ce_v5.platform.rules.pivotphase_confidence import default_params
from ce_v5.platform.rules.pivotphase_replay import (
    ReplayResult,
    ReplaySeries,
    _absorption_zone,
    _nearest_vp_touch,
    _project_confidence,
    replay_from_series,
)


def _series(n: int) -> ReplaySeries:
    return ReplaySeries(
        price=tuple(Decimal(100 + i) for i in range(n)),
        delta=tuple(Decimal(i % 7 + 1) for i in range(n)),
        delta_momentum=tuple(Decimal(0) for _ in range(n)),
        price_range=tuple(Decimal(2) for _ in range(n)),
        vp_poc=tuple(Decimal(100 + i) for i in range(n)),
        vp_vah=tuple(Decimal(200) for _ in range(n)),
        vp_val=tuple(Decimal(50) for _ in range(n)),
        vp_hvn=tuple(Decimal(100 + i) for i in range(n)),
        vp_lvn=tuple(Decimal(50) for _ in range(n)),
        absorption_bid=tuple(Decimal(0) for _ in range(n)),
        absorption_ask=tuple(Decimal(0) for _ in range(n)),
        climax_top=tuple(Decimal(0) for _ in range(n)),
        climax_bottom=tuple(Decimal(0) for _ in range(n)),
        void_bull=tuple(Decimal(0) for _ in range(n)),
        void_bear=tuple(Decimal(0) for _ in range(n)),
        notrade_score=tuple(Decimal(0) for _ in range(n)),
        cvd=tuple(Decimal(0) for _ in range(n)),
    )


def test_nearest_vp_touch_picks_closest() -> None:
    touch = _nearest_vp_touch(Decimal(100), Decimal(101), Decimal(105), Decimal(90))
    assert touch == VpTouch(level_type="poc", level_price=Decimal(101))
    low = _nearest_vp_touch(Decimal(100), Decimal(130), Decimal(140), Decimal(99))
    assert low == VpTouch(level_type="val", level_price=Decimal(99))


def test_project_confidence_idle_and_none_give_zero() -> None:
    assert _project_confidence(0, Decimal(50)) == Decimal(0)  # IDLE
    assert _project_confidence(2, None) == Decimal(0)  # NOT_EVALUABLE
    assert _project_confidence(2, Decimal("73.40")) == Decimal("73.40")


def test_emits_bars_after_lookback() -> None:
    result = replay_from_series(
        _series(10),
        PivotState(),
        PivotParams(),
        default_params(),
        norm_window=3,
        lookback=4,
    )
    assert len(result.outcomes) == 6


def test_phase_confidence_in_range_and_final_state() -> None:
    result = replay_from_series(
        _series(20),
        PivotState(),
        PivotParams(),
        default_params(),
        norm_window=5,
        lookback=5,
    )
    assert result.outcomes
    for outcome in result.outcomes:
        assert 0 <= outcome.phase <= 5
        assert Decimal(0) <= outcome.confidence <= Decimal(100)
    assert isinstance(result.final_state, PivotState)
    assert 0 <= result.final_state.phase <= 5


def test_deterministic() -> None:
    def run() -> object:
        return replay_from_series(
            _series(15),
            PivotState(),
            PivotParams(),
            default_params(),
            norm_window=4,
            lookback=3,
        )

    assert run() == run()


def test_series_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="misma longitud"):
        ReplaySeries(
            price=(Decimal(1),),
            delta=(Decimal(1), Decimal(2)),
            delta_momentum=(Decimal(0),),
            price_range=(Decimal(1),),
            vp_poc=(Decimal(1),),
            vp_vah=(Decimal(1),),
            vp_val=(Decimal(1),),
            vp_hvn=(Decimal(1),),
            vp_lvn=(Decimal(1),),
            absorption_bid=(Decimal(1),),
            absorption_ask=(Decimal(1),),
            climax_top=(Decimal(1),),
            climax_bottom=(Decimal(1),),
            void_bull=(Decimal(1),),
            void_bear=(Decimal(1),),
            notrade_score=(Decimal(1),),
            cvd=(Decimal(1),),
        )


def _series_absorcion_en(n: int, lado: str) -> ReplaySeries:
    """Como _series pero con la fuerza de absorcion CRECIENTE en `lado` y plana en el
    otro.

    La fuerza tiene que VARIAR barra a barra: F1 se normaliza por PERCENTIL, y el
    percentil de un valor constante dentro de su propia distribucion constante es
    siempre 0,5 -- da igual que valga 0 o 0,9. Con fuerza plana las dos orientaciones
    darian el MISMO numero y el test no probaria nada (se comprobo: pasaba en falso).
    """
    creciente = tuple(Decimal(i) / Decimal(n) for i in range(n))
    plana = tuple(Decimal(0) for _ in range(n))
    return ReplaySeries(
        price=tuple(Decimal(100 + i) for i in range(n)),
        delta=tuple(Decimal(i % 7 + 1) for i in range(n)),
        delta_momentum=tuple(Decimal(0) for _ in range(n)),
        price_range=tuple(Decimal(2) for _ in range(n)),
        vp_poc=tuple(Decimal(100 + i) for i in range(n)),
        vp_vah=tuple(Decimal(200) for _ in range(n)),
        vp_val=tuple(Decimal(50) for _ in range(n)),
        vp_hvn=tuple(Decimal(100 + i) for i in range(n)),
        vp_lvn=tuple(Decimal(50) for _ in range(n)),
        absorption_bid=creciente if lado == "bid" else plana,
        absorption_ask=creciente if lado == "ask" else plana,
        climax_top=plana,
        climax_bottom=plana,
        void_bull=plana,
        void_bear=plana,
        notrade_score=plana,
        cvd=plana,
    )


class TestF1EnElReplay:
    """F1 vivo (P08c-CONF-01): la absorcion entra en la confianza, orientada por
    lado."""

    def test_la_serie_de_absorcion_es_obligatoria(self) -> None:
        # ReplaySeries valida longitudes: si alguien anadiera las dos series con otro
        # tamano, el replay emparejaria la absorcion de una barra con el delta de otra.
        with pytest.raises(ValueError, match="misma longitud"):
            ReplaySeries(
                price=(Decimal(1),),
                delta=(Decimal(1),),
                delta_momentum=(Decimal(0),),
                price_range=(Decimal(1),),
                vp_poc=(Decimal(1),),
                vp_vah=(Decimal(1),),
                vp_val=(Decimal(1),),
                vp_hvn=(Decimal(1),),
                vp_lvn=(Decimal(1),),
                absorption_bid=(Decimal(1), Decimal(1)),
                absorption_ask=(Decimal(1),),
                climax_top=(Decimal(1),),
                climax_bottom=(Decimal(1),),
                void_bull=(Decimal(1),),
                void_bear=(Decimal(1),),
                notrade_score=(Decimal(1),),
                cvd=(Decimal(1),),
            )

    def test_el_replay_sigue_siendo_determinista_con_absorcion(self) -> None:
        series = _series_absorcion_en(40, "ask")
        params = PivotParams()
        conf = default_params()
        una = replay_from_series(series, PivotState(), params, conf, 10, 10)
        otra = replay_from_series(series, PivotState(), params, conf, 10, 10)
        assert una == otra

    def test_la_absorcion_del_lado_que_toca_cambia_la_confianza(self) -> None:
        # ORIENTACION (la parte que de verdad hay que clavar): con impulso alcista se
        # espera un TECHO, que lo confirma la absorcion de COMPRADORES (ask). Dos series
        # identicas salvo por QUE LADO lleva la fuerza tienen que dar confianzas
        # distintas; si el extractor leyera el lado contrario, saldrian iguales.
        params = PivotParams()
        conf = default_params()
        solo_bid = replay_from_series(
            _series_absorcion_en(40, "bid"), PivotState(), params, conf, 10, 10
        )
        solo_ask = replay_from_series(
            _series_absorcion_en(40, "ask"), PivotState(), params, conf, 10, 10
        )
        assert [o.confidence for o in solo_bid.outcomes] != [
            o.confidence for o in solo_ask.outcomes
        ]


def _series_climax_top_creciente(n: int) -> ReplaySeries:
    """Como _series pero con climax_top CRECIENTE (0 a casi 1) y el resto de F7 en 0.

    Igual que en TestF1EnElReplay: F7 se normaliza por PERCENTIL, y el percentil de un
    valor CONSTANTE dentro de su propia distribucion constante es siempre 0,5 -- se
    comprobo (fallaba en falso). Con una serie CRECIENTE, la ultima barra es siempre el
    MAXIMO de su propia ventana trailing -> percentil 1.0 -> maxima toxicidad relativa.
    """
    creciente = tuple(Decimal(i) / Decimal(n) for i in range(n))
    plana = tuple(Decimal(0) for _ in range(n))
    return ReplaySeries(
        price=tuple(Decimal(100 + i) for i in range(n)),
        delta=tuple(Decimal(i % 7 + 1) for i in range(n)),
        delta_momentum=tuple(Decimal(0) for _ in range(n)),
        price_range=tuple(Decimal(2) for _ in range(n)),
        vp_poc=tuple(Decimal(100 + i) for i in range(n)),
        vp_vah=tuple(Decimal(200) for _ in range(n)),
        vp_val=tuple(Decimal(50) for _ in range(n)),
        vp_hvn=tuple(Decimal(100 + i) for i in range(n)),
        vp_lvn=tuple(Decimal(50) for _ in range(n)),
        absorption_bid=tuple(Decimal(0) for _ in range(n)),
        absorption_ask=tuple(Decimal(0) for _ in range(n)),
        climax_top=creciente,
        climax_bottom=plana,
        void_bull=plana,
        void_bear=plana,
        notrade_score=plana,
        cvd=plana,
    )


def _series_plana(n: int) -> ReplaySeries:
    """Como _series: las cinco series de F7 en 0 constante (sin toxicidad nunca)."""
    return _series(n)


class TestF7EnElReplay:
    """F7 vivo (P08c-CONF-01 paso 3b): max(climax, void, notrade/100) penaliza."""

    def test_la_serie_de_toxicidad_es_obligatoria(self) -> None:
        with pytest.raises(ValueError, match="misma longitud"):
            ReplaySeries(
                price=(Decimal(1),),
                delta=(Decimal(1),),
                delta_momentum=(Decimal(0),),
                price_range=(Decimal(1),),
                vp_poc=(Decimal(1),),
                vp_vah=(Decimal(1),),
                vp_val=(Decimal(1),),
                vp_hvn=(Decimal(1),),
                vp_lvn=(Decimal(1),),
                absorption_bid=(Decimal(1),),
                absorption_ask=(Decimal(1),),
                climax_top=(Decimal(1), Decimal(1)),
                climax_bottom=(Decimal(1),),
                void_bull=(Decimal(1),),
                void_bear=(Decimal(1),),
                notrade_score=(Decimal(1),),
                cvd=(Decimal(1),),
            )

    def test_el_replay_sigue_siendo_determinista_con_toxicidad(self) -> None:
        series = _series_climax_top_creciente(40)
        params = PivotParams()
        conf = default_params()
        una = replay_from_series(series, PivotState(), params, conf, 10, 10)
        otra = replay_from_series(series, PivotState(), params, conf, 10, 10)
        assert una == otra

    def test_mas_toxicidad_da_menos_confianza_barra_a_barra(self) -> None:
        # climax_top NO entra en BarSignals (F7 solo afecta a la confianza, no a la
        # FSM), asi que las fases salen IDENTICAS entre la serie plana y la creciente,
        # y la comparacion barra a barra es limpia: solo cambia F7.
        params = PivotParams()
        conf = default_params()
        limpia = replay_from_series(
            _series_plana(40), PivotState(), params, conf, 10, 10
        )
        toxica = replay_from_series(
            _series_climax_top_creciente(40), PivotState(), params, conf, 10, 10
        )
        assert [o.phase for o in limpia.outcomes] == [o.phase for o in toxica.outcomes]
        distintas = [
            (a.confidence, b.confidence)
            for a, b in zip(limpia.outcomes, toxica.outcomes, strict=True)
            if a.confidence != b.confidence
        ]
        assert distintas  # F7 mueve al menos una barra
        assert all(limpia_c >= toxica_c for limpia_c, toxica_c in distintas)

    def test_void_y_notrade_tambien_alimentan_el_max(self) -> None:
        # void_bull/void_bear/notrade_score son los otros insumos del max(): una serie
        # con SOLO notrade_score creciente (climax y void en 0) tiene que penalizar
        # igual que climax_top creciente -- prueba que el max() los lee a los cinco, no
        # solo al primero.
        creciente = tuple(Decimal(i) / Decimal(40) for i in range(40))
        plana = tuple(Decimal(0) for _ in range(40))
        solo_notrade = ReplaySeries(
            price=tuple(Decimal(100 + i) for i in range(40)),
            delta=tuple(Decimal(i % 7 + 1) for i in range(40)),
            delta_momentum=plana,
            price_range=tuple(Decimal(2) for _ in range(40)),
            vp_poc=tuple(Decimal(100 + i) for i in range(40)),
            vp_vah=tuple(Decimal(200) for _ in range(40)),
            vp_val=tuple(Decimal(50) for _ in range(40)),
            vp_hvn=tuple(Decimal(100 + i) for i in range(40)),
            vp_lvn=tuple(Decimal(50) for _ in range(40)),
            absorption_bid=plana,
            absorption_ask=plana,
            climax_top=plana,
            climax_bottom=plana,
            void_bull=plana,
            void_bear=plana,
            # notrade.score en escala 0-100: creciente*100 recorre la misma proporcion
            # relativa que climax_top (0..1) tras dividir entre 100 en _toxicity_raw.
            notrade_score=tuple(v * Decimal(100) for v in creciente),
            cvd=plana,
        )
        params = PivotParams()
        conf = default_params()
        limpia = replay_from_series(
            _series_plana(40), PivotState(), params, conf, 10, 10
        )
        toxica = replay_from_series(solo_notrade, PivotState(), params, conf, 10, 10)
        distintas = [
            (a.confidence, b.confidence)
            for a, b in zip(limpia.outcomes, toxica.outcomes, strict=True)
            if a.confidence != b.confidence
        ]
        assert distintas
        assert all(limpia_c >= toxica_c for limpia_c, toxica_c in distintas)


def _series_con_divergencia(n: int, *, cvd_acompana: bool) -> ReplaySeries:
    """Serie con precio en dientes de sierra ascendente y CVD que ACOMPANA o DIVERGE.

    cvd_acompana=True  -> el CVD sube con el precio: NO hay divergencia.
    cvd_acompana=False -> el CVD baja mientras el precio sube: SI hay divergencia
    bajista (el flujo acumulado no confirma la subida).

    Solo cambia la serie de CVD entre los dos casos, asi que las fases de la FSM salen
    IDENTICAS (cvd no entra en BarSignals) y la comparacion aisla F3.
    """
    # ONDA de ciclos de 5 barras con el pico en el CENTRO y maximos crecientes. La forma
    # importa: una sierra con tendencia monotona NO confirma ningun pivote (symmetric_
    # pivots exige `strength` barras estrictamente menores a CADA lado, y en una subida
    # sostenida la barra siguiente siempre supera al candidato). Se comprobo: con sierra
    # el test pasaba en falso porque no habia ni una divergencia que comparar.
    precio: list[Decimal] = []
    for ciclo in range((n // 5) + 1):
        pico = Decimal(100 + ciclo * 10)
        precio += [pico - 10, pico - 5, pico, pico - 5, pico - 10]
    precio = precio[:n]
    signo = Decimal(1) if cvd_acompana else Decimal(-1)
    cvd = tuple(signo * p for p in precio)
    plana = tuple(Decimal(0) for _ in range(n))
    return ReplaySeries(
        price=tuple(precio),
        delta=tuple(Decimal(i % 7 + 1) for i in range(n)),
        delta_momentum=plana,
        price_range=tuple(Decimal(2) for _ in range(n)),
        vp_poc=tuple(precio),
        vp_vah=tuple(Decimal(500) for _ in range(n)),
        vp_val=tuple(Decimal(50) for _ in range(n)),
        vp_hvn=tuple(precio),
        vp_lvn=tuple(Decimal(50) for _ in range(n)),
        absorption_bid=plana,
        absorption_ask=plana,
        climax_top=plana,
        climax_bottom=plana,
        void_bull=plana,
        void_bear=plana,
        notrade_score=plana,
        cvd=cvd,
    )


class TestF3EnElReplay:
    """F3 vivo (P08c-CONF-03): la divergencia precio-vs-CVD entra en la confianza."""

    def test_la_serie_de_cvd_es_obligatoria(self) -> None:
        with pytest.raises(ValueError, match="misma longitud"):
            ReplaySeries(
                price=(Decimal(1),),
                delta=(Decimal(1),),
                delta_momentum=(Decimal(0),),
                price_range=(Decimal(1),),
                vp_poc=(Decimal(1),),
                vp_vah=(Decimal(1),),
                vp_val=(Decimal(1),),
                vp_hvn=(Decimal(1),),
                vp_lvn=(Decimal(1),),
                absorption_bid=(Decimal(1),),
                absorption_ask=(Decimal(1),),
                climax_top=(Decimal(1),),
                climax_bottom=(Decimal(1),),
                void_bull=(Decimal(1),),
                void_bear=(Decimal(1),),
                notrade_score=(Decimal(1),),
                cvd=(Decimal(1), Decimal(1)),
            )

    def test_el_replay_es_determinista_bit_a_bit_con_cvd(self) -> None:
        # ADR-007: F3 se precomputa de una pasada, asi que hay que confirmar que dos
        # replays de la misma serie coinciden digito a digito, no solo en valor.
        series = _series_con_divergencia(40, cvd_acompana=False)
        params = PivotParams()
        conf = default_params()
        una = replay_from_series(series, PivotState(), params, conf, 10, 10)
        otra = replay_from_series(series, PivotState(), params, conf, 10, 10)
        assert una == otra
        assert [str(o.confidence) for o in una.outcomes] == [
            str(o.confidence) for o in otra.outcomes
        ]

    def test_la_divergencia_cambia_la_confianza_sin_tocar_las_fases(self) -> None:
        # cvd NO entra en BarSignals: las fases tienen que salir IDENTICAS entre las dos
        # series, y lo unico que puede mover la confianza es F3.
        params = PivotParams()
        conf = default_params()
        acompana = replay_from_series(
            _series_con_divergencia(40, cvd_acompana=True),
            PivotState(),
            params,
            conf,
            10,
            10,
        )
        diverge = replay_from_series(
            _series_con_divergencia(40, cvd_acompana=False),
            PivotState(),
            params,
            conf,
            10,
            10,
        )
        assert [o.phase for o in acompana.outcomes] == [
            o.phase for o in diverge.outcomes
        ]
        assert [o.confidence for o in acompana.outcomes] != [
            o.confidence for o in diverge.outcomes
        ]


# --- Gate de fase 3 VIVO en el replay (P08c-CONF-04) ----------------------------------

_NIVEL = Decimal(100)


def _series_hacia_fase_3(
    *, lado: str = "ask", rompe_la_zona: bool = False, n: int = 24
) -> ReplaySeries:
    """Serie que lleva la FSM por el camino COMPLETO hasta fase 3 y mas alla.

    La forma esta calibrada contra los gates reales, no inventada: 12 barras de cebado
    con delta 1 (para que la ventana de normalizacion tenga distribucion), 2 de impulso
    con delta 500 (percentil alto -> impulse_score >= 70 -> fase 1), una barra que clava
    el precio EN el nivel VP (vp_poc == price, asi que el toque esta a distancia 0 ->
    fase 2) y despues absorcion sostenida en ese nivel (-> fase 3).

    lado="ask" da zona BEARISH, que es la que SOPORTA un impulso alcista; lado="bid" da
    la contraria y la FSM debe quedarse clavada en fase 2.
    rompe_la_zona=True hace que el precio atraviese la zona al alza (100.4 > 100.3).
    """
    precio: list[Decimal] = []
    delta: list[Decimal] = []
    bid: list[Decimal] = []
    ask: list[Decimal] = []
    for i in range(n):
        fuerza = Decimal(0)
        if i < 12:
            precio.append(Decimal(90) + Decimal(i) / Decimal(10))
            delta.append(Decimal(1))
        elif i < 14:
            precio.append(Decimal("99") + Decimal(i - 12) / Decimal(2))
            delta.append(Decimal(500))
        elif i == 14:
            precio.append(_NIVEL)
            delta.append(Decimal(10))
        else:
            precio.append(Decimal("100.4") if (rompe_la_zona and i >= 17) else _NIVEL)
            delta.append(Decimal(10))
            fuerza = Decimal("0.8")
        bid.append(fuerza if lado == "bid" else Decimal(0))
        ask.append(fuerza if lado == "ask" else Decimal(0))
    plana = tuple(Decimal(0) for _ in range(n))
    return ReplaySeries(
        price=tuple(precio),
        delta=tuple(delta),
        delta_momentum=plana,
        price_range=tuple(Decimal(2) for _ in range(n)),
        vp_poc=tuple(precio),
        vp_vah=tuple(Decimal(500) for _ in range(n)),
        vp_val=tuple(Decimal(50) for _ in range(n)),
        vp_hvn=tuple(precio),
        vp_lvn=tuple(Decimal(50) for _ in range(n)),
        absorption_bid=tuple(bid),
        absorption_ask=tuple(ask),
        climax_top=plana,
        climax_bottom=plana,
        void_bull=plana,
        void_bear=plana,
        notrade_score=plana,
        cvd=plana,
    )


def _replay(series: ReplaySeries, params: PivotParams | None = None) -> ReplayResult:
    return replay_from_series(
        series, PivotState(), params or PivotParams(), default_params(), 10, 10
    )


class TestGateDeFase3EnElReplay:
    """El camino 0->3 CONFIRMA EN VIVO desde P08c-CONF-04 (antes moria en fase 2)."""

    def test_el_zone_type_invierte_el_lado_de_la_absorcion(self) -> None:
        # ASK (agresion compradora absorbida) = TECHO = zona BEARISH, y al reves. Si
        # esto se toma tal cual en vez de invertido, is_counter_zone no casa NUNCA.
        techo = _absorption_zone(_NIVEL, Decimal(0), Decimal("0.8"))
        assert techo is not None
        assert techo.zone_type == BEARISH
        assert techo.zone_price == _NIVEL
        assert techo.zone_strength == Decimal("0.8")
        suelo = _absorption_zone(_NIVEL, Decimal("0.7"), Decimal(0))
        assert suelo is not None
        assert suelo.zone_type == BULLISH
        assert suelo.zone_strength == Decimal("0.7")

    def test_sin_absorcion_de_ningun_lado_no_hay_zona(self) -> None:
        # None y no una zona de fuerza 0: "no hubo absorcion" no es una zona debil, es
        # la AUSENCIA de zona, y el gate de fase 3 tiene que quedarse quieto.
        assert _absorption_zone(_NIVEL, Decimal(0), Decimal(0)) is None

    def test_el_replay_alcanza_fase_3_por_el_camino_completo(self) -> None:
        # LA PRUEBA DE QUE 3d SIRVE PARA ALGO: antes de alimentar BarSignals.absorption
        # el replay se quedaba clavado en fase 2 pasara lo que pasara.
        resultado = _replay(_series_hacia_fase_3())
        fases = [o.phase for o in resultado.outcomes]
        assert int(Phase.ABSORPTION) in fases
        assert max(fases) >= int(Phase.ABSORPTION)
        assert resultado.final_state.phase3_zone_price == _NIVEL
        assert resultado.final_state.phase3_zone_strength == Decimal("0.8")

    def test_la_absorcion_del_lado_CONTRARIO_deja_la_fsm_en_fase_2(self) -> None:
        # MUERDE la orientacion: misma serie, misma fuerza, solo cambia el lado. Con
        # impulso alcista, una zona de SUELO no soporta el techo que se espera.
        fases = [o.phase for o in _replay(_series_hacia_fase_3(lado="bid")).outcomes]
        assert int(Phase.ABSORPTION) not in fases
        assert max(fases) == int(Phase.ENCOUNTER)

    def test_romper_la_zona_invalida_y_devuelve_la_fsm_a_idle(self) -> None:
        # PHASE3_ZONE_BREAK visto desde el replay: el precio atraviesa la zona al alza
        # (100.4 > 100 * 1.003) y la secuencia entera muere.
        fases = [
            o.phase for o in _replay(_series_hacia_fase_3(rompe_la_zona=True)).outcomes
        ]
        assert int(Phase.ABSORPTION) in fases
        assert fases[-1] == int(Phase.IDLE)

    def test_el_11o_param_gobierna_la_rotura_tambien_en_el_replay(self) -> None:
        # Sin rotura por defecto; con el umbral apretado a 0.001 el mismo precio rompe.
        series = _series_hacia_fase_3(rompe_la_zona=True)
        laxo = _replay(
            series, replace(PivotParams(), phase3_break_threshold=Decimal("0.01"))
        )
        assert laxo.final_state.phase != int(Phase.IDLE)
        estricto = _replay(
            series, replace(PivotParams(), phase3_break_threshold=Decimal("0.001"))
        )
        assert estricto.final_state.phase == int(Phase.IDLE)

    def test_sigue_siendo_determinista_bit_a_bit_con_la_zona_viva(self) -> None:
        # ADR-007: la zona entra en el estado (phase3_zone_price/strength), asi que el
        # determinismo hay que reafirmarlo con el gate abierto.
        series = _series_hacia_fase_3()
        una, otra = _replay(series), _replay(series)
        assert una == otra
        assert [str(o.confidence) for o in una.outcomes] == [
            str(o.confidence) for o in otra.outcomes
        ]
