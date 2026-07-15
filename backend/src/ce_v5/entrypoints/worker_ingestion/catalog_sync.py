"""Sincronizacion del catalogo de instrumentos (P07).

POR QUE ANTES DE ABRIR STREAMS (aviso de B6b, cumplido aqui): el connector real
resuelve el simbolo NATIVO del exchange (BTCUSDT) al CANONICO (BTC-USDT) CONSULTANDO el
catalogo. De 'BTCUSDT' no se puede deducir donde parte (BTC-USDT o BT-CUSDT): es una
consulta, no un calculo. Si el catalogo esta vacio, el connector descarta TODO mensaje
que llegue (contado como metrica, nunca en silencio) y el ingestor parece sano sin
ingerir un solo dato. Por eso el arranque sincroniza el catalogo ANTES del primer
reconcile.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ce_v5.platform.market.datasource import Instrument, MarketDataSourcePort
from source.families.market import SYMBOL_PATTERN

_MARKET_TYPE = "spot"

# El SIMBOLO CANONICO se valida contra el MISMO patron del contrato (compilado una vez).
# Asi el filtro del catalogo y lo que la base y los payloads aceptan no pueden divergir.
_SYMBOL_RE = re.compile(SYMBOL_PATTERN)


class CatalogWriterPort(Protocol):
    """Lo minimo que este modulo necesita del catalogo. Estructural: lo cumple el
    adaptador de infra (o su envoltorio) sin heredar nada.
    """

    def upsert(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        native_symbol: str,
        status: str = ...,
    ) -> None: ...

    def deactivate_missing(
        self, exchange: str, market_type: str, present_symbols: list[str]
    ) -> int: ...


@runtime_checkable
class SymbolMapSink(Protocol):
    """Capacidad OPCIONAL de un datasource: recibir la resolucion nativo -> canonico.

    El connector REAL la necesita (nace con el mapa vacio y de 'BTCUSDT' no se deduce
    donde parte: hay que consultarlo, B6b). El FAKE NO la expone: resuelve sus propios
    simbolos, asi que la deteccion estructural lo salta sin un if por tipo concreto.
    Reflejar el catalogo a sus DOS consumidores -- la base (para validar intereses y la
    demanda) y el resolutor del connector (para traducir) -- es UNA responsabilidad:
    propagar el catalogo recien traido.
    """

    def set_symbol_map(self, instruments: Sequence[Instrument]) -> None: ...


@dataclass(frozen=True, slots=True)
class CatalogSyncResult:
    """Observable: activos, delistados y saltados por no representables en esta pasada.

    ``not_representable`` cuenta instrumentos cuyo simbolo canonico no encaja en
    SYMBOL_PATTERN (p.ej. un listing no-ASCII de Binance). Un simbolo saltado en
    silencio seria un dato perdido sin rastro; por eso viaja en el resultado.
    """

    active: int
    deactivated: int
    not_representable: int


def sync_catalog(
    datasource: MarketDataSourcePort, catalog_writer: CatalogWriterPort
) -> CatalogSyncResult:
    """Trae el catalogo del exchange y lo refleja en market_instrument.

    El catalogo del exchange es ENTRADA NO CONFIABLE (ADR-006), igual que las velas: un
    instrumento cuyo simbolo CANONICO no encaja en SYMBOL_PATTERN no es un ticker que
    representemos. Se SALTA y se CUENTA (observable), NO tumba la sincronizacion de los
    otros miles de pares (fault isolation: una listing rara no deja sin catalogo a
    nadie). Se valida NUESTRO symbol canonico; el native_symbol es del exchange y puede
    ser cualquier cosa.

    Los representables se hacen upsert; los que ya no vienen del exchange se marcan
    inactivos (NO se borran: un par delistado conserva su historico, y borrarlo dejaria
    velas huerfanas).
    """
    instrumentos = datasource.list_instruments(_MARKET_TYPE)

    representables: list[Instrument] = []
    no_representables = 0
    for instrumento in instrumentos:
        if _SYMBOL_RE.fullmatch(instrumento.symbol) is None:
            # Simbolo canonico no representable (p.ej. no-ASCII): se salta y se cuenta.
            # Nada de raise: una sola listing rara no puede tumbar la sincronizacion.
            no_representables += 1
            continue
        representables.append(instrumento)

    # El connector REAL resuelve nativo -> canonico CONSULTANDO, no calculando (B6b):
    # recibe el mapa de los instrumentos REPRESENTABLES (el mapa espeja el catalogo que
    # de verdad se persiste), sin una segunda llamada de red. El FAKE no expone esta
    # capacidad (resuelve sus simbolos): la deteccion es ESTRUCTURAL, no por tipo.
    if isinstance(datasource, SymbolMapSink):
        datasource.set_symbol_map(representables)

    activos_por_exchange: dict[str, list[str]] = {}
    activos = 0
    for instrumento in representables:
        _upsert(catalog_writer, instrumento)
        if instrumento.active:
            activos += 1
            activos_por_exchange.setdefault(instrumento.exchange, []).append(
                instrumento.symbol
            )

    deactivated = 0
    for exchange, simbolos in activos_por_exchange.items():
        deactivated += catalog_writer.deactivate_missing(
            exchange, _MARKET_TYPE, simbolos
        )
    return CatalogSyncResult(
        active=activos, deactivated=deactivated, not_representable=no_representables
    )


def _upsert(catalog_writer: CatalogWriterPort, instrumento: Instrument) -> None:
    catalog_writer.upsert(
        instrumento.exchange,
        instrumento.market_type,
        instrumento.symbol,
        instrumento.native_symbol,
        "active" if instrumento.active else "inactive",
    )
