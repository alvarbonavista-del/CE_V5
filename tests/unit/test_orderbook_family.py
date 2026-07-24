"""Tests del contrato de orderbook (P07c; ADR-014, ADR-007, ADR-006, CA-06, cond.1/3).

En FRIO, sin base ni red: demuestran que el contrato defiende el borde del snapshot
top-K y del resync, que la idempotency_key lleva la CONFIG (K, cadencia, ventana,
formula_version) que hace el hecho reproducible, y que el registro (CA-06) resuelve los
dos event_type publicados a su payload.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from source.families.market import MarketType, Timeframe
from source.families.orderbook import (
    MarketOrderbookEventType,
    MarketOrderbookSnapshotKind,
    OrderbookLevel,
    OrderbookResyncedPayload,
    OrderbookSnapshotPayload,
)
from source.families.registry import (
    expected_event_schema_version,
    payload_class_for,
)

_OPEN = 1_784_073_600_000  # 2026-07-14T00:00:00Z, alineado a 1m.
_CLOSE = _OPEN + Timeframe.M1.duration_ms


def _levels(pairs: list[tuple[str, str]]) -> tuple[OrderbookLevel, ...]:
    return tuple(OrderbookLevel(price=Decimal(p), size=Decimal(s)) for p, s in pairs)


def _frontier(**overrides: object) -> OrderbookSnapshotPayload:
    base: dict[str, object] = {
        "exchange": "binance",
        "market_type": MarketType.SPOT,
        "symbol": "BTC-USDT",
        "depth_k": 25,
        "bids": _levels([("100.5", "2"), ("100.4", "1")]),
        "asks": _levels([("100.6", "1.5"), ("100.7", "3")]),
        "sequence": 987654,
        "kind": MarketOrderbookSnapshotKind.FRONTIER,
        "timeframe": Timeframe.M1,
        "open_time": _OPEN,
        "close_time": _CLOSE,
        "cadence_ms": 1000,
        "formula_version": 1,
    }
    base.update(overrides)
    return OrderbookSnapshotPayload(**base)


def _sample(**overrides: object) -> OrderbookSnapshotPayload:
    base: dict[str, object] = {
        "kind": MarketOrderbookSnapshotKind.SAMPLE,
        "sample_time": _OPEN + 30_000,
    }
    base.update(overrides)
    return _frontier(**base)


def _resync(**overrides: object) -> OrderbookResyncedPayload:
    base: dict[str, object] = {
        "exchange": "binance",
        "market_type": MarketType.SPOT,
        "symbol": "BTC-USDT",
        "from_sequence": 500,
        "to_sequence": 540,
        "reason": "gap",
        "event_time": _OPEN + 42,
    }
    base.update(overrides)
    return OrderbookResyncedPayload(**base)


class TestRegistroCA06:
    def test_los_dos_publicados_resuelven_a_su_payload(self) -> None:
        assert (
            payload_class_for(MarketOrderbookEventType.ORDERBOOK_FRONTIER.value)
            is OrderbookSnapshotPayload
        )
        assert (
            payload_class_for(MarketOrderbookEventType.ORDERBOOK_RESYNCED.value)
            is OrderbookResyncedPayload
        )

    def test_event_schema_version_de_los_dos(self) -> None:
        for event_type in MarketOrderbookEventType:
            assert expected_event_schema_version(event_type.value) == 1

    def test_el_frontier_hace_ida_y_vuelta_por_el_registro(self) -> None:
        # 5.21: el payload NO es vacio y round-trips por su clase del registro.
        payload = _frontier()
        cls = payload_class_for(MarketOrderbookEventType.ORDERBOOK_FRONTIER.value)
        vuelto = cls.model_validate_json(payload.model_dump_json())
        assert vuelto == payload

    def test_el_resync_hace_ida_y_vuelta_por_el_registro(self) -> None:
        payload = _resync()
        cls = payload_class_for(MarketOrderbookEventType.ORDERBOOK_RESYNCED.value)
        vuelto = cls.model_validate_json(payload.model_dump_json())
        assert vuelto == payload


class TestIsCompleteFailSafe:
    def test_is_complete_por_defecto_False(self) -> None:
        # Cond.3: lo que no declara su completitud cuenta como incompleto.
        assert _frontier().is_complete is False
        assert _sample().is_complete is False

    def test_is_complete_declarado_se_respeta(self) -> None:
        assert _frontier(is_complete=True).is_complete is True


class TestOrdenYProfundidad:
    def test_bids_no_descendentes_rechazados(self) -> None:
        with pytest.raises(ValidationError):
            _frontier(bids=_levels([("100.4", "1"), ("100.5", "2")]))

    def test_asks_no_ascendentes_rechazados(self) -> None:
        with pytest.raises(ValidationError):
            _frontier(asks=_levels([("100.7", "3"), ("100.6", "1.5")]))

    def test_nivel_repetido_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            _frontier(bids=_levels([("100.5", "2"), ("100.5", "1")]))

    def test_un_lado_excede_depth_k_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            _frontier(depth_k=1, bids=_levels([("100.5", "2"), ("100.4", "1")]))

    def test_snapshot_vacio_rechazado(self) -> None:
        # 5.21: un libro sin bids ni asks no es un hecho.
        with pytest.raises(ValidationError):
            _frontier(bids=(), asks=())

    def test_nivel_con_tamano_no_positivo_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            OrderbookLevel(price=Decimal("100"), size=Decimal("0"))

    def test_nivel_con_precio_no_positivo_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            OrderbookLevel(price=Decimal("0"), size=Decimal("1"))

    def test_ventana_desalineada_rechazada(self) -> None:
        with pytest.raises(ValidationError):
            _frontier(open_time=_OPEN + 1)


class TestVarianteKind:
    def test_frontier_con_sample_time_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            _frontier(sample_time=_OPEN + 10)

    def test_sample_sin_sample_time_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            _frontier(kind=MarketOrderbookSnapshotKind.SAMPLE)

    def test_sample_time_fuera_de_ventana_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            _sample(sample_time=_CLOSE + 1)

    def test_sample_valido(self) -> None:
        assert _sample().sample_time == _OPEN + 30_000


class TestIdempotencyKeyLlevaLaConfig:
    """Cond.1: la clave incluye K, cadencia, ventana y formula_version; variar
    cualquiera produce OTRO hecho. Reprocesar con la MISMA config reconstruye la
    MISMA clave.
    """

    def _key(self, payload: OrderbookSnapshotPayload) -> str:
        return payload.idempotency_key(payload.kind)

    def test_misma_config_misma_clave(self) -> None:
        assert self._key(_frontier()) == self._key(_frontier())

    def test_distinta_K_distinta_clave(self) -> None:
        assert self._key(_frontier(depth_k=25)) != self._key(_frontier(depth_k=50))

    def test_distinta_cadencia_distinta_clave(self) -> None:
        base = self._key(_frontier(cadence_ms=1000))
        assert base != self._key(_frontier(cadence_ms=500))

    def test_distinta_formula_version_distinta_clave(self) -> None:
        base = self._key(_frontier(formula_version=1))
        assert base != self._key(_frontier(formula_version=2))

    def test_distinta_ventana_distinta_clave(self) -> None:
        otra = _OPEN + Timeframe.M1.duration_ms
        base = self._key(_frontier())
        assert base != self._key(
            _frontier(open_time=otra, close_time=otra + Timeframe.M1.duration_ms)
        )

    def test_frontier_y_sample_no_colisionan(self) -> None:
        # Misma ventana y config, distinto kind: claves distintas (prefijo distinto).
        assert self._key(_frontier()) != self._key(_sample())

    def test_dos_samples_de_distinto_instante_no_colisionan(self) -> None:
        a = self._key(_sample(sample_time=_OPEN + 10_000))
        b = self._key(_sample(sample_time=_OPEN + 20_000))
        assert a != b

    def test_idempotency_key_con_kind_incoherente_lanza(self) -> None:
        # Pedir la clave de un frontier como si fuera sample (o viceversa) es un error.
        with pytest.raises(ValueError, match="pero el snapshot es"):
            _frontier().idempotency_key(MarketOrderbookSnapshotKind.SAMPLE)

    def test_la_clave_del_frontier_lleva_la_config_verbatim(self) -> None:
        clave = self._key(_frontier())
        assert clave == (
            "market.orderbook_frontier|market:orderbook:binance:spot:BTC-USDT|1m|"
            f"{_OPEN}|k25|c1000|v1"
        )


class TestResync:
    def test_stream_key_sin_timeframe(self) -> None:
        assert _resync().stream_key() == "market:orderbook:binance:spot:BTC-USDT"

    def test_mismo_hueco_misma_clave(self) -> None:
        assert _resync().idempotency_key() == _resync().idempotency_key()

    def test_distinto_hueco_distinta_clave(self) -> None:
        a = _resync(from_sequence=500, to_sequence=540).idempotency_key()
        b = _resync(from_sequence=500, to_sequence=560).idempotency_key()
        assert a != b

    def test_extremo_desconocido_se_codifica(self) -> None:
        clave = _resync(to_sequence=None).idempotency_key()
        assert clave == (
            "market.orderbook_resynced|market:orderbook:binance:spot:BTC-USDT|from500|tonone"
        )

    def test_to_menor_que_from_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            _resync(from_sequence=540, to_sequence=500)

    def test_reason_vacio_rechazado(self) -> None:
        with pytest.raises(ValidationError):
            _resync(reason="")
