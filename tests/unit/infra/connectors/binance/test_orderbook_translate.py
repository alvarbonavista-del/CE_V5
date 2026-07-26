"""Traduccion de los mensajes de LIBRO (orderbook) de Binance. SIN RED.

Los mensajes tienen la forma EXACTA que documenta Binance: la foto por REST
/api/v3/depth (lastUpdateId + bids/asks) y los deltas del WS <symbol>@depth@100ms (U/u +
b/a). Probarlos contra el Binance real seria probar la red, no la logica, y ataria el CI
a un tercero.

SOLO SE PRUEBA LA TRADUCCION DE FORMA: la continuidad de secuencias, el hueco y el
resync son del MOTOR (probados en frio en test_orderbook_book); la conexion viva es la
Tanda III.
"""

from __future__ import annotations

from typing import Any

import pytest

from ce_v5.infra.connectors.binance.translate import (
    BinanceTranslationError,
    raw_orderbook_delta_from_binance,
    raw_orderbook_seed_from_binance,
)


def _seed(**overrides: Any) -> dict[str, Any]:
    """Una foto REST /api/v3/depth de Binance con su forma REAL."""
    msg: dict[str, Any] = {
        "lastUpdateId": 160,
        "bids": [["100.50", "2.000"], ["100.40", "1.500"]],
        "asks": [["100.60", "1.500"], ["100.70", "3.000"]],
    }
    msg.update(overrides)
    return msg


def _delta(**overrides: Any) -> dict[str, Any]:
    """Un depthUpdate del WS <symbol>@depth@100ms con su forma REAL."""
    msg: dict[str, Any] = {
        "e": "depthUpdate",
        "E": 1_784_073_600_123,
        "s": "BTCUSDT",
        "U": 161,
        "u": 165,
        "b": [["100.50", "0"], ["100.30", "4.000"]],
        "a": [["100.60", "2.000"]],
    }
    msg.update(overrides)
    return msg


class TestSemilla:
    def test_traduce_una_foto_completa(self) -> None:
        seed = raw_orderbook_seed_from_binance(_seed(), "BTC-USDT", "spot")

        assert seed.exchange == "binance"
        assert seed.market_type == "spot"
        assert seed.symbol == "BTC-USDT"
        # lastUpdateId es la SECUENCIA BASE contra la que el motor encadena.
        assert seed.base_sequence == 160
        assert seed.bids == (("100.50", "2.000"), ("100.40", "1.500"))
        assert seed.asks == (("100.60", "1.500"), ("100.70", "3.000"))

    def test_los_precios_y_tamanos_viajan_como_TEXTO_INTACTO(self) -> None:
        # NI float, NI redondeo, NI limpieza de ceros: en M5 esto es dinero.
        seed = raw_orderbook_seed_from_binance(_seed(), "BTC-USDT", "spot")
        assert seed.bids[0] == ("100.50", "2.000")
        assert all(isinstance(p, str) and isinstance(s, str) for p, s in seed.bids)

    @pytest.mark.parametrize("clave", ["lastUpdateId", "bids", "asks"])
    def test_falta_una_clave_y_NO_se_traduce_a_medias(self, clave: str) -> None:
        msg = _seed()
        del msg[clave]
        with pytest.raises(BinanceTranslationError):
            raw_orderbook_seed_from_binance(msg, "BTC-USDT", "spot")

    def test_lastUpdateId_no_entero_se_rechaza(self) -> None:
        with pytest.raises(BinanceTranslationError):
            raw_orderbook_seed_from_binance(_seed(lastUpdateId="x"), "BTC-USDT", "spot")


class TestDelta:
    def test_traduce_un_delta_completo(self) -> None:
        delta = raw_orderbook_delta_from_binance(_delta(), "BTC-USDT", "spot")

        assert delta.exchange == "binance"
        assert delta.symbol == "BTC-USDT"
        # U y u son las secuencias de Binance, SIN INTERPRETAR (el motor las encadena).
        assert delta.first_update_id == 161
        assert delta.final_update_id == 165
        # Los campos de secuencia de OTROS exchanges quedan a None: cada uno los suyos.
        assert delta.seq_id is None
        assert delta.prev_seq_id is None
        assert delta.update_id is None
        assert delta.seq is None
        assert delta.is_snapshot is False

    def test_un_tamano_cero_se_conserva_como_texto(self) -> None:
        # tamano 0 = borrar el nivel; el traductor lo COPIA, el motor lo interpreta.
        delta = raw_orderbook_delta_from_binance(_delta(), "BTC-USDT", "spot")
        assert delta.bids[0] == ("100.50", "0")

    @pytest.mark.parametrize("clave", ["U", "u", "b", "a"])
    def test_falta_una_clave_y_NO_se_traduce_a_medias(self, clave: str) -> None:
        msg = _delta()
        del msg[clave]
        with pytest.raises(BinanceTranslationError):
            raw_orderbook_delta_from_binance(msg, "BTC-USDT", "spot")

    def test_nivel_malformado_se_rechaza(self) -> None:
        # Un nivel que no es [precio, cantidad] es un mensaje corrupto: no se construye
        # un libro basura.
        with pytest.raises(BinanceTranslationError):
            raw_orderbook_delta_from_binance(_delta(b=[["100.5"]]), "BTC-USDT", "spot")


def test_el_modulo_NO_valida_dominio() -> None:
    # SOLO TRADUCE FORMATO. Un precio negativo pasa de largo: lo rechaza la FRONTERA DE
    # CONFIANZA (el motor del libro), que es UNA sola para los tres exchanges.
    seed = raw_orderbook_seed_from_binance(
        _seed(bids=[["-1", "2"]]), "BTC-USDT", "spot"
    )
    assert seed.bids[0] == ("-1", "2")
