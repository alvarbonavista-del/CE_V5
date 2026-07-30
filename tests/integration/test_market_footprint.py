"""Tests de integracion del historico de footprint (ADR-013, ADR-007, regla 5.20).

Contra PostgreSQL REAL y con el rol de INGESTA. Lo que se prueba aqui NO lo puede probar
un doble en memoria:

- La ATOMICIDAD historico+outbox (ADR-013) la garantiza el MOTOR, no nuestro codigo: o
  estan las dos filas (footprint y outbox con el MISMO idempotency_key), o ninguna.
- El DEDUP por idempotency_key (PK + UNIQUE de la outbox): reprocesar el mismo footprint
  no duplica ni reencola.
- is_complete viaja y vuelve (columna de la migracion 0019): una barra incompleta se
  persiste COMO incompleta.
- Las CELDAS (jsonb) conservan el Decimal EXACTO: el footprint es la suma de volumenes
  trade a trade; un float perderia digitos en silencio.
- END TO END: el evento encolado es PUBLICABLE -- el publisher lo valida contra el
  registro CA-06 (market.footprint_closed -> FootprintClosedPayload) y lo saca al bus
  con el rol de INGESTA (cuyas policies de outbox admiten los market.footprint_*, 0017).

Base de JUGUETE: nunca datos reales (DOC_ENTREGABLES sec.5).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest
import redis

from ce_v5.entrypoints.worker_rules.materializers import (
    SOURCE_MATERIALIZERS,
    FootprintWindowedSpec,
)
from ce_v5.infra.bus_redis import RedisBusConfig, RedisEventBus, create_client
from ce_v5.infra.db.cvd_snapshot import read_cvd_snapshot_before, write_cvd_snapshot
from ce_v5.infra.db.market_footprint import (
    PostgresFootprintWriter,
    read_footprint_delta_range,
    read_footprint_window,
)
from ce_v5.infra.db.outbox_publisher import OutboxPublisher, topic_for
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.platform.rules.cvd import CVD_SOURCE_ID, ResetPolicy
from ce_v5.platform.rules.orderflow import (
    ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID,
    ORDERFLOW_DELTA_SOURCE_ID,
)
from ce_v5.platform.rules.volume_profile import (
    DEFAULT_BIN_COUNT,
    compute_volume_profile,
)
from source.envelope import Envelope
from source.envelope.enums import Scope
from source.families.footprint import (
    FootprintCell,
    FootprintClosedPayload,
    FootprintCorrectedPayload,
    FootprintPayload,
    MarketFootprintEventType,
)
from source.families.market import MarketType, Timeframe
from source.families.registry import expected_event_schema_version
from source.time import MaturityState

_DSN = os.environ.get("CE_V5_DATABASE_URL")
_URL = os.environ.get("CE_V5_REDIS_URL")
pytestmark = pytest.mark.skipif(
    _DSN is None or _URL is None,
    reason="requiere CE_V5_DATABASE_URL y CE_V5_REDIS_URL",
)

_TF = Timeframe.M1
_OPEN = 1_784_073_600_000
_CLOSE = _OPEN + _TF.duration_ms
_EVENT_TIME = _OPEN + 42

Persistir = Callable[[FootprintPayload, MarketFootprintEventType, int], bool]


@pytest.fixture
def limpiar_footprint(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """Footprint y outbox: sin FK a nadie, se acumularian entre ejecuciones."""

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_footprint")
            session.execute("DELETE FROM outbox")

    _wipe()
    yield
    _wipe()


def _cells(offset: Decimal = Decimal(0)) -> tuple[FootprintCell, ...]:
    # Dos niveles de precio con Decimal de muchos digitos: la prueba de que el jsonb no
    # los redondea. Ordenadas por precio ascendente (lo exige el contrato).
    #
    # offset DESPLAZA el nivel y el volumen comprador para que cada barra de una VENTANA
    # sea DISTINGUIBLE de las demas: con celdas identicas, un test de ventana no podria
    # detectar que el lector mezcla o reordena barras. offset=0 reproduce EXACTAMENTE
    # las celdas originales, asi que los tests que ya existian ven lo mismo que antes.
    return (
        FootprintCell(
            price=Decimal("100.12345678") + offset,
            buy_volume=Decimal("1.5") + offset,
            sell_volume=Decimal("0.25"),
            delta=Decimal("1.5") + offset - Decimal("0.25"),
        ),
        FootprintCell(
            price=Decimal("100.99999999") + offset,
            buy_volume=Decimal("0"),
            sell_volume=Decimal("3.0"),
            delta=Decimal("-3.0"),
        ),
    )


def _closed(
    is_complete: bool = True,  # noqa: FBT001, FBT002
    open_time: int = _OPEN,
    offset: Decimal = Decimal(0),
) -> FootprintClosedPayload:
    cells = _cells(offset)
    buy = sum((c.buy_volume for c in cells), Decimal(0))
    sell = sum((c.sell_volume for c in cells), Decimal(0))
    return FootprintClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol="BTC-USDT",
        timeframe=_TF,
        open_time=open_time,
        close_time=open_time + _TF.duration_ms,
        cells=cells,
        bar_buy_volume=buy,
        bar_sell_volume=sell,
        bar_delta=buy - sell,
        trade_count=3,
        is_complete=is_complete,
    )


def _corrected(
    revision: int,
    corrige: str,
    open_time: int = _OPEN,
    offset: Decimal = Decimal(0),
) -> FootprintCorrectedPayload:
    cells = _cells(offset)
    buy = sum((c.buy_volume for c in cells), Decimal(0))
    sell = sum((c.sell_volume for c in cells), Decimal(0))
    return FootprintCorrectedPayload(
        maturity_state=MaturityState.CORRECTION,
        corrects_idempotency_key=corrige,
        correction_revision=revision,
        exchange="binance",
        market_type=MarketType.SPOT,
        symbol="BTC-USDT",
        timeframe=_TF,
        open_time=open_time,
        close_time=open_time + _TF.duration_ms,
        cells=cells,
        bar_buy_volume=buy,
        bar_sell_volume=sell,
        bar_delta=buy - sell,
        trade_count=3,
        is_complete=True,
    )


def _envelope_de(
    payload: FootprintPayload, event_type: MarketFootprintEventType, event_time: int
) -> bytes:
    """El sobre canonico del footprint, como lo construye el motor (ADR-003/007)."""
    envelope = Envelope[FootprintPayload](
        event_type=event_type.value,
        event_schema_version=expected_event_schema_version(event_type.value),
        source="worker_footprint",
        idempotency_key=payload.idempotency_key(event_type),
        stream_key=payload.stream_key(),
        scope=Scope.PUBLIC_MARKET,  # los publicos NO llevan tenant (ADR-011).
        event_time=event_time,
        ingestion_time=event_time,
        processing_time=event_time,
        correlation_id=payload.stream_key(),
        payload=payload,
    )
    return envelope.model_dump_json().encode()


@pytest.fixture
def persistir_footprint(ingestion_db: PsycopgDatabase) -> Persistir:
    """Escribe un footprint por el camino REAL: historico+outbox atomico (INGESTA)."""
    writer = PostgresFootprintWriter(ingestion_db)

    def _persistir(
        payload: FootprintPayload, event_type: MarketFootprintEventType, event_time: int
    ) -> bool:
        return writer.persist_and_enqueue(
            envelope_json=_envelope_de(payload, event_type, event_time),
            payload=payload,
            event_type=event_type.value,
            stream_key=payload.stream_key(),
            idempotency_key=payload.idempotency_key(event_type),
        )

    return _persistir


def _contar(db: PsycopgDatabase, sql: str, params: tuple[object, ...] = ()) -> int:
    with db.transaction() as session:
        row = session.fetchone(sql, params)
    assert row is not None
    valor = row[0]
    assert isinstance(valor, int)
    return valor


class TestAtomicidad:
    def test_historico_y_outbox_o_los_dos_o_ninguno(
        self,
        ingestion_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # ADR-013 contra el MOTOR: tras un persist_and_enqueue con exito hay UNA fila en
        # market_footprint Y UNA en outbox, con el MISMO idempotency_key.
        payload = _closed()
        clave = payload.idempotency_key(MarketFootprintEventType.FOOTPRINT_CLOSED)

        assert (
            persistir_footprint(
                payload, MarketFootprintEventType.FOOTPRINT_CLOSED, _EVENT_TIME
            )
            is True
        )

        assert (
            _contar(
                ingestion_db,
                "SELECT count(*) FROM market_footprint WHERE idempotency_key = %s",
                (clave,),
            )
            == 1
        )
        assert (
            _contar(
                ingestion_db,
                "SELECT count(*) FROM outbox WHERE idempotency_key = %s",
                (clave,),
            )
            == 1
        )


class TestDedup:
    def test_el_mismo_footprint_dos_veces_no_duplica_ni_reencola(
        self,
        ingestion_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # Reprocesar el mismo candle_closed reconstruye la MISMA clave: idempotente.
        payload = _closed()

        assert (
            persistir_footprint(
                payload, MarketFootprintEventType.FOOTPRINT_CLOSED, _EVENT_TIME
            )
            is True
        )
        assert (
            persistir_footprint(
                payload, MarketFootprintEventType.FOOTPRINT_CLOSED, _EVENT_TIME
            )
            is False
        )

        assert _contar(ingestion_db, "SELECT count(*) FROM market_footprint") == 1
        assert _contar(ingestion_db, "SELECT count(*) FROM outbox") == 1


class TestIsCompleteViajaYVuelve:
    def test_una_barra_incompleta_se_persiste_como_incompleta(
        self,
        ingestion_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # La columna de la 0019: is_complete=False se guarda tal cual, sin perderse por
        # el DEFAULT. Una barra incompleta se persiste Y SE VE (0018 lo anticipo).
        payload = _closed(is_complete=False)
        clave = payload.idempotency_key(MarketFootprintEventType.FOOTPRINT_CLOSED)
        persistir_footprint(
            payload, MarketFootprintEventType.FOOTPRINT_CLOSED, _EVENT_TIME
        )

        with ingestion_db.transaction() as session:
            row = session.fetchone(
                "SELECT is_complete FROM market_footprint WHERE idempotency_key = %s",
                (clave,),
            )
        assert row is not None
        assert row[0] is False


class TestCeldasExactas:
    def test_el_jsonb_conserva_el_decimal_sin_redondear(
        self,
        ingestion_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # El Decimal viaja EN TEXTO dentro del jsonb: 100.12345678 vuelve intacto. Un
        # float binario lo habria corrompido, y el footprint mentiria al sumar trades.
        payload = _closed()
        clave = payload.idempotency_key(MarketFootprintEventType.FOOTPRINT_CLOSED)
        persistir_footprint(
            payload, MarketFootprintEventType.FOOTPRINT_CLOSED, _EVENT_TIME
        )

        with ingestion_db.transaction() as session:
            row = session.fetchone(
                "SELECT cells FROM market_footprint WHERE idempotency_key = %s",
                (clave,),
            )
        assert row is not None
        raw = row[0]
        cells = raw if isinstance(raw, list) else json.loads(str(raw))
        assert cells[0]["price"] == "100.12345678"
        assert cells[1]["price"] == "100.99999999"
        assert cells[0]["delta"] == "1.25"


class TestCorreccionAppendOnly:
    def test_una_correccion_convive_con_el_cerrado(
        self,
        ingestion_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # APPEND-ONLY (ADR-007): la correccion es un hecho NUEVO que apunta al cerrado
        # de la misma barra. Los dos conviven, con claves distintas.
        cerrado = _closed()
        clave_cerrado = cerrado.idempotency_key(
            MarketFootprintEventType.FOOTPRINT_CLOSED
        )
        persistir_footprint(
            cerrado, MarketFootprintEventType.FOOTPRINT_CLOSED, _EVENT_TIME
        )

        correccion = _corrected(1, clave_cerrado)
        clave_corregida = correccion.idempotency_key(
            MarketFootprintEventType.FOOTPRINT_CORRECTED
        )
        assert (
            persistir_footprint(
                correccion, MarketFootprintEventType.FOOTPRINT_CORRECTED, _EVENT_TIME
            )
            is True
        )

        assert _contar(ingestion_db, "SELECT count(*) FROM market_footprint") == 2
        assert (
            _contar(
                ingestion_db,
                "SELECT count(*) FROM market_footprint WHERE maturity_state = %s",
                ("correction",),
            )
            == 1
        )

        # LA CLAVE DEL CORREGIDO NO COLISIONA CON LA DEL CERRADO. La formula discrimina
        # por event_type + maturity_state + sufijo de revision (r1): si colisionaran, la
        # outbox (idempotency_key UNIQUE) se tragaria la segunda EN SILENCIO.
        assert clave_corregida != clave_cerrado

        # OUTBOX ATOMICO (ADR-013), EXPLICITO PARA EL CORREGIDO: el persist del
        # footprint corregido Y su encolado van en LA MISMA transaccion. La prueba
        # observable es que existen las DOS filas con la MISMA idempotency_key del
        # corregido; si no fuera atomico, faltaria una (footprint o evento).
        assert (
            _contar(
                ingestion_db,
                "SELECT count(*) FROM market_footprint WHERE idempotency_key = %s",
                (clave_corregida,),
            )
            == 1
        )
        assert (
            _contar(
                ingestion_db,
                "SELECT count(*) FROM outbox WHERE idempotency_key = %s "
                "AND event_type = %s",
                (clave_corregida, MarketFootprintEventType.FOOTPRINT_CORRECTED.value),
            )
            == 1
        )
        # APPEND-ONLY: el cerrado sigue teniendo SU fila de outbox, intacta. Dos hechos,
        # dos filas, dos claves: el corregido no reescribe ni desplaza al cerrado.
        assert _contar(ingestion_db, "SELECT count(*) FROM outbox") == 2

        # El evento encolado del corregido dice su madurez: correction + revision 1.
        with ingestion_db.transaction() as session:
            fila = session.fetchone(
                "SELECT envelope FROM outbox WHERE idempotency_key = %s",
                (clave_corregida,),
            )
        assert fila is not None
        envelope = fila[0] if isinstance(fila[0], dict) else json.loads(str(fila[0]))
        assert envelope["payload"]["maturity_state"] == "correction"
        assert envelope["payload"]["correction_revision"] == 1
        assert envelope["payload"]["corrects_idempotency_key"] == clave_cerrado


def _escribir_barras(
    persistir: Persistir, cuantas: int
) -> list[FootprintClosedPayload]:
    """Escribe `cuantas` barras CERRADAS consecutivas, cada una distinguible."""
    escritas: list[FootprintClosedPayload] = []
    for indice in range(cuantas):
        payload = _closed(
            open_time=_OPEN + indice * _TF.duration_ms,
            offset=Decimal(indice),
        )
        assert (
            persistir(payload, MarketFootprintEventType.FOOTPRINT_CLOSED, _EVENT_TIME)
            is True
        )
        escritas.append(payload)
    return escritas


def _leer_ventana(
    rules_db: PsycopgDatabase, up_to_open_time: int, bars: int
) -> tuple[FootprintPayload, ...]:
    """La ventana LEIDA CON EL ROL DE REGLAS (GRANT SELECT de la 0021)."""
    with rules_db.transaction() as session:
        return read_footprint_window(
            session, "binance", "BTC-USDT", _TF.value, up_to_open_time, bars
        )


def _assert_misma_barra(
    obtenida: FootprintPayload, esperada: FootprintClosedPayload
) -> None:
    """Igualdad campo a campo de una barra: totales, contador, celdas y madurez."""
    assert obtenida.open_time == esperada.open_time
    assert obtenida.close_time == esperada.close_time
    assert obtenida.exchange == esperada.exchange
    assert obtenida.symbol == esperada.symbol
    assert obtenida.timeframe == esperada.timeframe
    assert obtenida.market_type == esperada.market_type
    assert obtenida.bar_buy_volume == esperada.bar_buy_volume
    assert obtenida.bar_sell_volume == esperada.bar_sell_volume
    assert obtenida.bar_delta == esperada.bar_delta
    assert obtenida.trade_count == esperada.trade_count
    assert obtenida.is_complete == esperada.is_complete
    assert len(obtenida.cells) == len(esperada.cells)
    for celda, celda_esperada in zip(obtenida.cells, esperada.cells, strict=True):
        assert celda.price == celda_esperada.price
        assert celda.buy_volume == celda_esperada.buy_volume
        assert celda.sell_volume == celda_esperada.sell_volume
        assert celda.delta == celda_esperada.delta


class TestVentanaDeFootprintParaLaMaterializacion:
    """read_footprint_window: la BASE que consume la materializacion WINDOWED (CE-14).

    Se ESCRIBE con el rol de INGESTA y se LEE con el rol de REGLAS, que es el reparto
    real de poder (5.20): el GRANT SELECT de la 0021 es lo que hace posible esta
    lectura, y la escritura le sigue estando NEGADA. Un doble no probaria ni el dedup
    por revision (lo hace el DISTINCT ON del motor) ni que el Decimal del jsonb vuelve
    exacto tras el model_validate.
    """

    def test_round_trip_de_la_ventana_oldest_to_newest(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # (3.1) N barras cerradas consecutivas vuelven COMPLETAS y EN ORDEN: celdas
        # (precio/buy/sell/delta), totales de barra, trade_count e is_complete identicos
        # a lo escrito. El perfil de volumen se calcula sobre esto: si el lector
        # reordenara o mezclara barras, el POC saldria de una historia que no existio.
        escritas = _escribir_barras(persistir_footprint, 3)
        ultimo = escritas[-1].open_time

        ventana = _leer_ventana(rules_db, ultimo, 10)

        assert len(ventana) == 3
        tiempos = [barra.open_time for barra in ventana]
        assert tiempos == sorted(tiempos)  # oldest->newest, explicito.
        for obtenida, esperada in zip(ventana, escritas, strict=True):
            _assert_misma_barra(obtenida, esperada)

    def test_bars_menor_que_la_historia_da_las_mas_recientes(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # (3.2) El recorte es por el extremo ANTIGUO: con bars=2 sobre 3 barras se
        # devuelven las DOS MAS RECIENTES, no las dos primeras.
        escritas = _escribir_barras(persistir_footprint, 3)
        ultimo = escritas[-1].open_time

        ventana = _leer_ventana(rules_db, ultimo, 2)

        assert len(ventana) == 2
        _assert_misma_barra(ventana[0], escritas[1])
        _assert_misma_barra(ventana[1], escritas[2])

    def test_up_to_open_time_excluye_las_barras_posteriores(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # (3.3) La ventana se ancla en la barra que se evalua: nada con open_time MAYOR
        # entra. Mirar el futuro seria exactamente el look-ahead que invalida un
        # backtest.
        escritas = _escribir_barras(persistir_footprint, 3)

        ventana = _leer_ventana(rules_db, escritas[1].open_time, 10)

        assert len(ventana) == 2
        assert [barra.open_time for barra in ventana] == [
            escritas[0].open_time,
            escritas[1].open_time,
        ]
        assert escritas[2].open_time not in [barra.open_time for barra in ventana]

    def test_dedup_por_revision_devuelve_la_vigente(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # (3.4) Append-only (ADR-007): el cerrado y su correccion CONVIVEN en la tabla,
        # pero la ventana sirve UNA fila por open_time y es la VIGENTE (la revision mas
        # alta). Sin el DISTINCT ON, esa barra saldria DOS veces y desplazaria toda la
        # serie: las funciones que operan por posicion contarian una historia falsa.
        cerrado = _closed()
        clave_cerrado = cerrado.idempotency_key(
            MarketFootprintEventType.FOOTPRINT_CLOSED
        )
        persistir_footprint(
            cerrado, MarketFootprintEventType.FOOTPRINT_CLOSED, _EVENT_TIME
        )
        # offset distinto: la correccion trae OTROS numeros, asi que se puede comprobar
        # que lo servido es la correccion y no el cerrado original.
        correccion = _corrected(1, clave_cerrado, offset=Decimal(7))
        persistir_footprint(
            correccion, MarketFootprintEventType.FOOTPRINT_CORRECTED, _EVENT_TIME
        )

        ventana = _leer_ventana(rules_db, _OPEN, 10)

        assert len(ventana) == 1  # UNA sola fila para ese open_time.
        vigente = ventana[0]
        assert vigente.maturity_state is MaturityState.CORRECTION
        assert vigente.correction_revision == 1
        assert vigente.corrects_idempotency_key == clave_cerrado
        # Los numeros son los de la CORRECCION, no los del cerrado que reemplaza.
        assert vigente.bar_buy_volume == correccion.bar_buy_volume
        assert vigente.bar_delta == correccion.bar_delta
        assert vigente.cells[0].price == correccion.cells[0].price
        assert vigente.bar_buy_volume != cerrado.bar_buy_volume

    def test_sin_historia_suficiente_o_sin_coincidencia_devuelve_vacio(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # (3.5) Un hueco es un hecho AUSENTE: el lector no rellena nada. La tupla vacia
        # es lo que el evaluador traduce a NOT_EVALUABLE (K3), que NO es FALSE.
        assert _leer_ventana(rules_db, _OPEN, 10) == ()  # tabla vacia.

        escritas = _escribir_barras(persistir_footprint, 2)
        # Ancla ANTERIOR a toda la historia: ninguna barra califica.
        assert _leer_ventana(rules_db, escritas[0].open_time - 1, 10) == ()
        # Otro flujo (symbol que no existe): sin coincidencia, sin invencion.
        ultimo = escritas[-1].open_time
        with rules_db.transaction() as session:
            assert (
                read_footprint_window(
                    session, "binance", "ETH-USDT", _TF.value, ultimo, 10
                )
                == ()
            )


class TestMaterializacionWindowedSobreLaVentanaReal:
    """La composicion lector + materializador contra PostgreSQL REAL (CE-14, MAT-06).

    Cierra el circuito que ni el test puro del materializador ni el del lector cubren
    por separado: que la BASE leida de la BD, pasada por materialize_windowed, produce
    EXACTAMENTE la misma serie que aplicar la funcion pura a cada ventana rodante de los
    footprints que se escribieron. Misma base -> misma serie, bit a bit.

    window_bars=3 (no el 100 de produccion) para no escribir 100 barras: la constante
    real la fija el test unitario del registro. Lo que se prueba aqui es el MECANISMO.
    """

    def test_la_serie_es_el_perfil_de_cada_ventana_rodante(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        escritas = _escribir_barras(persistir_footprint, 5)
        ultimo = escritas[-1].open_time
        spec = FootprintWindowedSpec(
            transform=lambda ventana: (
                compute_volume_profile(ventana, bin_count=DEFAULT_BIN_COUNT).poc
            ),
            window_bars=3,
        )

        with rules_db.transaction() as session:
            serie = spec.materialize(
                session, "binance", "BTC-USDT", _TF.value, ultimo, 5
            )

        # Con 5 barras y ventana 3 solo hay 3 valores computables (las barras 3, 4 y 5);
        # las dos primeras no tienen ventana completa detras y NO se inventan.
        esperados = tuple(
            compute_volume_profile(
                escritas[inicio : inicio + 3], bin_count=DEFAULT_BIN_COUNT
            ).poc
            for inicio in range(3)
        )
        assert serie == esperados
        assert len(serie) == 3

    def test_historia_mas_corta_que_la_ventana_no_emite_valor(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # Dos barras y ventana de 3: NINGUN valor es computable. La serie vacia es lo
        # que el evaluador lee como NOT_EVALUABLE (K3); rellenar seria inventar.
        escritas = _escribir_barras(persistir_footprint, 2)
        spec = FootprintWindowedSpec(
            transform=lambda ventana: (
                compute_volume_profile(ventana, bin_count=DEFAULT_BIN_COUNT).poc
            ),
            window_bars=3,
        )

        with rules_db.transaction() as session:
            serie = spec.materialize(
                session,
                "binance",
                "BTC-USDT",
                _TF.value,
                escritas[-1].open_time,
                5,
            )

        assert serie == ()


class TestMaterializacionPointLocalDeOrderflowDelta:
    """orderflow.delta materializada del REGISTRO REAL contra PostgreSQL (MAT-07).

    Se usa el spec del registro (SOURCE_MATERIALIZERS), no uno de prueba: lo que se
    valida es el cableado que correra en produccion. POINT_LOCAL = un valor por barra,
    el bar_delta de esa barra, sin ventana ni acumulacion. Es la BASE de cvd.value
    (T5b-2): si esta serie estuviera desalineada, el acumulado heredaria el error.
    """

    def test_la_serie_es_el_bar_delta_de_cada_barra(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # Cada barra lleva un offset distinto, asi que su bar_delta tambien: una serie
        # desordenada o desplazada no podria coincidir por casualidad.
        escritas = _escribir_barras(persistir_footprint, 4)
        deltas = [barra.bar_delta for barra in escritas]
        assert len(set(deltas)) == 4  # los cuatro deltas son distintos.
        spec = SOURCE_MATERIALIZERS[ORDERFLOW_DELTA_SOURCE_ID]

        with rules_db.transaction() as session:
            serie = spec.materialize(
                session,
                "binance",
                "BTC-USDT",
                _TF.value,
                escritas[-1].open_time,
                4,
            )

        assert serie == tuple(deltas)

    def test_bars_menor_que_la_historia_da_los_mas_recientes(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # POINT_LOCAL no necesita historia extra: history_bars=2 sobre 4 barras da los
        # DOS deltas mas recientes, recortados por el extremo antiguo.
        escritas = _escribir_barras(persistir_footprint, 4)
        spec = SOURCE_MATERIALIZERS[ORDERFLOW_DELTA_SOURCE_ID]

        with rules_db.transaction() as session:
            serie = spec.materialize(
                session,
                "binance",
                "BTC-USDT",
                _TF.value,
                escritas[-1].open_time,
                2,
            )

        assert serie == (escritas[2].bar_delta, escritas[3].bar_delta)


class TestElEventoEncoladoEsPublicable:
    def test_el_publisher_lo_valida_y_lo_saca_al_bus(
        self,
        ingestion_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # END TO END: si el envelope no cumpliera el registro event_type -> payload
        # (CA-06), el publisher lo RECHAZARIA. Que salga demuestra que el sobre del
        # footprint es valido de verdad. El publisher corre con el ROL DE INGESTA, cuyas
        # policies de outbox admiten los market.footprint_* (0017).
        assert _URL is not None
        config = RedisBusConfig(url=_URL, namespace="test-" + uuid.uuid4().hex)
        client: redis.Redis = create_client(config)
        try:
            bus = RedisEventBus(client, config)
            payload = _closed()
            persistir_footprint(
                payload, MarketFootprintEventType.FOOTPRINT_CLOSED, _EVENT_TIME
            )

            publisher = OutboxPublisher(db=ingestion_db, bus=bus)
            assert publisher.drain_once() == 1

            topic = topic_for(MarketFootprintEventType.FOOTPRINT_CLOSED.value)
            bus.ensure_group(topic, "g1")
            recibidos = bus.poll(topic, "g1", "c1", max_messages=10, block_ms=0)
            assert len(recibidos) == 1

            envelope = json.loads(recibidos[0].message.envelope)
            assert envelope["event_type"] == "market.footprint_closed"
            assert envelope["scope"] == "public_market"
            assert envelope["tenant_id"] is None
            assert envelope["event_time"] == _EVENT_TIME
            assert envelope["payload"]["is_complete"] is True
            assert envelope["payload"]["cells"][0]["price"] == "100.12345678"

            assert (
                _contar(
                    ingestion_db,
                    "SELECT count(*) FROM outbox WHERE published_at IS NULL",
                )
                == 0
            )
        finally:
            for key in client.scan_iter(match=f"{config.namespace}:*"):
                client.delete(key)
            client.close()


@pytest.fixture
def limpiar_cvd_snapshot(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """cvd_snapshot: sin FK a nadie, se acumularia entre ejecuciones (como footprint).

    Se limpia con el rol de MIGRACIONES a proposito: el rol de reglas NO tiene DELETE
    sobre su propio estado (append-only, 0022), asi que no podria hacerlo ni para un
    test -- y eso es exactamente lo que el check 5.20 garantiza.
    """

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM cvd_snapshot")

    _wipe()
    yield
    _wipe()


_ROLLING = ResetPolicy.ROLLING.value


def _escribir_snapshot(
    rules_db: PsycopgDatabase, open_time: int, value: Decimal
) -> None:
    """Escribe un snapshot con el rol de REGLAS (el INSERT que le dio la 0022)."""
    with rules_db.transaction() as session:
        write_cvd_snapshot(
            session, "binance", "BTC-USDT", _TF.value, _ROLLING, open_time, value
        )


def _leer_ancla(
    rules_db: PsycopgDatabase, before_open_time: int
) -> tuple[int, Decimal] | None:
    with rules_db.transaction() as session:
        return read_cvd_snapshot_before(
            session, "binance", "BTC-USDT", _TF.value, _ROLLING, before_open_time
        )


class TestStoreDelSnapshotDeCvd:
    """3.1: el estado de replay del INTEGRATOR, escrito y leido por el ROL DE REGLAS.

    Es el primer caso en que ce_v5_rules ESCRIBE (0022, MAT-07): lo que se prueba aqui
    contra PostgreSQL real es que ese grant funciona de verdad, que el ancla elegida es
    la correcta (la mas reciente ANTERIOR a la ventana) y que el Decimal vuelve exacto.
    """

    def test_el_ancla_es_el_snapshot_mas_reciente_anterior(
        self,
        rules_db: PsycopgDatabase,
        limpiar_cvd_snapshot: None,
    ) -> None:
        # Tres barras con valores distintos. El ancla de una ventana que empieza en la
        # tercera es la SEGUNDA: la mas reciente ESTRICTAMENTE anterior.
        primera = _OPEN
        segunda = _OPEN + _TF.duration_ms
        tercera = _OPEN + 2 * _TF.duration_ms
        _escribir_snapshot(rules_db, primera, Decimal("10.5"))
        _escribir_snapshot(rules_db, segunda, Decimal("-3.25"))
        _escribir_snapshot(rules_db, tercera, Decimal("100.125"))

        assert _leer_ancla(rules_db, tercera) == (segunda, Decimal("-3.25"))
        assert _leer_ancla(rules_db, segunda) == (primera, Decimal("10.5"))

    def test_el_limite_es_estricto_no_devuelve_la_propia_barra(
        self,
        rules_db: PsycopgDatabase,
        limpiar_cvd_snapshot: None,
    ) -> None:
        # open_time < before, ESTRICTO: si devolviera la barra igual al limite, el
        # replay sumaria su delta DOS veces (el ancla ya lo lleva) y el CVD derivaria.
        _escribir_snapshot(rules_db, _OPEN, Decimal("7"))
        assert _leer_ancla(rules_db, _OPEN) is None
        assert _leer_ancla(rules_db, _OPEN + 1) == (_OPEN, Decimal("7"))

    def test_sin_ancla_anterior_devuelve_none(
        self,
        rules_db: PsycopgDatabase,
        limpiar_cvd_snapshot: None,
    ) -> None:
        # None NO es un cero: significa "no hay ancla", y el materializador arranca el
        # acumulado en el inicio de la ventana (bootstrap). Confundirlo con 0 seria
        # afirmar un acumulado que nadie calculo.
        assert _leer_ancla(rules_db, _OPEN) is None
        _escribir_snapshot(rules_db, _OPEN + 10 * _TF.duration_ms, Decimal("1"))
        assert _leer_ancla(rules_db, _OPEN) is None

    def test_reinsertar_la_misma_barra_es_idempotente(
        self,
        rules_db: PsycopgDatabase,
        limpiar_cvd_snapshot: None,
    ) -> None:
        # Reevaluar la misma barra recomputa el MISMO valor (el fold es determinista),
        # asi que el segundo INSERT es un duplicado exacto: ON CONFLICT DO NOTHING lo
        # absorbe sin fallar y sin duplicar. Si lanzara, un redelivery del bus tumbaria
        # el tick; si duplicara, la PK no seria identidad.
        _escribir_snapshot(rules_db, _OPEN, Decimal("42.75"))
        _escribir_snapshot(rules_db, _OPEN, Decimal("42.75"))

        assert (
            _contar(
                rules_db,
                "SELECT count(*) FROM cvd_snapshot WHERE open_time = %s",
                (_OPEN,),
            )
            == 1
        )
        assert _leer_ancla(rules_db, _OPEN + 1) == (_OPEN, Decimal("42.75"))

    def test_el_valor_decimal_vuelve_exacto(
        self,
        rules_db: PsycopgDatabase,
        limpiar_cvd_snapshot: None,
    ) -> None:
        # numeric, no float: el CVD es una suma de deltas trade a trade, y un binario
        # perderia digitos en silencio a lo largo del acumulado.
        valor = Decimal("-12345.6789012345")
        _escribir_snapshot(rules_db, _OPEN, valor)
        ancla = _leer_ancla(rules_db, _OPEN + 1)
        assert ancla is not None
        assert ancla[1] == valor


class TestLectorDeRangoDeBarDelta:
    """3.2: los deltas POSTERIORES al ancla, leidos por RANGO con el rol de reglas.

    (after, up_to]: inferior ABIERTO porque el ancla ya aporta su valor -- incluir su
    barra sumaria ese delta dos veces --, superior CERRADO porque la barra pedida SI
    entra. Mismo dedup por revision que read_footprint_window.
    """

    def test_devuelve_las_barras_del_rango_oldest_to_newest(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        escritas = _escribir_barras(persistir_footprint, 4)
        deltas = [barra.bar_delta for barra in escritas]
        assert len(set(deltas)) == 4  # cada barra es distinguible por su delta.

        with rules_db.transaction() as session:
            rango = read_footprint_delta_range(
                session,
                "binance",
                "BTC-USDT",
                _TF.value,
                escritas[0].open_time,
                escritas[3].open_time,
            )

        # Inferior ABIERTO: la barra 0 (== after) NO entra. Superior CERRADO: la 3 SI.
        assert rango == (
            (escritas[1].open_time, escritas[1].bar_delta),
            (escritas[2].open_time, escritas[2].bar_delta),
            (escritas[3].open_time, escritas[3].bar_delta),
        )

    def test_el_limite_inferior_es_abierto_y_el_superior_cerrado(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        escritas = _escribir_barras(persistir_footprint, 4)

        with rules_db.transaction() as session:
            rango = read_footprint_delta_range(
                session,
                "binance",
                "BTC-USDT",
                _TF.value,
                escritas[1].open_time,
                escritas[2].open_time,
            )

        tiempos = [open_time for open_time, _ in rango]
        assert escritas[1].open_time not in tiempos  # after EXCLUIDO.
        assert escritas[2].open_time in tiempos  # up_to INCLUIDO.
        assert tiempos == [escritas[2].open_time]

    def test_rango_vacio_da_tupla_vacia(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # after == up_to deja el rango (X, X] vacio: no hay barra que acumular, y el
        # materializador se queda con el valor del ancla tal cual.
        escritas = _escribir_barras(persistir_footprint, 3)
        ultimo = escritas[-1].open_time

        with rules_db.transaction() as session:
            vacio = read_footprint_delta_range(
                session, "binance", "BTC-USDT", _TF.value, ultimo, ultimo
            )
            sin_coincidencia = read_footprint_delta_range(
                session, "binance", "ETH-USDT", _TF.value, 0, ultimo
            )

        assert vacio == ()
        assert sin_coincidencia == ()

    def test_dedup_por_revision_sirve_el_delta_de_la_correccion(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # Append-only (ADR-007): el cerrado y su correccion CONVIVEN, pero el rango debe
        # servir UNA fila por barra con el delta VIGENTE. Si sirviera las dos, el CVD
        # acumularia esa barra dos veces; si sirviera la vieja, ignoraria la correccion.
        cerrado = _closed()
        clave = cerrado.idempotency_key(MarketFootprintEventType.FOOTPRINT_CLOSED)
        persistir_footprint(
            cerrado, MarketFootprintEventType.FOOTPRINT_CLOSED, _EVENT_TIME
        )
        correccion = _corrected(1, clave, offset=Decimal(11))
        persistir_footprint(
            correccion, MarketFootprintEventType.FOOTPRINT_CORRECTED, _EVENT_TIME
        )
        assert correccion.bar_delta != cerrado.bar_delta

        with rules_db.transaction() as session:
            rango = read_footprint_delta_range(
                session, "binance", "BTC-USDT", _TF.value, _OPEN - 1, _OPEN
            )

        assert rango == ((_OPEN, correccion.bar_delta),)


def _borrar_snapshots(migrator_db: PsycopgDatabase) -> None:
    """Borra los snapshots con el rol de MIGRACIONES (el de reglas no tiene DELETE)."""
    with migrator_db.transaction() as session:
        session.execute("DELETE FROM cvd_snapshot")


def _cumsum(deltas: list[Decimal]) -> list[Decimal]:
    """Acumulado desde el ORIGEN: la verdad contra la que se mide todo replay."""
    total = Decimal(0)
    salida: list[Decimal] = []
    for delta in deltas:
        total += delta
        salida.append(total)
    return salida


def _materializar_cvd(
    rules_db: PsycopgDatabase, open_time: int, history_bars: int
) -> tuple[Decimal, ...]:
    """cvd.value materializado con el spec REAL del registro, con el rol de reglas."""
    spec = SOURCE_MATERIALIZERS[CVD_SOURCE_ID]
    with rules_db.transaction() as session:
        return spec.materialize(
            session, "binance", "BTC-USDT", _TF.value, open_time, history_bars
        )


class TestCvdIntegratorReplayDesdeSnapshot:
    """cvd.value INTEGRATOR: replay ACOTADO desde snapshot contra PostgreSQL (MAT-07).

    Se usa el spec del REGISTRO (SOURCE_MATERIALIZERS), no uno de prueba: lo que se
    valida es el cableado de produccion, componiendo los cuatro piezas -- ventana de
    footprint, ancla de snapshot, rango de deltas y materialize_recursive -- mas la
    PERSISTENCIA del snapshot de la barra vigente. Es el unico materializador CON
    ESTADO: el snapshot ES su memoria de replay.
    """

    def test_bootstrap_sin_ancla_acumula_desde_el_inicio_de_la_ventana(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
        limpiar_cvd_snapshot: None,
    ) -> None:
        # 4.1 Sin snapshot previo el acumulado arranca en 0 en la primera barra de la
        # ventana. El valor ABSOLUTO del rolling es anchor-dependiente (cvd.py); lo que
        # NO depende del ancla es la forma de la serie, y eso es lo que F3 usa.
        escritas = _escribir_barras(persistir_footprint, 5)
        deltas = [barra.bar_delta for barra in escritas]
        assert len(set(deltas)) == 5  # cada barra es distinguible por su delta.
        ultimo = escritas[-1].open_time

        serie = _materializar_cvd(rules_db, ultimo, 5)

        assert serie == tuple(_cumsum(deltas))

    def test_bootstrap_persiste_el_snapshot_de_la_barra_vigente(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
        limpiar_cvd_snapshot: None,
    ) -> None:
        # 4.1 (segunda mitad): materializar DEJA el ancla para la ventana siguiente. Sin
        # esta escritura, cada tick volveria a acumular desde el inicio de su ventana y
        # el replay acotado no existiria.
        escritas = _escribir_barras(persistir_footprint, 5)
        deltas = [barra.bar_delta for barra in escritas]
        ultimo = escritas[-1].open_time

        _materializar_cvd(rules_db, ultimo, 5)

        assert _leer_ancla(rules_db, ultimo + 1) == (ultimo, _cumsum(deltas)[-1])

    def test_con_ancla_el_replay_reproduce_la_cola_del_acumulado_de_origen(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
        limpiar_cvd_snapshot: None,
    ) -> None:
        # 4.2 Sembrado desde un snapshot CONSISTENTE con el origen, la ventana corta
        # reproduce EXACTAMENTE los valores que tendria acumulando desde el origen. Esa
        # es la razon de ser del snapshot: mismo resultado, replay acotado.
        escritas = _escribir_barras(persistir_footprint, 5)
        acumulado = _cumsum([barra.bar_delta for barra in escritas])
        _borrar_snapshots(migrator_db)
        # Ancla en la barra 1; la ventana pedida (M=2) son las barras 3 y 4.
        _escribir_snapshot(rules_db, escritas[1].open_time, acumulado[1])

        serie = _materializar_cvd(rules_db, escritas[-1].open_time, 2)

        assert serie == (acumulado[3], acumulado[4])

    def test_gate_bit_exacto_dos_anclas_distintas_dan_la_misma_cola(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
        limpiar_cvd_snapshot: None,
    ) -> None:
        # 4.3 EL GATE de ADR-007. El replay desde CUALQUIER snapshot valido reproduce la
        # MISMA cola, bit a bit: el fold es determinista sobre Decimal. Si esto fallara,
        # el CVD daria valores distintos segun donde se hubiera cortado el snapshot, y
        # entonces el snapshot seria una fuente de deriva, no una optimizacion.
        escritas = _escribir_barras(persistir_footprint, 5)
        acumulado = _cumsum([barra.bar_delta for barra in escritas])
        ultimo = escritas[-1].open_time
        # Dos anclas distintas, ambas ANTERIORES al inicio de la ventana (barra 3).
        k1, k2 = 2, 0

        _borrar_snapshots(migrator_db)
        _escribir_snapshot(rules_db, escritas[k1].open_time, acumulado[k1])
        serie_1 = _materializar_cvd(rules_db, ultimo, 2)

        _borrar_snapshots(migrator_db)
        _escribir_snapshot(rules_db, escritas[k2].open_time, acumulado[k2])
        serie_2 = _materializar_cvd(rules_db, ultimo, 2)

        assert serie_1 == serie_2
        assert serie_1 == (acumulado[3], acumulado[4])

    def test_sin_footprint_no_inventa_serie_ni_snapshot(
        self,
        rules_db: PsycopgDatabase,
        limpiar_footprint: None,
        limpiar_cvd_snapshot: None,
    ) -> None:
        # Sin base no hay acumulado: tupla vacia (NOT_EVALUABLE, K3) y NINGUN snapshot
        # escrito. Persistir un snapshot de una barra que no existe plantaria un ancla
        # falsa que envenenaria todos los replays posteriores.
        assert _materializar_cvd(rules_db, _OPEN, 5) == ()
        assert _leer_ancla(rules_db, _OPEN + 10 * _TF.duration_ms) is None


def _materializar_delta_momentum(
    rules_db: PsycopgDatabase, open_time: int, history_bars: int
) -> tuple[Decimal, ...]:
    """delta_momentum con el spec REAL del registro, leyendo con el rol de reglas."""
    spec = SOURCE_MATERIALIZERS[ORDERFLOW_DELTA_MOMENTUM_SOURCE_ID]
    with rules_db.transaction() as session:
        return spec.materialize(
            session, "binance", "BTC-USDT", _TF.value, open_time, history_bars
        )


class TestDeltaMomentumSobreLaSerieDeDelta:
    """orderflow.delta_momentum: DAG de 2o NIVEL contra PostgreSQL real (MAT-08).

    Es la primera fuente que se materializa sobre otra fuente DERIVADA
    (orderflow.delta), no sobre el footprint crudo: el DerivedSeriesSpec pide la serie
    de la base a SU materializador del registro y le aplica la funcion pura de paridad
    v4. Lo que estos dos tests separan es el borde: con historia por delante, el primer
    valor de la ventana usa su prior REAL (lookback=1); en la primera barra ABSOLUTA no
    hay prior y la funcion pura da 0.
    """

    def test_con_historia_previa_el_primer_valor_usa_su_prior_real(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # 5.1 EL TEST DEL LOOKBACK. Con 5 barras y history_bars=3, la ventana pedida son
        # las barras 2, 3 y 4. Si el spec no pidiera la barra 1 de mas, el primer valor
        # saldria 0 (borde falso) en vez de d2-d1: un cambio de delta INVENTADO justo en
        # el punto donde una regla de momentum dispararia.
        escritas = _escribir_barras(persistir_footprint, 5)
        deltas = [barra.bar_delta for barra in escritas]
        assert len(set(deltas)) == 5  # cada barra distinguible por su delta.

        serie = _materializar_delta_momentum(rules_db, escritas[-1].open_time, 3)

        assert serie == (
            deltas[2] - deltas[1],
            deltas[3] - deltas[2],
            deltas[4] - deltas[3],
        )
        assert serie[0] != Decimal(0)  # el borde falso NO aparece.

    def test_en_la_primera_barra_absoluta_no_hay_prior_y_vale_cero(
        self,
        rules_db: PsycopgDatabase,
        persistir_footprint: Persistir,
        limpiar_footprint: None,
    ) -> None:
        # 5.2 El borde REAL: pidiendo TODA la historia, la barra 0 no tiene barra previa
        # que exista, asi que compute_delta_momentum da 0 [PARIDAD v4]. Eso NO es el
        # borde falso del test anterior: aqui el cero es la respuesta correcta.
        escritas = _escribir_barras(persistir_footprint, 5)
        deltas = [barra.bar_delta for barra in escritas]

        serie = _materializar_delta_momentum(rules_db, escritas[-1].open_time, 5)

        assert serie == (
            Decimal(0),
            deltas[1] - deltas[0],
            deltas[2] - deltas[1],
            deltas[3] - deltas[2],
            deltas[4] - deltas[3],
        )
        assert len(serie) == 5
