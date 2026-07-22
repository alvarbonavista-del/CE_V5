"""Store de trades individuales y de sus HUECOS (P07b; ADR-014, ADR-006, regla 5.20).

Solo el rol de INGESTA puede escribir aqui (regla 5.20, migracion 0017). Si lo intentara
la API, la rechazaria PostgreSQL, no un if de este fichero.

Cumple TradeWriterPort de ce_v5.platform.market por FORMA (Protocol estructural): este
modulo NO importa platform, ni platform importa infra.

SIN OUTBOX, a diferencia de market_candles.py, y no es un olvido: los trades NO se
publican al bus (un par liquido produce miles por minuto; publicarlos de uno en uno
seria la avalancha de I-02, y nadie los consume asi). Lo que se publica es el FOOTPRINT
por barra, que si va por outbox. Sin publicacion no hay pareja persistida/publicada que
pueda divergir, que es lo unico que el patron outbox existe para impedir: meter aqui
una outbox seria ceremonia sin invariante que defender.
"""

from __future__ import annotations

from ce_v5.infra.db.ports import Database
from source.families.footprint import MarketTrade
from source.families.market import LastSeenTrade

# DEDUP HONESTO POR IDENTIDAD NATURAL. La PK (exchange, market_type, symbol, trade_id)
# es la identidad que el propio exchange le da al hecho: dos mensajes con el mismo
# trade_id son EL MISMO trade, venga uno del WebSocket y otro del bootstrap REST.
#
# ON CONFLICT DO NOTHING ... RETURNING, y no un SELECT previo: entre un SELECT y su
# INSERT cabe otra replica insertando la misma fila, y entonces el "ya lo comprobe"
# seria falso. Aqui lo decide el MOTOR en una sola sentencia atomica, y el RETURNING
# delata si la fila entro de verdad. Sin ese RETURNING no habria forma de distinguir
# "insertado" de "ya estaba" sin volver a preguntar, y el motor cuenta esa diferencia.
_INSERT_TRADE_SQL = """
INSERT INTO market_trade (
    exchange, market_type, symbol, trade_id,
    price, qty, aggressor_side, event_time, source_sequence
) VALUES (
    %s, %s, %s, %s,
    %s, %s, %s, %s, %s
)
ON CONFLICT (exchange, market_type, symbol, trade_id) DO NOTHING
RETURNING trade_id
"""

# EL PUNTO DESDE EL QUE HAY QUE RELLENAR tras una reconexion. Sale de la BASE y no de la
# memoria del proceso, y eso es lo que hace que un REINICIO con un hueco mayor que el
# techo REST tambien se detecte: un proceso que arranca de cero no recuerda nada, pero
# la tabla si.
#
# ORDEN POR (event_time, trade_id): el event_time solo no basta, porque varios trades
# comparten milisegundo y "el ultimo" tiene que ser UNO, siempre el mismo. El desempate
# por trade_id lo hace determinista.
_LAST_SEEN_SQL = """
SELECT trade_id, event_time
FROM market_trade
WHERE exchange = %s AND market_type = %s AND symbol = %s
ORDER BY event_time DESC, trade_id DESC
LIMIT 1
"""

# ON CONFLICT DO NOTHING ... RETURNING, igual que el INSERT de trades y por lo mismo: el
# RETURNING delata si la fila entro DE VERDAD, y sin el no habria forma de distinguir un
# hueco NUEVO de uno ya apuntado sin volver a preguntar.
_INSERT_GAP_SQL = """
INSERT INTO market_trade_gap (
    exchange, market_type, symbol, gap_from_event_time_ms, gap_to_event_time_ms
) VALUES (%s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
RETURNING exchange
"""


class PostgresTradeWriter:
    """Trades individuales sobre PostgreSQL, con el rol de INGESTA."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def persist(self, trade: MarketTrade) -> bool:
        """Guarda el trade. Devuelve False si YA ESTABA (dedup por su clave unica).

        El trade llega YA VALIDADO por la frontera de confianza (ADR-006): price y qty
        son Decimal finitos y positivos y el lado esta en el enum cerrado. Los CHECK de
        la tabla (0017) son el segundo cerrojo, el del motor, no el primero.

        Los Decimal viajan COMO Decimal a columnas numeric: convertirlos a float aqui
        perderia digitos en silencio, y el footprint que se agrega despues sumaria
        cantidades que no son las que ocurrieron.
        """
        with self._database.transaction() as session:
            escrito = session.fetchall(
                _INSERT_TRADE_SQL,
                (
                    trade.exchange,
                    trade.market_type.value,
                    trade.symbol,
                    trade.trade_id,
                    trade.price,
                    trade.qty,
                    trade.aggressor_side.value,
                    trade.event_time,
                    trade.source_sequence,
                ),
            )
        return bool(escrito)

    def last_seen(self, exchange: str, market_type: str, symbol: str) -> LastSeenTrade:
        """El trade persistido de mayor (event_time, trade_id) de ese flujo.

        Campos a None si el flujo no tiene ni una fila: es la PRIMERA conexion y no hay
        hueco posible, porque no se puede haber perdido lo que nunca se tuvo.
        """
        with self._database.transaction() as session:
            fila = session.fetchone(_LAST_SEEN_SQL, (exchange, market_type, symbol))
        if fila is None:
            return LastSeenTrade(trade_id=None, event_time_ms=None)
        return LastSeenTrade(trade_id=str(fila[0]), event_time_ms=int(str(fila[1])))

    def record_gap(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        gap_from_event_time_ms: int | None,
        gap_to_event_time_ms: int | None,
    ) -> bool:
        """Apunta un hueco NO cubierto. Devuelve True solo si la fila entro.

        IDEMPOTENTE por el UNIQUE de la tabla (con NULLS NOT DISTINCT, para que dos
        huecos iguales con un extremo desconocido no cuenten como distintos). El
        booleano distingue un hueco NUEVO de uno ya conocido, que es lo que permite que
        la metrica del motor cuente perdida de dato real y no reconexiones.
        """
        with self._database.transaction() as session:
            escrito = session.fetchall(
                _INSERT_GAP_SQL,
                (
                    exchange,
                    market_type,
                    symbol,
                    gap_from_event_time_ms,
                    gap_to_event_time_ms,
                ),
            )
        return bool(escrito)
