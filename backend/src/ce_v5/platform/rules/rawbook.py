"""DataSource observacional del SNAPSHOT DEL LIBRO L2 (market.orderbook_snapshot).

ESPEJO de market_close_declaration() (rawclose.py) para la familia orderbook de P07c.
Cierra el hueco que Central (G2) senalo: el snapshot del libro se persiste con una
idempotency/cache_key explicita (cond.1) pero NO tenia declaracion ADR-008, asi que sus
dimensiones de cache vivian solo en el codigo de persistencia.

AL CATALOGO VIVO DESDE P08c-CONF-05, y sigue sin necesitar evaluador. Hasta esta
pieza la declaracion existia pero no se registraba, porque nadie la consumia. Ahora
notrade.* la declara en su consumes (el bloque L2 se computa sobre el frontier), y el
catalogo VALIDA que toda arista apunte a un nodo existente: sin registrar, la validacion
rompe con MissingDependencyError. Es el mismo sitio que ocupa market.footprint, tambien
NON_SERVIBLE y tambien registrada: un nodo NON_SERVIBLE es un nodo del DAG que no se
sirve como termino, no un nodo invisible.

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
#   clock_source                                -> el RELOJ que fecho la captura
#                                                  ('system' en produccion, 'simulated'
#                                                  en backtest/tests)
#
# clock_source ES LA DECIMA DIMENSION POR DICTAMEN DE CENTRAL (G2/clock_source). Estaba
# ya en la clave que se persiste (segmento cs{...}) pero no en el schema declarado: dos
# capturas del MISMO as_of por relojes distintos son HECHOS DISTINTOS, y una fuente que
# no lo declara podria compartir evaluacion entre una captura real y una simulada.
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
    "clock_source",
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


def declarations() -> tuple[DataSourceDeclaration, ...]:
    """Declaraciones que este modulo publica al catalogo vivo (discovery).

    Espejo de rawfootprint.declarations(): el nodo BASE del que cuelgan las fuentes
    derivadas del libro. Hoy solo lo consume notrade.* (bloque L2, P08c-CONF-05).
    """
    return (orderbook_snapshot_declaration(),)
