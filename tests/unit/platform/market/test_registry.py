"""Unit tests del MarketInterestRegistry (ADR-014).

Con dobles EN MEMORIA de los tres puertos y un SimulatedClock: la logica se prueba
sin PostgreSQL y sin reloj real. El tiempo NUNCA sale de datetime.now(): sale del
Clock inyectado (ADR-007), y por eso los expires_at son deterministas.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from ce_v5.core.clock import SimulatedClock
from ce_v5.platform.market.errors import IntentRejected, IntentRejectionReason
from ce_v5.platform.market.registry import MarketInterestRegistry
from source.families.market import (
    IntentSourceType,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    StreamScope,
    SubscriptionIntent,
    Timeframe,
)

_AHORA = 1_784_073_600_000


class _CatalogoFalso:
    """Catalogo en memoria: binance con BTC-USDT activo y DOGE-USDT delistado."""

    def __init__(self) -> None:
        self._activos = {("binance", "spot", "BTC-USDT")}
        self._inactivos = {("binance", "spot", "DOGE-USDT")}

    def has_exchange(self, exchange: str) -> bool:
        return exchange == "binance"

    def exists(self, exchange: str, market_type: str, symbol: str) -> bool:
        clave = (exchange, market_type, symbol)
        return clave in self._activos or clave in self._inactivos

    def is_tradable(self, exchange: str, market_type: str, symbol: str) -> bool:
        return (exchange, market_type, symbol) in self._activos

    def native_symbol(self, exchange: str, market_type: str, symbol: str) -> str | None:
        if not self.exists(exchange, market_type, symbol):
            return None
        return symbol.replace("-", "")


class _TimeframesFalsos:
    """binance sirve 1m y 1h; NO sirve 4h (aunque 4h sea valido en el enum)."""

    def timeframes_for(self, exchange: str) -> frozenset[Timeframe]:
        if exchange != "binance":
            return frozenset()
        return frozenset({Timeframe.M1, Timeframe.H1})


class _StoreFalso:
    """Almacen en memoria de intereses."""

    def __init__(self) -> None:
        self.intents: list[SubscriptionIntent] = []

    def count_for_subject(self, tenant_id: UUID, user_id: UUID) -> int:
        return len(
            [
                i
                for i in self.intents
                if i.tenant_id == tenant_id and i.user_id == user_id
            ]
        )

    def insert(self, intent: SubscriptionIntent) -> None:
        self.intents.append(intent)

    def delete(
        self,
        tenant_id: UUID,
        user_id: UUID,
        source_type: IntentSourceType,
        source_ref: str,
        market_stream_key: str,
    ) -> int:
        antes = len(self.intents)
        self.intents = [
            i
            for i in self.intents
            if not (
                i.tenant_id == tenant_id
                and i.user_id == user_id
                and i.source_type == source_type
                and i.source_ref == source_ref
                and i.market_stream_key() == market_stream_key
            )
        ]
        return antes - len(self.intents)

    def list_for_subject(
        self, tenant_id: UUID, user_id: UUID
    ) -> Sequence[SubscriptionIntent]:
        return [
            i for i in self.intents if i.tenant_id == tenant_id and i.user_id == user_id
        ]


def _clave(
    symbol: str = "BTC-USDT",
    exchange: str = "binance",
    timeframe: Timeframe = Timeframe.M1,
) -> MarketStreamKey:
    return MarketStreamKey(
        exchange=exchange,
        market_type=MarketType.SPOT,
        symbol=symbol,
        data_kind=MarketDataKind.CANDLES,
        timeframe=timeframe,
    )


@pytest.fixture
def store() -> _StoreFalso:
    return _StoreFalso()


@pytest.fixture
def registry(store: _StoreFalso) -> MarketInterestRegistry:
    return MarketInterestRegistry(
        catalog=_CatalogoFalso(),
        store=store,
        timeframes=_TimeframesFalsos(),
        clock=SimulatedClock(start_ms=_AHORA),
    )


def _alta(
    registry: MarketInterestRegistry,
    *,
    tenant_id: UUID | None = None,
    user_id: UUID | None = None,
    stream_key: MarketStreamKey | None = None,
    source_ref: str = "widget-1",
    lease_ttl_ms: int | None = None,
) -> SubscriptionIntent:
    return registry.add(
        tenant_id=uuid4() if tenant_id is None else tenant_id,
        user_id=uuid4() if user_id is None else user_id,
        stream_scope=StreamScope.PUBLIC_MARKET,
        stream_key=_clave() if stream_key is None else stream_key,
        source_type=IntentSourceType.WIDGET,
        source_ref=source_ref,
        lease_ttl_ms=lease_ttl_ms,
    )


class TestAltaValida:
    def test_alta_valida_deriva_su_stream_key_y_usa_el_clock(
        self, registry: MarketInterestRegistry, store: _StoreFalso
    ) -> None:
        intent = _alta(registry)

        assert intent.market_stream_key() == "market:candles:binance:spot:BTC-USDT:1m"
        # El tiempo sale del Clock inyectado, JAMAS de datetime.now().
        assert intent.created_at == _AHORA
        assert intent.updated_at == _AHORA
        assert store.intents == [intent]

    def test_interes_persistente_no_caduca(
        self, registry: MarketInterestRegistry
    ) -> None:
        # Una regla o una alerta no caduca porque el usuario cierre el navegador.
        intent = _alta(registry)
        assert intent.expires_at is None

    def test_interes_efimero_caduca_segun_el_clock(
        self, registry: MarketInterestRegistry
    ) -> None:
        # Un widget SI caduca: si no, quedarian suscripciones zombis gastando una
        # conexion al exchange para nadie.
        intent = _alta(registry, lease_ttl_ms=30_000)
        assert intent.expires_at == _AHORA + 30_000

    def test_dos_sujetos_distintos_mismo_flujo_misma_clave(
        self, registry: MarketInterestRegistry
    ) -> None:
        # EL CORAZON DE ADR-014: si la clave no fuese identica, cada tenant abriria su
        # propio stream y volveria la explosion N x M que la pieza existe para evitar.
        uno = _alta(registry)
        otro = _alta(registry)
        assert uno.tenant_id != otro.tenant_id
        assert uno.market_stream_key() == otro.market_stream_key()


class TestRechazos:
    def test_exchange_desconocido(self, registry: MarketInterestRegistry) -> None:
        with pytest.raises(IntentRejected) as excinfo:
            _alta(registry, stream_key=_clave(exchange="exchange_fantasma"))
        assert excinfo.value.reason is IntentRejectionReason.UNKNOWN_EXCHANGE

    def test_par_inexistente(self, registry: MarketInterestRegistry) -> None:
        # Control de SEGURIDAD: sin catalogo se podrian fabricar claves arbitrarias y
        # abrir streams infinitos (DoS por cardinalidad).
        with pytest.raises(IntentRejected) as excinfo:
            _alta(registry, stream_key=_clave(symbol="NOEXISTE-USDT"))
        assert excinfo.value.reason is IntentRejectionReason.UNKNOWN_INSTRUMENT

    def test_instrumento_delistado(self, registry: MarketInterestRegistry) -> None:
        with pytest.raises(IntentRejected) as excinfo:
            _alta(registry, stream_key=_clave(symbol="DOGE-USDT"))
        assert excinfo.value.reason is IntentRejectionReason.INSTRUMENT_INACTIVE

    def test_timeframe_que_ese_exchange_no_soporta(
        self, registry: MarketInterestRegistry
    ) -> None:
        # 4h es un timeframe VALIDO en el enum canonico, pero este exchange no lo
        # sirve. Lo que vale es lo que soporta el exchange, no lo que existe.
        assert Timeframe.H4 in Timeframe
        with pytest.raises(IntentRejected) as excinfo:
            _alta(registry, stream_key=_clave(timeframe=Timeframe.H4))
        assert excinfo.value.reason is IntentRejectionReason.UNSUPPORTED_INTERVAL

    def test_nada_se_inserta_cuando_se_rechaza(
        self, registry: MarketInterestRegistry, store: _StoreFalso
    ) -> None:
        with pytest.raises(IntentRejected):
            _alta(registry, stream_key=_clave(symbol="NOEXISTE-USDT"))
        assert store.intents == []


class TestTopeTecnico:
    def test_superar_el_tope_por_sujeto_rechaza_y_no_inserta(
        self, store: _StoreFalso
    ) -> None:
        # Tope de SUPERVIVENCIA, no cuota comercial: sin el, un solo usuario puede
        # pedir miles de streams y tumbar la ingesta. Es un DoS gratis.
        registry = MarketInterestRegistry(
            catalog=_CatalogoFalso(),
            store=store,
            timeframes=_TimeframesFalsos(),
            clock=SimulatedClock(start_ms=_AHORA),
            max_intents_per_subject=2,
        )
        tenant_id, user_id = uuid4(), uuid4()
        _alta(registry, tenant_id=tenant_id, user_id=user_id, source_ref="w1")
        _alta(registry, tenant_id=tenant_id, user_id=user_id, source_ref="w2")

        with pytest.raises(IntentRejected) as excinfo:
            _alta(registry, tenant_id=tenant_id, user_id=user_id, source_ref="w3")
        assert excinfo.value.reason is IntentRejectionReason.SUBJECT_LIMIT_EXCEEDED
        assert store.count_for_subject(tenant_id, user_id) == 2

    def test_el_tope_es_por_sujeto_no_global(self, store: _StoreFalso) -> None:
        registry = MarketInterestRegistry(
            catalog=_CatalogoFalso(),
            store=store,
            timeframes=_TimeframesFalsos(),
            clock=SimulatedClock(start_ms=_AHORA),
            max_intents_per_subject=1,
        )
        _alta(registry, tenant_id=uuid4(), user_id=uuid4())
        # Otro sujeto NO paga el tope del primero.
        _alta(registry, tenant_id=uuid4(), user_id=uuid4())
        assert len(store.intents) == 2


class TestBajaYListado:
    def test_baja_de_un_interes_existente(
        self, registry: MarketInterestRegistry, store: _StoreFalso
    ) -> None:
        tenant_id, user_id = uuid4(), uuid4()
        _alta(registry, tenant_id=tenant_id, user_id=user_id, source_ref="w1")

        retirado = registry.remove(
            tenant_id, user_id, IntentSourceType.WIDGET, "w1", _clave()
        )
        assert retirado is True
        assert store.intents == []

    def test_baja_de_un_interes_inexistente(
        self, registry: MarketInterestRegistry
    ) -> None:
        retirado = registry.remove(
            uuid4(), uuid4(), IntentSourceType.WIDGET, "no-existe", _clave()
        )
        assert retirado is False

    def test_listado_por_sujeto(self, registry: MarketInterestRegistry) -> None:
        tenant_id, user_id = uuid4(), uuid4()
        _alta(registry, tenant_id=tenant_id, user_id=user_id, source_ref="w1")
        _alta(registry, tenant_id=uuid4(), user_id=uuid4(), source_ref="ajeno")

        intents = registry.list_for_subject(tenant_id, user_id)
        assert [i.source_ref for i in intents] == ["w1"]
