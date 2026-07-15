"""Puertos de la plataforma de market data (ADR-014, DOC_ESTRUCTURA sec.6).

El puerto pertenece a QUIEN LO CONSUME (patron hexagonal): se declara aqui, en la
capa que lo necesita, y NO en la que lo implementa. Los adapters concretos (infra/db)
lo satisfacen ESTRUCTURALMENTE -- son Protocol, no clases base: infra no importa esta
capa ni esta capa importa infra (son hermanos independientes en el contrato de
capas) -- y el cableado ocurre en entrypoints.
"""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from source.families.market import IntentSourceType, SubscriptionIntent, Timeframe


class InstrumentCatalogPort(Protocol):
    """Catalogo de instrumentos reales por exchange.

    Es el que hace posible distinguir "no conozco ese exchange" de "ese par no
    existe": sin catalogo, validar que un interes apunta a un mercado REAL seria
    humo, y se podrian abrir streams contra pares inventados.
    """

    def has_exchange(self, exchange: str) -> bool:
        """Hay al menos un instrumento de ese exchange en el catalogo."""
        ...

    def is_tradable(self, exchange: str, market_type: str, symbol: str) -> bool:
        """El instrumento existe y esta ACTIVO (no delistado)."""
        ...

    def exists(self, exchange: str, market_type: str, symbol: str) -> bool:
        """El instrumento existe en el catalogo, activo o no."""
        ...

    def native_symbol(self, exchange: str, market_type: str, symbol: str) -> str | None:
        """Como llama ESE exchange al simbolo canonico (BTC-USDT -> BTCUSDT)."""
        ...


class IntentStorePort(Protocol):
    """Persistencia de los intereses. La FUENTE DE VERDAD de la demanda (ADR-014)."""

    def count_for_subject(self, tenant_id: UUID, user_id: UUID) -> int:
        """Cuantos intereses tiene ya ese sujeto (para el tope tecnico)."""
        ...

    def insert(self, intent: SubscriptionIntent) -> None:
        """Da de alta el interes."""
        ...

    def delete(
        self,
        tenant_id: UUID,
        user_id: UUID,
        source_type: IntentSourceType,
        source_ref: str,
        market_stream_key: str,
    ) -> int:
        """Da de baja el interes de ESE origen sobre ESE flujo. Devuelve cuantos."""
        ...

    def list_for_subject(
        self, tenant_id: UUID, user_id: UUID
    ) -> Sequence[SubscriptionIntent]:
        """Los intereses del sujeto."""
        ...


class SupportedTimeframesPort(Protocol):
    """Timeframes que soporta CADA exchange.

    NO es un dato de la base: es una CAPACIDAD DEL ADAPTADOR del exchange, que la
    declara en su manifest (ADR-008). Cada exchange soporta intervalos distintos,
    y suponerlos iguales seria justo el error que Central advirtio al prohibir
    copiar el barrido de un exchange a otro.
    """

    def timeframes_for(self, exchange: str) -> frozenset[Timeframe]:
        """Los timeframes que ESE exchange sirve de verdad."""
        ...
