"""Tests de replay_from_series (P08c P5 T4c): propiedades con modulos reales + helpers.

Las propiedades (fase 0-5, confianza 0-100, determinismo, conteo, estado final) se
cumplen sea cual sea el detalle del FSM; el comportamiento fino ya lo cubren P3/P4 y el
test de integracion del replay (ci_local).
"""

from decimal import Decimal

import pytest

from ce_v5.platform.rules.pivotphase import PivotParams, PivotState, VpTouch
from ce_v5.platform.rules.pivotphase_confidence import default_params
from ce_v5.platform.rules.pivotphase_replay import (
    ReplaySeries,
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
