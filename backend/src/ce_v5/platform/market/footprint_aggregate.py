"""Agregacion PURA del footprint de una barra (P07b 3b-1; ADR-007, I-04).

El footprint es la foto por barra: por cada NIVEL DE PRECIO EXACTO (tick nativo del
exchange), el volumen agresor comprador y vendedor y su delta. Esta funcion lo AGREGA
desde los trades ya persistidos de la ventana. SIN IO, determinista.

REPRODUCIBILIDAD BIT A BIT (cierra los NO VERIFICADO de I-04 1.1/4.4). Tres propiedades
la garantizan, y ninguna depende del orden en que lleguen los trades:
- DEDUP por trade_id: dos veces el mismo trade cuenta UNA (idempotente ante reentrega).
- SUMA CONMUTATIVA por celda: Decimal exacto; a+b = b+a. El orden de los trades del
  mismo milisegundo NO afecta al resultado, no hace falta un orden total entre ellos.
- CELDAS ORDENADAS por precio ascendente sin repetir nivel: el mismo conjunto de trades
  produce SIEMPRE la misma tupla de celdas, byte a byte.

CELDA = PRECIO EXACTO (LOCKED por Central). Sin agrupar por tick/price_step, sin cap de
celdas: agrupar o capar reintroduciria perdida de informacion. La observabilidad de
"cuantas celdas tiene una barra" la mide el motor (celdas-por-barra), no se limita aqui.

is_complete: FAIL-SAFE. False si ALGUN hueco de trades (market_trade_gap) se solapa con
la ventana [open_time, open_time + tf_ms): a esa barra le faltan trades y sus celdas no
son la verdad completa del mercado. True solo si ningun hueco la toca.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from source.families.footprint import (
    FootprintCell,
    FootprintClosedPayload,
    FootprintCorrectedPayload,
    MarketFootprintEventType,
    MarketTrade,
    footprint_idempotency_key,
)
from source.families.market import (
    AggressorSide,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    Timeframe,
)
from source.time import MaturityState

# Un hueco de trades no cubierto, tal como lo lee el store: (from, to) en event_time ms.
# Cualquiera de los extremos puede ser None (desconocido): el fail-safe lo trata como
# infinito por ese lado, asi que un hueco de extremo incierto se solapa por si acaso.
TradeGap = tuple["int | None", "int | None"]


@dataclass(frozen=True, slots=True)
class FootprintStreamIdentity:
    """Identidad del flujo de footprint (exchange/market/symbol/timeframe)."""

    exchange: str
    market_type: MarketType
    symbol: str
    timeframe: Timeframe

    def footprint_stream_key(self) -> str:
        """stream_key del flujo de FOOTPRINT (data_kind=footprint, ADR-003/014)."""
        return MarketStreamKey(
            exchange=self.exchange,
            market_type=self.market_type,
            symbol=self.symbol,
            data_kind=MarketDataKind.FOOTPRINT,
            timeframe=self.timeframe,
        ).as_stream_key()


def _gap_overlaps(
    gap_from: int | None, gap_to: int | None, window_start: int, window_end: int
) -> bool:
    """El intervalo de datos que FALTAN (gap_from, gap_to) toca [window_start, win_end)?

    gap_from es el ultimo trade que SI teniamos; gap_to el mas antiguo que el relleno
    alcanzo. Entre medias faltan trades. FAIL-SAFE: un extremo None (desconocido) se
    trata como infinito por ese lado -> si hay duda, se considera que solapa.
    """
    lower_ok = gap_to is None or gap_to > window_start
    upper_ok = gap_from is None or gap_from < window_end
    return lower_ok and upper_ok


def aggregate_footprint(
    identity: FootprintStreamIdentity,
    open_time: int,
    close_time: int,
    trades: Sequence[MarketTrade],
    gaps: Sequence[TradeGap],
    *,
    maturity_state: MaturityState,
    correction_revision: int | None = None,
) -> FootprintClosedPayload | FootprintCorrectedPayload:
    """Agrega el footprint de la barra [open_time, open_time+tf_ms). PURA, determinista.

    maturity_state es CLOSED (footprint_closed) o CORRECTION (footprint_corrected). En
    una correccion, correction_revision es la de la vela corregida (lockstep) y el
    resultado referencia por corrects_idempotency_key el footprint CERRADO de esa barra.

    trades: los de market_trade cuyo event_time cae en la ventana (los da el store).
    gaps: los huecos de market_trade_gap que se solapan con la ventana (o todos: la
    funcion reevalua el solape). is_complete = False si alguno solapa (fail-safe).
    """
    window_end = open_time + identity.timeframe.duration_ms

    # Celdas por PRECIO EXACTO, dedup por trade_id, suma conmutativa buy/sell.
    por_precio: dict[Decimal, list[Decimal]] = {}
    vistos: set[str] = set()
    for trade in trades:
        if trade.trade_id in vistos:
            continue
        vistos.add(trade.trade_id)
        acumulado = por_precio.get(trade.price)
        if acumulado is None:
            acumulado = [Decimal(0), Decimal(0)]
            por_precio[trade.price] = acumulado
        if trade.aggressor_side is AggressorSide.BUY:
            acumulado[0] += trade.qty
        else:
            acumulado[1] += trade.qty

    cells = tuple(
        FootprintCell(
            price=precio,
            buy_volume=buy,
            sell_volume=sell,
            delta=buy - sell,
        )
        for precio, (buy, sell) in sorted(por_precio.items())
    )
    bar_buy_volume = sum((cell.buy_volume for cell in cells), Decimal(0))
    bar_sell_volume = sum((cell.sell_volume for cell in cells), Decimal(0))
    is_complete = not any(
        _gap_overlaps(gap_from, gap_to, open_time, window_end)
        for gap_from, gap_to in gaps
    )

    campos_comunes = {
        "exchange": identity.exchange,
        "market_type": identity.market_type,
        "symbol": identity.symbol,
        "timeframe": identity.timeframe,
        "open_time": open_time,
        "close_time": close_time,
        "cells": cells,
        "bar_buy_volume": bar_buy_volume,
        "bar_sell_volume": bar_sell_volume,
        "bar_delta": bar_buy_volume - bar_sell_volume,
        "trade_count": len(vistos),
        "is_complete": is_complete,
    }

    if maturity_state is MaturityState.CLOSED:
        return FootprintClosedPayload(
            maturity_state=MaturityState.CLOSED,
            **campos_comunes,
        )

    if maturity_state is not MaturityState.CORRECTION:
        msg = f"aggregate_footprint solo agrega closed o correction: {maturity_state}."
        raise ValueError(msg)
    if correction_revision is None:
        msg = "una correccion de footprint exige correction_revision (>=1)."
        raise ValueError(msg)

    # La correccion REFERENCIA al footprint CERRADO de la MISMA barra (append-only,
    # ADR-007): su corrects_idempotency_key es la idempotency_key de ese closed.
    corrects = footprint_idempotency_key(
        event_type=MarketFootprintEventType.FOOTPRINT_CLOSED,
        stream_key=identity.footprint_stream_key(),
        open_time=open_time,
        maturity_state=MaturityState.CLOSED,
        correction_revision=None,
    )
    return FootprintCorrectedPayload(
        maturity_state=MaturityState.CORRECTION,
        corrects_idempotency_key=corrects,
        correction_revision=correction_revision,
        **campos_comunes,
    )
