"""Tests de integracion del store de TRADES individuales (P07b, ADR-006, regla 5.20).

Contra PostgreSQL REAL y con el rol de INGESTA. Lo que se prueba aqui NO lo puede probar
un doble en memoria:

- El DEDUP lo decide el MOTOR, no nuestro codigo: ON CONFLICT (exchange, market_type,
  symbol, trade_id) DO NOTHING ... RETURNING. Si la PK o el ON CONFLICT no cuadraran, el
  segundo insert o reventaria o duplicaria el trade -- y un trade duplicado es volumen
  inventado que el footprint sumaria dos veces.
- Los Decimal van y vuelven SIN perder digitos por columnas numeric. Un float por el
  camino y la barra ya no es la suma de sus trades.
- SIN OUTBOX, verificado contra la base: los trades no se publican (I-02). Que la outbox
  siga vacia tras persistir es la prueba, no una promesa del docstring.

Base de JUGUETE: nunca datos reales (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from decimal import Decimal

import pytest

from ce_v5.infra.db.market_trades import PostgresTradeWriter
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from source.families.footprint import MarketTrade
from source.families.market import AggressorSide, MarketType

_DSN = os.environ.get("CE_V5_DATABASE_URL")
pytestmark = pytest.mark.skipif(_DSN is None, reason="requiere CE_V5_DATABASE_URL")

_EVENT_TIME = 1_784_073_600_042


@pytest.fixture
def limpiar_trades(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """market_trade no tiene FK a nadie: se acumularia entre ejecuciones.

    Lo limpia el rol de MIGRACIONES a proposito: al de ingesta le esta REVOCADO el
    DELETE (0017, append-only real). Que el propio test necesite otro rol para borrar es
    la demostracion practica de que el historico no lo reescribe quien lo escribe.
    """

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_trade")
            session.execute("DELETE FROM outbox")

    _wipe()
    yield
    _wipe()


def _trade(trade_id: str = "77001", **overrides: object) -> MarketTrade:
    base: dict[str, object] = {
        "exchange": "binance",
        "market_type": MarketType.SPOT,
        "symbol": "BTC-USDT",
        "trade_id": trade_id,
        "price": Decimal("104.12345678"),
        "qty": Decimal("0.13500000"),
        "aggressor_side": AggressorSide.BUY,
        "event_time": _EVENT_TIME,
        "source_sequence": 4210,
    }
    base.update(overrides)
    return MarketTrade(**base)


def _contar(db: PsycopgDatabase, sql: str, params: tuple[object, ...] = ()) -> int:
    with db.transaction() as session:
        row = session.fetchone(sql, params)
    assert row is not None
    valor = row[0]
    assert isinstance(valor, int)
    return valor


class TestIdaYVuelta:
    def test_un_trade_se_lee_con_sus_valores_exactos(
        self, ingestion_db: PsycopgDatabase, limpiar_trades: None
    ) -> None:
        writer = PostgresTradeWriter(ingestion_db)
        assert writer.persist(_trade()) is True

        with ingestion_db.transaction() as session:
            fila = session.fetchone(
                "SELECT price, qty, aggressor_side, event_time, source_sequence "
                "FROM market_trade "
                "WHERE exchange = %s AND market_type = %s "
                "AND symbol = %s AND trade_id = %s",
                ("binance", "spot", "BTC-USDT", "77001"),
            )
        assert fila is not None
        # Decimal SIN perder precision: el footprint SUMA estos numeros trade a trade.
        assert fila[0] == Decimal("104.12345678")
        assert fila[1] == Decimal("0.13500000")
        assert fila[2] == "buy"
        # ADR-007: el instante es el del EXCHANGE, tal cual, sin reloj nuestro de por
        # medio.
        assert fila[3] == _EVENT_TIME
        assert fila[4] == 4210

    def test_el_lado_vendedor_y_la_secuencia_ausente_viajan_igual(
        self, ingestion_db: PsycopgDatabase, limpiar_trades: None
    ) -> None:
        # source_sequence es opcional: no todos los exchanges la publican. Un None es un
        # NULL, no un 0: inventar un cero seria afirmar una secuencia que nadie dio.
        writer = PostgresTradeWriter(ingestion_db)
        trade = _trade(
            trade_id="77002",
            aggressor_side=AggressorSide.SELL,
            source_sequence=None,
        )
        assert writer.persist(trade) is True

        with ingestion_db.transaction() as session:
            fila = session.fetchone(
                "SELECT aggressor_side, source_sequence FROM market_trade "
                "WHERE trade_id = %s",
                ("77002",),
            )
        assert fila is not None
        assert fila[0] == "sell"
        assert fila[1] is None


class TestDedup:
    def test_el_mismo_trade_dos_veces_no_duplica_y_lo_dice(
        self, ingestion_db: PsycopgDatabase, limpiar_trades: None
    ) -> None:
        # EL CASO NORMAL tras una reconexion + bootstrap REST. El segundo insert NO
        # revienta (ON CONFLICT DO NOTHING) y devuelve False (RETURNING vacio): esa
        # distincion es la que el motor cuenta como duplicates_skipped. Sin ella,
        # "insertado" y "ya estaba" serian indistinguibles y las metricas mentirian.
        writer = PostgresTradeWriter(ingestion_db)

        assert writer.persist(_trade()) is True
        assert writer.persist(_trade()) is False

        assert _contar(ingestion_db, "SELECT count(*) FROM market_trade") == 1

    def test_un_reenvio_con_otros_valores_tampoco_sobreescribe(
        self, ingestion_db: PsycopgDatabase, limpiar_trades: None
    ) -> None:
        # APPEND-ONLY: DO NOTHING, no DO UPDATE. Si el exchange reenviara el mismo
        # trade_id con otro precio, el hecho ORIGINAL no se toca. Un trade no se
        # corrige: ya ocurrio. (Y aunque quisieramos, al rol de ingesta le esta
        # REVOCADO el UPDATE.)
        writer = PostgresTradeWriter(ingestion_db)
        assert writer.persist(_trade()) is True

        assert writer.persist(_trade(price=Decimal("999.99"))) is False

        with ingestion_db.transaction() as session:
            fila = session.fetchone(
                "SELECT price FROM market_trade WHERE trade_id = %s", ("77001",)
            )
        assert fila is not None
        assert fila[0] == Decimal("104.12345678")  # INTACTO.

    def test_solo_colisiona_la_identidad_completa(
        self, ingestion_db: PsycopgDatabase, limpiar_trades: None
    ) -> None:
        # La identidad es (exchange, market_type, symbol, trade_id) ENTERA. Dos
        # exchanges pueden usar el mismo numero de trade sin ser el mismo hecho; si la
        # PK fuese solo trade_id, el segundo exchange perderia trades EN SILENCIO.
        writer = PostgresTradeWriter(ingestion_db)

        assert writer.persist(_trade(trade_id="7")) is True
        assert writer.persist(_trade(trade_id="7", exchange="okx")) is True
        assert writer.persist(_trade(trade_id="7", symbol="ETH-USDT")) is True

        assert _contar(ingestion_db, "SELECT count(*) FROM market_trade") == 3


class TestSinOutbox:
    def test_persistir_un_trade_no_encola_nada(
        self, ingestion_db: PsycopgDatabase, limpiar_trades: None
    ) -> None:
        # LA DIFERENCIA DE FONDO CON LAS VELAS, verificada contra la BASE: el trade se
        # persiste y NO se publica (I-02: publicar miles por minuto seria la avalancha).
        # Lo que llegara al bus es el FOOTPRINT por barra, que si va por outbox.
        writer = PostgresTradeWriter(ingestion_db)
        assert writer.persist(_trade()) is True

        assert _contar(ingestion_db, "SELECT count(*) FROM market_trade") == 1
        assert _contar(ingestion_db, "SELECT count(*) FROM outbox") == 0
