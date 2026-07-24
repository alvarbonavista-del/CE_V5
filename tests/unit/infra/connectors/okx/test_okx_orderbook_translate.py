"""Traduccion de los mensajes de LIBRO (orderbook) de OKX. SIN RED.

Los mensajes tienen la forma EXACTA del canal 'books' de OKX v5: el data[0] de un
action=snapshot (semilla) y de un action=update (delta), con seqId/prevSeqId y niveles
de CUATRO campos [precio, tamano, liquidados, ordenes]. El checksum se IGNORA (Central).

SOLO se prueba la traduccion de forma: la continuidad (prevSeqId encadena, keepalive y
mantenimiento) es del MOTOR; la conexion viva, de la Tanda III.
"""

from __future__ import annotations

from typing import Any

import pytest

from ce_v5.infra.connectors.okx.translate import (
    OkxTranslationError,
    raw_orderbook_delta_from_okx,
    raw_orderbook_seed_from_okx,
)


def _snapshot(**overrides: Any) -> dict[str, Any]:
    """El data[0] de un mensaje action=snapshot del canal 'books' de OKX."""
    book: dict[str, Any] = {
        "asks": [["100.60", "1.5", "0", "2"], ["100.70", "3", "0", "1"]],
        "bids": [["100.50", "2", "0", "1"], ["100.40", "1", "0", "1"]],
        "ts": "1784073600123",
        "checksum": -855196043,
        "seqId": 123,
        "prevSeqId": -1,
    }
    book.update(overrides)
    return book


def _update(**overrides: Any) -> dict[str, Any]:
    """El data[0] de un mensaje action=update del canal 'books' de OKX."""
    book: dict[str, Any] = {
        "asks": [["100.60", "0", "0", "0"]],
        "bids": [["100.30", "5", "0", "1"]],
        "ts": "1784073600223",
        "checksum": 123456,
        "seqId": 124,
        "prevSeqId": 123,
    }
    book.update(overrides)
    return book


class TestSemilla:
    def test_traduce_una_foto_completa(self) -> None:
        seed = raw_orderbook_seed_from_okx(_snapshot(), "BTC-USDT", "spot")

        assert seed.exchange == "okx"
        assert seed.symbol == "BTC-USDT"
        # base_sequence = seqId; prevSeqId (-1 en snapshot) se ignora.
        assert seed.base_sequence == 123
        # De los CUATRO campos por nivel solo se quedan precio y tamano.
        assert seed.bids == (("100.50", "2"), ("100.40", "1"))
        assert seed.asks == (("100.60", "1.5"), ("100.70", "3"))

    @pytest.mark.parametrize("clave", ["bids", "asks", "seqId"])
    def test_falta_una_clave_y_NO_se_traduce_a_medias(self, clave: str) -> None:
        book = _snapshot()
        del book[clave]
        with pytest.raises(OkxTranslationError):
            raw_orderbook_seed_from_okx(book, "BTC-USDT", "spot")

    def test_seqId_no_entero_se_rechaza(self) -> None:
        with pytest.raises(OkxTranslationError):
            raw_orderbook_seed_from_okx(_snapshot(seqId="x"), "BTC-USDT", "spot")


class TestDelta:
    def test_traduce_un_delta_completo(self) -> None:
        delta = raw_orderbook_delta_from_okx(_update(), "BTC-USDT", "spot")

        assert delta.exchange == "okx"
        # seqId y prevSeqId SIN INTERPRETAR (el motor encadena por prevSeqId).
        assert delta.seq_id == 124
        assert delta.prev_seq_id == 123
        # Los de otros exchanges, a None.
        assert delta.first_update_id is None
        assert delta.final_update_id is None
        assert delta.update_id is None
        assert delta.is_snapshot is False

    def test_un_tamano_cero_se_conserva(self) -> None:
        delta = raw_orderbook_delta_from_okx(_update(), "BTC-USDT", "spot")
        assert delta.asks[0] == ("100.60", "0")

    @pytest.mark.parametrize("clave", ["bids", "asks", "seqId", "prevSeqId"])
    def test_falta_una_clave_y_NO_se_traduce_a_medias(self, clave: str) -> None:
        book = _update()
        del book[clave]
        with pytest.raises(OkxTranslationError):
            raw_orderbook_delta_from_okx(book, "BTC-USDT", "spot")

    def test_nivel_malformado_se_rechaza(self) -> None:
        with pytest.raises(OkxTranslationError):
            raw_orderbook_delta_from_okx(_update(bids=[["100.3"]]), "BTC-USDT", "spot")


def test_el_modulo_NO_valida_dominio() -> None:
    seed = raw_orderbook_seed_from_okx(
        _snapshot(bids=[["-1", "2", "0", "1"]]), "BTC-USDT", "spot"
    )
    assert seed.bids[0] == ("-1", "2")
