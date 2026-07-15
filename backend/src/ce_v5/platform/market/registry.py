"""MarketInterestRegistry (ADR-014): la demanda, agregada por SubscriptionIntents."""

from collections.abc import Sequence
from uuid import UUID, uuid4

from ce_v5.core.clock import Clock
from ce_v5.platform.market.errors import IntentRejected, IntentRejectionReason
from ce_v5.platform.market.ports import (
    InstrumentCatalogPort,
    IntentStorePort,
    SupportedTimeframesPort,
)
from source.families.market import (
    MAX_INTENTS_PER_SUBJECT,
    IntentSourceType,
    MarketStreamKey,
    StreamScope,
    SubscriptionIntent,
)


class MarketInterestRegistry:
    """MarketInterestRegistry (ADR-014): agrega la demanda por SubscriptionIntents.

    NO decide producto ni cuotas comerciales (eso es P11 + el gate). Valida que el
    interes apunte a algo REAL y aplica el tope TECNICO de supervivencia.
    """

    def __init__(
        self,
        catalog: InstrumentCatalogPort,
        store: IntentStorePort,
        timeframes: SupportedTimeframesPort,
        clock: Clock,
        max_intents_per_subject: int = MAX_INTENTS_PER_SUBJECT,
    ) -> None:
        # El Clock se INYECTA y se DECLARA (ADR-007): ni un datetime.now() disperso.
        self._catalog = catalog
        self._store = store
        self._timeframes = timeframes
        self._clock = clock
        self._max_intents = max_intents_per_subject

    def add(
        self,
        tenant_id: UUID,
        user_id: UUID,
        stream_scope: StreamScope,
        stream_key: MarketStreamKey,
        source_type: IntentSourceType,
        source_ref: str,
        priority: int = 100,
        lease_ttl_ms: int | None = None,
    ) -> SubscriptionIntent:
        """Declara un interes. Valida ANTES de insertar; si rechaza, no toca nada."""
        self._validar(stream_key)
        self._validar_tope(tenant_id, user_id)

        now = self._clock.now_ms()
        intent = SubscriptionIntent(
            intent_id=uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            stream_scope=stream_scope,
            stream_key=stream_key,
            source_type=source_type,
            source_ref=source_ref,
            priority=priority,
            # Sin TTL, el interes es PERSISTENTE (una regla no caduca porque el
            # usuario cierre el navegador). Con TTL, es EFIMERO (un widget), para
            # que no queden suscripciones zombis.
            expires_at=None if lease_ttl_ms is None else now + lease_ttl_ms,
            created_at=now,
            updated_at=now,
        )
        self._store.insert(intent)
        return intent

    def remove(
        self,
        tenant_id: UUID,
        user_id: UUID,
        source_type: IntentSourceType,
        source_ref: str,
        stream_key: MarketStreamKey,
    ) -> bool:
        """Retira el interes de ESE origen sobre ESE flujo. True si habia alguno."""
        borrados = self._store.delete(
            tenant_id,
            user_id,
            source_type,
            source_ref,
            stream_key.as_stream_key(),
        )
        return borrados > 0

    def list_for_subject(
        self, tenant_id: UUID, user_id: UUID
    ) -> Sequence[SubscriptionIntent]:
        """Los intereses vivos del sujeto."""
        return self._store.list_for_subject(tenant_id, user_id)

    def _validar(self, stream_key: MarketStreamKey) -> None:
        """El interes debe apuntar a algo REAL, y en ese orden exacto."""
        exchange = stream_key.exchange
        if not self._catalog.has_exchange(exchange):
            raise IntentRejected(
                IntentRejectionReason.UNKNOWN_EXCHANGE,
                f"El exchange '{exchange}' no esta en el catalogo: sin adaptador no "
                "hay quien traiga ese flujo.",
            )

        market_type = stream_key.market_type.value
        symbol = stream_key.symbol
        if not self._catalog.exists(exchange, market_type, symbol):
            # CONTROL DE SEGURIDAD, no comodidad: sin catalogo se podrian fabricar
            # MarketStreamKeys arbitrarios y abrir streams infinitos.
            raise IntentRejected(
                IntentRejectionReason.UNKNOWN_INSTRUMENT,
                f"El par '{symbol}' no existe en {exchange}/{market_type}.",
            )
        if not self._catalog.is_tradable(exchange, market_type, symbol):
            raise IntentRejected(
                IntentRejectionReason.INSTRUMENT_INACTIVE,
                f"El par '{symbol}' esta delistado en {exchange}: suscribirse gastaria "
                "una conexion para no recibir nunca un dato.",
            )

        # Lo que vale NO es el enum canonico, sino lo que ESE exchange sirve.
        timeframe = stream_key.timeframe
        if timeframe is not None and timeframe not in self._timeframes.timeframes_for(
            exchange
        ):
            raise IntentRejected(
                IntentRejectionReason.UNSUPPORTED_INTERVAL,
                f"El exchange '{exchange}' no sirve el intervalo '{timeframe.value}'.",
            )

    def _validar_tope(self, tenant_id: UUID, user_id: UUID) -> None:
        """Tope TECNICO de supervivencia: sin el, un solo usuario tumba la ingesta."""
        if self._store.count_for_subject(tenant_id, user_id) >= self._max_intents:
            raise IntentRejected(
                IntentRejectionReason.SUBJECT_LIMIT_EXCEEDED,
                f"El sujeto ya tiene {self._max_intents} intereses (tope TECNICO de "
                "plataforma, no cuota comercial): mas streams tumbarian la ingesta.",
            )
