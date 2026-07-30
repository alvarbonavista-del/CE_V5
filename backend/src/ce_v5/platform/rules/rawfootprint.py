"""DataSource observacional del FOOTPRINT por barra (market.footprint).

Fuente BASE del catalogo de orderflow de P08c (volume profile, absorcion, delta/CVD),
derivada del footprint que agrega P07b. Espejo de market_close_declaration()
(rawclose.py) y orderbook_snapshot_declaration() (rawbook.py). ADITIVA (CE-14): solo
anade la declaracion; NO se cablea en el catalogo vivo del worker de reglas, porque en
v5.0 esta fuente no se sirve como termino escalar (cablearla seria tocar el nucleo).

NON_SERVIBLE: el footprint de una barra es un conjunto de celdas (precio x volumen
agresor), no un escalar. El marco (ADR-008) admite este caso; las magnitudes escalares
que SI se serviran (volume profile, imbalance, absorcion) son fuentes DERIVADAS de
esta, catalogo de I-02 (P08c).

POINT_LOCAL (diferencia clave con el libro L2, que es RECURSIVE): el footprint de la
barra T sale SOLO de los trades de T, sin estado de T-1. Una correccion de T afecta
SOLO a T, asi que el motor puede propagarla por VENTANA acotada a las fuentes derivadas
que miran T (CA-P08-08). El libro es RECURSIVE porque el de T es el de T-1 mas deltas.
"""

from source.datasource import (
    DataSourceDeclaration,
    HistoryUnit,
    MemoryModel,
    Servibility,
    SharingScope,
    SourceType,
)
from source.families.market import Timeframe
from source.rules.scalar import ScalarType

MARKET_FOOTPRINT_SOURCE_ID = "market.footprint"

# Dimensiones de la cache_key, ENUMERADAS: dos footprints que difieran en cualquiera
# son HECHOS DISTINTOS y no pueden compartir evaluacion. Las cuatro viajan DENTRO del
# stream_key (market:footprint:exchange:market_type:symbol:tf).
#
# market_type ENTRA EXPLICITO (a diferencia de market.close, que PINa spot): un mismo
# exchange y symbol existen a la vez en spot y en derivados (Binance lista BTC-USDT en
# spot Y en perpetuo), y son footprints distintos. En v5.0 el enum MarketType es
# spot-only; declararla ahora es la extension ADITIVA prevista (como FOOTPRINT en
# MarketDataKind), no codigo muerto: el dia que entre un derivado la clave YA lo separa.
#
# SIN data_family (constante = footprint para esta fuente, no discrimina). SIN
# formula_version: el footprint es agregacion determinista lossless (P07b, bit a bit),
# sin parametros de recorte; un cambio semantico iria por event_schema_version
# (ADR-005), no por una dimension que nada mueve.
MARKET_FOOTPRINT_CACHE_KEY_SCHEMA: tuple[str, ...] = (
    "exchange",
    "symbol",
    "market_type",
    "timeframe",
)


def market_footprint_declaration() -> DataSourceDeclaration:
    """Declaracion ADR-008 de la fuente base de footprint por barra."""
    return DataSourceDeclaration(
        source_id=MARKET_FOOTPRINT_SOURCE_ID,
        source_type=SourceType.OBSERVABLE,
        # No se combina como termino escalar en v5.0 (ver docstring).
        servibility=Servibility.NON_SERVIBLE,
        # POINT_LOCAL: el footprint de T sale solo de los trades de T.
        memory_model=MemoryModel.POINT_LOCAL,
        # Precios y volumenes en DECIMAL, jamas float (P07b).
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        # BARS: un footprint por barra cerrada.
        history_units=(HistoryUnit.BARS,),
        shared_evaluation=True,
        # Publico sin tenant (scope=public_market, ADR-011).
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=MARKET_FOOTPRINT_CACHE_KEY_SCHEMA,
    )


def declarations() -> tuple[DataSourceDeclaration, ...]:
    """Declaraciones que este modulo publica al catalogo vivo (discovery, MAT-02)."""
    return (market_footprint_declaration(),)
