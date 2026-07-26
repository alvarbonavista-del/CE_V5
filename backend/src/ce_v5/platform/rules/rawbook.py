"""DataSource observacional del SNAPSHOT DEL LIBRO L2 (market.orderbook_snapshot).

ESPEJO de market_close_declaration() (rawclose.py) para la familia orderbook de P07c.
Cierra el hueco que Central (G2) senalo: el snapshot del libro se persiste con una
idempotency/cache_key explicita (cond.1) pero NO tenia declaracion ADR-008, asi que sus
dimensiones de cache vivian solo en el codigo de persistencia.

ADITIVA (CE-14): este modulo solo ANADE una declaracion. NO se registra en el catalogo
vivo del worker de reglas (composition.py): registrarla exigiria un evaluador para una
fuente que en v5.0 NO se sirve como termino escalar, y eso seria tocar el nucleo. La
declaracion existe, es verificable por test y queda lista para el catalogo que disena
I-02 (P08b/P08c, el orderflow).

POR QUE NON_SERVIBLE: un snapshot top-K no es un escalar. El marco (ADR-008) admite
exactamente este caso -- "se calcula pero NO se combina en reglas" --; las magnitudes
escalares que SI se serviran (imbalance, spread, presion) son fuentes DERIVADAS de esta,
catalogo de I-02, no de v5.0.

POR QUE RECURSIVE: el libro L2 es CON ESTADO y ORDER-DEPENDIENTE -- el libro de T es el
de T-1 mas sus deltas --, asi que su valor NO es point-local. Declararlo POINT_LOCAL
autorizaria al motor a propagar una correccion por ventana acotada, que para el libro
daria un numero silenciosamente incorrecto. Coincide con la familia: un libro no se
corrige retroactivamente, se REINICIA desde una foto nueva y ese reinicio
(market.orderbook_resynced) es su propio hecho.
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

ORDERBOOK_SNAPSHOT_SOURCE_ID = "market.orderbook_snapshot"

# Las DIMENSIONES de la cache_key del snapshot, ENUMERADAS (no asumidas): dos capturas
# que difieran en CUALQUIERA de ellas son HECHOS DISTINTOS y no pueden compartir
# evaluacion ni pisarse. Cada una corresponde a una parte real de la clave que construye
# orderbook_snapshot_idempotency_key (contracts/source/families/orderbook.py):
#
#   exchange, symbol, market_type, data_family -> el stream_key del flujo
#                                                 (market:orderbook:ex:mkt:symbol)
#   timeframe                                   -> tf de la barra as-of
#   frontier_time_anchor                        -> el as_of (open_time de la barra)
#   depth_k, cadence_ms, formula_version        -> la CONFIG que hace el recorte
#                                                  reproducible por procedencia (cond.1)
#
# SIN schema_version: el payload del snapshot NO tiene un campo schema_version propio
# que pueda variar sin subir formula_version. La version del esquema del evento vive en
# el SOBRE (event_schema_version, fijada por el registro y gobernada por ADR-005), y
# cualquier cambio semantico del recorte sube formula_version, que SI esta en la clave.
# Anadir aqui una dimension que nada mueve seria ruido, no garantia.
ORDERBOOK_SNAPSHOT_CACHE_KEY_SCHEMA: tuple[str, ...] = (
    "exchange",
    "symbol",
    "market_type",
    "data_family",
    "depth_k",
    "cadence_ms",
    "timeframe",
    "frontier_time_anchor",
    "formula_version",
)


def orderbook_snapshot_declaration() -> DataSourceDeclaration:
    """Declaracion ADR-008 del snapshot observacional del libro L2."""
    return DataSourceDeclaration(
        source_id=ORDERBOOK_SNAPSHOT_SOURCE_ID,
        source_type=SourceType.OBSERVABLE,
        # No se combina como termino escalar en v5.0 (ver docstring del modulo).
        servibility=Servibility.NON_SERVIBLE,
        memory_model=MemoryModel.RECURSIVE,
        # Las magnitudes del libro (precio y tamano) son DECIMAL, jamas float: el libro
        # es la base del precio de ejecucion (M5).
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        # BARS: el frontier es UNO por barra cerrada. TIME: la muestra intra-ventana se
        # toma a cadencia (cadence_ms), que es historia en tiempo, no en barras.
        history_units=(HistoryUnit.BARS, HistoryUnit.TIME),
        shared_evaluation=True,
        # Publico sin tenant (scope=public_market, ADR-011): el libro de un flujo es el
        # mismo para todos los que lo miran.
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=ORDERBOOK_SNAPSHOT_CACHE_KEY_SCHEMA,
    )
