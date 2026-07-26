"""Traduccion de los mensajes de LIBRO (orderbook) de Bybit v5. SIN RED.

Los mensajes tienen la forma EXACTA del topic orderbook.{depth}.{symbol}: el 'data' de
un type=snapshot (semilla) y de un type=delta, con u (updateId), seq y niveles [precio,
tamano]. Un u == 1 marca un RESET (Bybit reinicia la secuencia y reenvia una foto).

SOLO se prueba la traduccion de forma: la continuidad por u, el reset y el hueco son del
MOTOR; la conexion viva, de la Tanda III.
"""

from __future__ import annotations

from typing import Any

import pytest

from ce_v5.infra.connectors.bybit.translate import (
    BybitTranslationError,
    raw_orderbook_delta_from_bybit,
    raw_orderbook_seed_from_bybit,
)


def _snapshot(**overrides: Any) -> dict[str, Any]:
    """El 'data' de un mensaje type=snapshot del topic orderbook.* de Bybit."""
    data: dict[str, Any] = {
        "s": "BTCUSDT",
        "b": [["100.50", "2"], ["100.40", "1"]],
        "a": [["100.60", "1.5"], ["100.70", "3"]],
        "u": 50,
        "seq": 7000,
    }
    data.update(overrides)
    return data


def _delta(**overrides: Any) -> dict[str, Any]:
    """El 'data' de un mensaje type=delta del topic orderbook.* de Bybit."""
    data: dict[str, Any] = {
        "s": "BTCUSDT",
        "b": [["100.50", "0"]],
        "a": [["100.60", "3"]],
        "u": 51,
        "seq": 7001,
    }
    data.update(overrides)
    return data


class TestSemilla:
    def test_traduce_una_foto_completa(self) -> None:
        seed = raw_orderbook_seed_from_bybit(_snapshot(), "BTC-USDT", "spot")

        assert seed.exchange == "bybit"
        assert seed.symbol == "BTC-USDT"
        # base_sequence = u (updateId): el motor encadena los deltas por u, NO por seq.
        assert seed.base_sequence == 50
        assert seed.base_sequence != _snapshot()["seq"]
        assert seed.bids == (("100.50", "2"), ("100.40", "1"))
        assert seed.asks == (("100.60", "1.5"), ("100.70", "3"))

    @pytest.mark.parametrize("clave", ["b", "a", "u"])
    def test_falta_una_clave_y_NO_se_traduce_a_medias(self, clave: str) -> None:
        data = _snapshot()
        del data[clave]
        with pytest.raises(BybitTranslationError):
            raw_orderbook_seed_from_bybit(data, "BTC-USDT", "spot")

    def test_u_no_entero_se_rechaza(self) -> None:
        with pytest.raises(BybitTranslationError):
            raw_orderbook_seed_from_bybit(_snapshot(u="x"), "BTC-USDT", "spot")


class TestDelta:
    def test_traduce_un_delta_completo(self) -> None:
        delta = raw_orderbook_delta_from_bybit(_delta(), "BTC-USDT", "spot")

        assert delta.exchange == "bybit"
        # u SIN INTERPRETAR (el motor encadena por u); seq se conserva sin usar.
        assert delta.update_id == 51
        assert delta.seq == 7001
        # Los de otros exchanges, a None.
        assert delta.first_update_id is None
        assert delta.seq_id is None
        # u != 1: no es un reset.
        assert delta.is_snapshot is False

    def test_u_igual_a_1_marca_is_snapshot(self) -> None:
        # Bybit reinicia u a 1 y reenvia una foto cuando su servicio se reinicia: u == 1
        # es un RESET, que el motor reconstruye desde la foto.
        delta = raw_orderbook_delta_from_bybit(_delta(u=1), "BTC-USDT", "spot")
        assert delta.update_id == 1
        assert delta.is_snapshot is True

    def test_el_llamador_puede_forzar_is_snapshot(self) -> None:
        # Un type=snapshot reenviado a mitad de flujo se pasa como delta con
        # is_snapshot=True aunque su u no sea 1.
        delta = raw_orderbook_delta_from_bybit(
            _delta(u=99), "BTC-USDT", "spot", is_snapshot=True
        )
        assert delta.is_snapshot is True

    def test_un_tamano_cero_se_conserva(self) -> None:
        delta = raw_orderbook_delta_from_bybit(_delta(), "BTC-USDT", "spot")
        assert delta.bids[0] == ("100.50", "0")

    @pytest.mark.parametrize("clave", ["b", "a", "u", "seq"])
    def test_falta_una_clave_y_NO_se_traduce_a_medias(self, clave: str) -> None:
        data = _delta()
        del data[clave]
        with pytest.raises(BybitTranslationError):
            raw_orderbook_delta_from_bybit(data, "BTC-USDT", "spot")

    def test_nivel_malformado_se_rechaza(self) -> None:
        with pytest.raises(BybitTranslationError):
            raw_orderbook_delta_from_bybit(_delta(b=[["100.5"]]), "BTC-USDT", "spot")


def test_el_modulo_NO_valida_dominio() -> None:
    seed = raw_orderbook_seed_from_bybit(_snapshot(b=[["-1", "2"]]), "BTC-USDT", "spot")
    assert seed.bids[0] == ("-1", "2")
