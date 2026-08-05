"""GATE ADR-007 de rsi.value: replay desde snapshot == wilder_rsi puro, BIT A BIT.

Contra PostgreSQL REAL y con los DOS roles que manda la regla 5.20: las velas las
ESCRIBE el rol de INGESTA por el camino real (PostgresCandleWriter, fixture
persistir_vela) y la serie la materializa el rol de REGLAS, el unico con el GRANT SELECT
de la 0016 sobre market_candle y con SELECT+INSERT sobre rsi_snapshot (0025). Con un
doble en memoria no se probarian ni los grants ni el SQL del rango.

QUE ES EL GATE. rsi.value es RECURSIVE (Wilder/RMA): el estado de la barra T depende del
de T-1. El materializador no recomputa desde el origen en cada tick, sino que ANCLA el
replay en un snapshot de estado. Eso solo es legitimo si la serie replayada es IDENTICA
-- no aproximada: identica en el ultimo digito -- a la que daria wilder_rsi desde el
origen, y si esa identidad NO depende de donde se cortase el snapshot. Si fallara, el
snapshot dejaria de ser una optimizacion y pasaria a ser una fuente de deriva.

LO QUE ESTE FICHERO ANADE SOBRE EL DE ema. El estado de Wilder son TRES valores
(avg_gain, avg_loss, last_close) en vez de uno, y la serie tiene WARM-UP: las barras
anteriores a la semilla no tienen RSI, asi que el materializador devuelve una serie MAS
CORTA que la ventana pedida en vez de rellenar. Las dos cosas se prueban aqui.

Se usa el spec del REGISTRO (SOURCE_MATERIALIZERS), no uno de prueba: lo que se valida
es el cableado de produccion.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest

from ce_v5.entrypoints.worker_rules.materializers import (
    SOURCE_MATERIALIZERS,
    ParameterizedMaterializer,
)
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.rsi_snapshot import read_rsi_snapshot_before
from ce_v5.platform.rules.indicators.rsi import (
    RSI_PERIOD_DEFAULT,
    RSI_SOURCE_ID,
    wilder_rsi,
)
from source.families.market import (
    CandleClosedPayload,
    CandlePayload,
    MarketCandleEventType,
    MarketType,
    Timeframe,
)
from source.rules.scalar import ScalarType, ScalarValue
from source.time import MaturityState

_DSN = os.environ.get("CE_V5_DATABASE_URL")
pytestmark = pytest.mark.skipif(_DSN is None, reason="requiere CE_V5_DATABASE_URL")

_EXCHANGE = "binance"
_SYMBOL = "BTC-USDT"
_TF = Timeframe.H1
_OPEN = 1_784_073_600_000

# Cierres FIJOS, todos DISTINTOS y ALTERNANDO subida/bajada: el RSI necesita ganancias Y
# perdidas para no saturarse en 100/0, donde _rsi_from_avgs corta por rama y una serie
# equivocada podria coincidir por casualidad. Con period=14 la semilla cae en la barra
# 14, asi que 24 barras dejan 10 valores maduros: suficiente para anclar en varios
# puntos y para que una ventana caiga a caballo del warm-up.
_CIERRES = tuple(
    Decimal(v)
    for v in (
        "20000.00",
        "20150.50",
        "20060.75",
        "20310.00",
        "20240.50",
        "20480.25",
        "20390.75",
        "20620.00",
        "20555.50",
        "20800.25",
        "20710.75",
        "20960.00",
        "20880.50",
        "21120.75",
        "21030.25",
        "21280.00",
        "21190.50",
        "21440.25",
        "21350.75",
        "21600.00",
        "21510.50",
        "21760.25",
        "21670.75",
        "21920.00",
    )
)
_BARRAS = len(_CIERRES)

Persistir = Callable[[CandlePayload, MarketCandleEventType, int], bool]


@pytest.fixture
def limpiar_rsi(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """market_candle/rsi_snapshot/outbox: sin FK a ningun tenant, se limpian a mano.

    Con el rol de MIGRACIONES porque el de reglas NO tiene DELETE sobre rsi_snapshot
    (append-only, 0025): que el test necesite otro rol para borrar es justamente la
    prueba de que la rendija del motor es solo SELECT+INSERT.
    """

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_candle")
            session.execute("DELETE FROM rsi_snapshot")
            session.execute("DELETE FROM outbox")

    _wipe()
    yield
    _wipe()


def _open_time(indice: int) -> int:
    return _OPEN + indice * _TF.duration_ms


def _vela(indice: int, close: Decimal) -> CandleClosedPayload:
    """La vela CERRADA numero `indice`, con su cierre propio.

    El rango OHLC se DERIVA del cierre (el contrato exige high >= open/close y
    low <= open/close). Lo que mide este fichero es el cierre.
    """
    open_time = _open_time(indice)
    return CandleClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange=_EXCHANGE,
        market_type=MarketType.SPOT,
        symbol=_SYMBOL,
        timeframe=_TF,
        open_time=open_time,
        close_time=open_time + _TF.duration_ms,
        open=close,
        high=close + Decimal("10"),
        low=close - Decimal("10"),
        close=close,
        volume=Decimal("12.5"),
    )


def _sembrar(persistir: Persistir, cuantas: int = _BARRAS) -> None:
    """Las primeras `cuantas` velas cerradas, por el camino REAL del rol de ingesta."""
    assert len(set(_CIERRES)) == _BARRAS  # cada barra distinguible por su cierre.
    for indice, close in enumerate(_CIERRES[:cuantas]):
        assert (
            persistir(
                _vela(indice, close),
                MarketCandleEventType.CANDLE_CLOSED,
                _OPEN + indice,
            )
            is True
        )


def _rsi_puro(
    hasta: int = _BARRAS, period: int = RSI_PERIOD_DEFAULT
) -> tuple[Decimal, ...]:
    """La VERDAD contra la que se mide todo replay: wilder_rsi hasta la barra `hasta`-1,
    ya sin los None de warm-up (que es como sale del materializador).
    """
    serie = wilder_rsi(list(_CIERRES[:hasta]), period)
    return tuple(v for v in serie if v is not None)


def _materializar(
    rules_db: PsycopgDatabase,
    open_time: int,
    history_bars: int,
    period: int | None = None,
) -> tuple[Decimal, ...]:
    """rsi.value con el spec REAL del registro, con el rol de reglas.

    period=None usa la instancia del registro (default 14); con period liga una copia
    por with_params, igual que hace el dispatch cuando el plan trae el override.
    """
    spec = SOURCE_MATERIALIZERS[RSI_SOURCE_ID]
    if period is not None:
        assert isinstance(spec, ParameterizedMaterializer)
        spec = spec.with_params(
            {
                "period": ScalarValue(
                    scalar_type=ScalarType.INTEGER, integer_value=period
                )
            }
        )
    with rules_db.transaction() as session:
        return spec.materialize(
            session, _EXCHANGE, _SYMBOL, _TF.value, open_time, history_bars
        )


def _leer_ancla(
    rules_db: PsycopgDatabase,
    before_open_time: int,
    period: int = RSI_PERIOD_DEFAULT,
) -> tuple[int, Decimal, Decimal, Decimal] | None:
    with rules_db.transaction() as session:
        return read_rsi_snapshot_before(
            session, _EXCHANGE, _SYMBOL, _TF.value, period, before_open_time
        )


def _contar_snapshots(
    rules_db: PsycopgDatabase, open_time: int, period: int = RSI_PERIOD_DEFAULT
) -> int:
    with rules_db.transaction() as session:
        fila = session.fetchone(
            "SELECT count(*) FROM rsi_snapshot WHERE open_time = %s AND period = %s",
            (open_time, period),
        )
    assert fila is not None
    total = fila[0]
    assert isinstance(total, int)
    return total


def _borrar_snapshots(migrator_db: PsycopgDatabase) -> None:
    """Borra los snapshots con el rol de MIGRACIONES (el de reglas no tiene DELETE)."""
    with migrator_db.transaction() as session:
        session.execute("DELETE FROM rsi_snapshot")


class TestRsiBootstrapDesdeElOrigen:
    """Sin ancla: la serie se siembra con rsi_seed en la barra `period`, desde el
    origen.

    Como en ema y por el mismo motivo: el RSI no es anchor-independiente, asi que
    recortar el bootstrap al inicio de la ventana daria OTRA serie.
    """

    def test_el_bootstrap_es_el_wilder_rsi_puro_desde_el_origen(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_rsi: None,
    ) -> None:
        _sembrar(persistir_vela)

        serie = _materializar(rules_db, _open_time(_BARRAS - 1), 4)

        assert serie == _rsi_puro()[-4:]
        # Y NO es el cierre: si el materializador devolviera la ventana de precios tal
        # cual (el fallo mudo mas facil), esta linea lo caza.
        assert serie != _CIERRES[-4:]

    def test_una_ventana_que_no_llega_al_origen_sigue_dando_el_rsi_del_origen(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_rsi: None,
    ) -> None:
        # LA condicion del bootstrap: aunque la regla pida 2 barras, el RSI de esas dos
        # es el que sale de arrastrar Wilder desde la semilla de la barra 14. Sembrar en
        # el inicio de la ventana daria numeros distintos.
        _sembrar(persistir_vela)

        serie = _materializar(rules_db, _open_time(_BARRAS - 1), 2)

        assert serie == _rsi_puro()[-2:]

    def test_el_bootstrap_persiste_el_estado_de_la_barra_vigente(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_rsi: None,
    ) -> None:
        # Materializar DEJA el ancla para la ventana siguiente. Sin esta escritura, cada
        # tick recomputaria desde el origen y el replay acotado no existiria. El
        # last_close persistido tiene que ser el cierre de la barra vigente: es lo que
        # hace que el primer diff del replay siguiente sea el correcto.
        _sembrar(persistir_vela)
        barra = _BARRAS - 1

        _materializar(rules_db, _open_time(barra), 4)

        ancla = _leer_ancla(rules_db, _open_time(barra) + 1)
        assert ancla is not None
        ancla_open_time, avg_gain, avg_loss, last_close = ancla
        assert ancla_open_time == _open_time(barra)
        assert last_close == _CIERRES[barra]
        # avg_gain/avg_loss son el estado real, no ceros de relleno.
        assert avg_gain > Decimal(0)
        assert avg_loss > Decimal(0)

    def test_sin_velas_no_inventa_serie_ni_snapshot(
        self,
        rules_db: PsycopgDatabase,
        limpiar_rsi: None,
    ) -> None:
        assert _materializar(rules_db, _open_time(0), 5) == ()
        assert _leer_ancla(rules_db, _open_time(_BARRAS)) is None


class TestWarmUpSerieMasCorta:
    """El warm-up sale como serie MAS CORTA, nunca como None a media serie.

    Un materializador devuelve tuple[Decimal, ...]: no puede emitir el None que
    wilder_rsi usa para el warm-up. La escasez se expresa devolviendo menos valores --
    igual que read_close_window y materialize_windowed --, y el evaluador la trata como
    NOT_EVALUABLE (K3). Lo que NUNCA se hace es rellenar.
    """

    def test_una_ventana_a_caballo_del_warm_up_sale_recortada(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_rsi: None,
    ) -> None:
        # Se piden 10 barras terminando en la 16: la ventana abarca las barras 7..16,
        # pero solo las 14, 15 y 16 tienen RSI (period=14). Salen TRES valores, no diez,
        # y son los tres maduros de wilder_rsi hasta esa barra.
        _sembrar(persistir_vela, cuantas=17)

        serie = _materializar(rules_db, _open_time(16), 10)

        assert len(serie) == 3
        assert serie == _rsi_puro(hasta=17)
        assert serie == wilder_rsi(list(_CIERRES[:17]), RSI_PERIOD_DEFAULT)[14:17]

    def test_una_ventana_entera_en_warm_up_da_serie_vacia_y_ningun_snapshot(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_rsi: None,
    ) -> None:
        # Sin las period+1 velas no hay ni semilla: () es NOT_EVALUABLE, no un error. Y
        # NO se persiste nada: un ancla sin estado real envenenaria todos los replays.
        _sembrar(persistir_vela, cuantas=11)

        assert _materializar(rules_db, _open_time(10), 5) == ()
        assert _leer_ancla(rules_db, _open_time(_BARRAS)) is None

    def test_la_barra_exacta_de_la_semilla_ya_emite_un_valor(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_rsi: None,
    ) -> None:
        # Frontera: con period+1 cierres (barras 0..14) la semilla existe y la serie
        # tiene UN valor, el de la barra 14. Una barra menos y no habria ninguno.
        _sembrar(persistir_vela, cuantas=15)

        serie = _materializar(rules_db, _open_time(14), 5)

        assert len(serie) == 1
        assert serie == _rsi_puro(hasta=15)


class TestGateBitExactoDelReplay:
    """EL GATE (ADR-007): replay desde snapshot == wilder_rsi puro, bit a bit, y sea
    cual sea el snapshot desde el que se replaye.
    """

    def test_el_replay_desde_el_snapshot_reproduce_el_wilder_rsi_puro(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_rsi: None,
    ) -> None:
        # Dos materializaciones en open_time DISTINTOS: la primera bootstrapea y deja el
        # ancla; la segunda YA replaya desde ella (su ventana empieza tras el ancla).
        _sembrar(persistir_vela)
        completo = _rsi_puro()  # indices 0..9 <-> barras 14..23

        primera = _materializar(rules_db, _open_time(18), 3)
        segunda = _materializar(rules_db, _open_time(23), 3)

        assert primera == completo[2:5]  # barras 16,17,18
        assert segunda == completo[7:10]  # barras 21,22,23
        # Bit a bit: igualdad de Decimal Y de representacion textual (dos Decimal
        # iguales pueden diferir en exponente; aqui no se admite ni eso).
        assert [str(v) for v in segunda] == [str(v) for v in completo[7:10]]

    def test_dos_anclas_distintas_dan_la_misma_cola(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_rsi: None,
    ) -> None:
        # EL GATE. El replay desde CUALQUIER snapshot valido -- y el bootstrap sin
        # ninguno -- reproduce la MISMA cola. Si esto fallara, el RSI valdria cosas
        # distintas segun donde se hubiera cortado el snapshot, y el snapshot seria una
        # fuente de deriva en vez de una optimizacion.
        _sembrar(persistir_vela)
        completo = _rsi_puro()
        ultimo = _open_time(23)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, _open_time(18), 3)  # ancla en la barra 18
        cola_ancla_18 = _materializar(rules_db, ultimo, 3)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, _open_time(20), 3)  # ancla en la barra 20
        cola_ancla_20 = _materializar(rules_db, ultimo, 3)

        _borrar_snapshots(migrator_db)
        cola_sin_ancla = _materializar(rules_db, ultimo, 3)  # bootstrap

        assert cola_ancla_18 == cola_ancla_20 == cola_sin_ancla
        assert cola_ancla_18 == completo[7:10]

    def test_el_estado_replayado_coincide_con_el_del_bootstrap(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_rsi: None,
    ) -> None:
        # No basta con que coincida la SERIE: el ESTADO persistido (las dos medias y el
        # cierre) tambien tiene que ser identico, porque es lo que sembrara el siguiente
        # replay. Si divergiera, la igualdad se rompria una barra mas adelante.
        _sembrar(persistir_vela)
        ultimo = _open_time(23)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, _open_time(18), 3)
        _materializar(rules_db, ultimo, 3)
        estado_replay = _leer_ancla(rules_db, ultimo + 1)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, ultimo, 3)
        estado_bootstrap = _leer_ancla(rules_db, ultimo + 1)

        assert estado_replay == estado_bootstrap
        assert estado_replay is not None

    def test_re_materializar_la_misma_barra_no_cambia_nada(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_rsi: None,
    ) -> None:
        # IDEMPOTENCIA (append-only, 0025): reevaluar la misma barra recomputa el MISMO
        # estado determinista, asi que el INSERT repetido es un duplicado exacto que el
        # ON CONFLICT DO NOTHING absorbe. Ni serie distinta, ni fila de mas.
        _sembrar(persistir_vela)
        ultimo = _open_time(23)

        una = _materializar(rules_db, ultimo, 3)
        ancla_tras_una = _leer_ancla(rules_db, ultimo + 1)
        otra = _materializar(rules_db, ultimo, 3)

        assert otra == una
        assert _contar_snapshots(rules_db, ultimo) == 1
        assert _leer_ancla(rules_db, ultimo + 1) == ancla_tras_una


class TestPeriodPorParametroEfectivo:
    """MAT-05 Q2 end-to-end: el period de la regla CAMBIA el hecho materializado."""

    def test_period_siete_da_otra_serie_y_es_el_wilder_rsi_puro_de_siete(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_rsi: None,
    ) -> None:
        _sembrar(persistir_vela)
        ultimo = _open_time(23)

        por_defecto = _materializar(rules_db, ultimo, 3)
        de_siete = _materializar(rules_db, ultimo, 3, period=7)

        assert por_defecto == _rsi_puro()[-3:]
        assert de_siete == _rsi_puro(period=7)[-3:]
        assert de_siete != por_defecto  # lo que prueba que el param VIAJA.

    def test_cada_period_tiene_su_propio_ancla(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_rsi: None,
    ) -> None:
        # period entra en la PK de rsi_snapshot (0025): dos series distintas no pueden
        # colisionar en la misma barra, y el ancla de rsi(7) NUNCA siembra un rsi(14).
        _sembrar(persistir_vela)
        ultimo = _open_time(23)

        _materializar(rules_db, ultimo, 3)
        _materializar(rules_db, ultimo, 3, period=7)

        ancla_14 = _leer_ancla(rules_db, ultimo + 1)
        ancla_7 = _leer_ancla(rules_db, ultimo + 1, period=7)
        assert ancla_14 is not None
        assert ancla_7 is not None
        assert ancla_14 != ancla_7
        # El cierre SI es el mismo (es la misma barra); lo que difiere son las medias.
        assert ancla_14[3] == ancla_7[3] == _CIERRES[23]
        assert (ancla_14[1], ancla_14[2]) != (ancla_7[1], ancla_7[2])
        assert _contar_snapshots(rules_db, ultimo) == 1
        assert _contar_snapshots(rules_db, ultimo, period=7) == 1

    def test_el_replay_de_un_period_no_usa_el_ancla_del_otro(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_rsi: None,
    ) -> None:
        # Se deja SOLO el ancla de period=14 en una barra intermedia y se materializa
        # rsi(7) despues: si el lector ignorase el period, sembraria rsi(7) con el
        # estado de rsi(14) y la serie se apartaria del wilder_rsi puro de 7.
        _sembrar(persistir_vela)
        _materializar(rules_db, _open_time(18), 3)  # ancla SOLO de period=14

        de_siete = _materializar(rules_db, _open_time(23), 3, period=7)

        assert de_siete == _rsi_puro(period=7)[-3:]
