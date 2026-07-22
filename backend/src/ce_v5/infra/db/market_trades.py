"""Escritura de trades individuales (P07b; ADR-014, ADR-006, regla 5.20).

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
