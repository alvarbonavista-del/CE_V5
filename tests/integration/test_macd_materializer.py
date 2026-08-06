"""GATE ADR-007 de macd.*: replay desde snapshot == macd() puro, BIT A BIT, en las TRES.

Contra PostgreSQL REAL y con los DOS roles que manda la regla 5.20: las velas las
ESCRIBE el rol de INGESTA por el camino real (PostgresCandleWriter, fixture
persistir_vela) y las series las materializa el rol de REGLAS, el unico con el GRANT
SELECT de la 0016 sobre market_candle y con SELECT+INSERT sobre macd_snapshot (0026).

QUE ES EL GATE. El MACD es RECURSIVE por dentro: tres EMAs encadenadas, cada una
dependiente de su T-1. El materializador no recomputa desde el origen en cada tick, sino
que ANCLA el replay en un snapshot de estado. Eso solo es legitimo si las series
replayadas son IDENTICAS -- no aproximadas: identicas en el ultimo digito -- a las que
daria macd() desde el origen, y si esa identidad NO depende de donde se cortase el
snapshot.

LO QUE ESTE FICHERO ANADE SOBRE EL DE rsi. Son TRES fuentes que comparten UN estado y UN
snapshot, asi que aqui se prueba ademas que las tres publican proyecciones DISTINTAS del
mismo calculo y que materializar cualquiera de ellas deja el MISMO estado persistido (el
ON CONFLICT DO NOTHING las hace convivir). Y no hay warm-up: la serie devuelta tiene
siempre tantos valores como barras tenga la ventana.

Se usa el spec del REGISTRO (SOURCE_MATERIALIZERS), no uno de prueba.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

import pytest

from ce_v5.entrypoints.worker_rules.materializers import (
    SOURCE_MATERIALIZERS,
    ParameterizedMaterializer,
    _scalars_to_decimals,
)
from ce_v5.infra.db.macd_snapshot import read_macd_snapshot_before
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.platform.rules.indicators.macd import (
    MACD_FAST_DEFAULT,
    MACD_HISTOGRAM_SOURCE_ID,
    MACD_LINE_SOURCE_ID,
    MACD_SIGNAL_DEFAULT,
    MACD_SIGNAL_SOURCE_ID,
    MACD_SLOW_DEFAULT,
    macd,
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

# Cierres FIJOS, todos DISTINTOS y con subidas y bajadas: si el materializador
# devolviera la ventana desplazada o el propio cierre, con una serie monotona podria
# colar por casualidad. 30 barras dan recorrido de sobra a las tres EMAs (la lenta es
# 26).
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
        "21830.50",
        "22080.25",
        "21990.75",
        "22240.00",
        "22150.50",
        "22400.25",
    )
)
_BARRAS = len(_CIERRES)
_TODAS = (MACD_LINE_SOURCE_ID, MACD_SIGNAL_SOURCE_ID, MACD_HISTOGRAM_SOURCE_ID)

Persistir = Callable[[CandlePayload, MarketCandleEventType, int], bool]


@pytest.fixture
def limpiar_macd(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """market_candle/macd_snapshot/outbox: sin FK a ningun tenant, se limpian a mano.

    Con el rol de MIGRACIONES porque el de reglas NO tiene DELETE sobre macd_snapshot
    (append-only, 0026): que el test necesite otro rol para borrar es justamente la
    prueba de que la rendija del motor es solo SELECT+INSERT.
    """

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_candle")
            session.execute("DELETE FROM macd_snapshot")
            session.execute("DELETE FROM outbox")

    _wipe()
    yield
    _wipe()


def _open_time(indice: int) -> int:
    return _OPEN + indice * _TF.duration_ms


def _vela(indice: int, close: Decimal) -> CandleClosedPayload:
    """La vela CERRADA numero `indice`, con su cierre propio (OHLC derivado)."""
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


def _sembrar(persistir: Persistir) -> None:
    """Las _BARRAS velas cerradas, por el camino REAL del rol de ingesta."""
    assert len(set(_CIERRES)) == _BARRAS  # cada barra distinguible por su cierre.
    for indice, close in enumerate(_CIERRES):
        assert (
            persistir(
                _vela(indice, close),
                MarketCandleEventType.CANDLE_CLOSED,
                _OPEN + indice,
            )
            is True
        )


def _macd_puro(
    fast: int = MACD_FAST_DEFAULT,
    slow: int = MACD_SLOW_DEFAULT,
    signal: int = MACD_SIGNAL_DEFAULT,
) -> dict[str, tuple[Decimal, ...]]:
    """La VERDAD contra la que se mide todo replay: macd() sobre la serie ENTERA."""
    resultado = macd(list(_CIERRES), fast, slow, signal)
    return {
        MACD_LINE_SOURCE_ID: resultado.macd,
        MACD_SIGNAL_SOURCE_ID: resultado.signal,
        MACD_HISTOGRAM_SOURCE_ID: resultado.histogram,
    }


def _materializar(
    rules_db: PsycopgDatabase,
    source_id: str,
    open_time: int,
    history_bars: int,
    params: dict[str, int] | None = None,
) -> tuple[Decimal, ...]:
    """La fuente `source_id` con el spec REAL del registro, con el rol de reglas."""
    spec = SOURCE_MATERIALIZERS[source_id]
    if params is not None:
        assert isinstance(spec, ParameterizedMaterializer)
        spec = spec.with_params(
            {
                nombre: ScalarValue(scalar_type=ScalarType.INTEGER, integer_value=valor)
                for nombre, valor in params.items()
            }
        )
    with rules_db.transaction() as session:
        # El registro sirve el CARRIER (tuple[ScalarValue, ...], D1): se abre aqui para
        # que el resto del test siga comparando contra Decimal puro, sin cambiar
        # NINGUN valor -- es la misma envoltura que ya usa produccion (composition.py).
        return _scalars_to_decimals(
            spec.materialize(
                session, _EXCHANGE, _SYMBOL, _TF.value, open_time, history_bars
            )
        )


def _leer_ancla(
    rules_db: PsycopgDatabase,
    before_open_time: int,
    fast: int = MACD_FAST_DEFAULT,
    slow: int = MACD_SLOW_DEFAULT,
    signal: int = MACD_SIGNAL_DEFAULT,
) -> tuple[int, Decimal, Decimal, Decimal] | None:
    with rules_db.transaction() as session:
        return read_macd_snapshot_before(
            session, _EXCHANGE, _SYMBOL, _TF.value, fast, slow, signal, before_open_time
        )


def _contar_snapshots(
    rules_db: PsycopgDatabase,
    open_time: int,
    fast: int = MACD_FAST_DEFAULT,
    slow: int = MACD_SLOW_DEFAULT,
    signal: int = MACD_SIGNAL_DEFAULT,
) -> int:
    with rules_db.transaction() as session:
        fila = session.fetchone(
            "SELECT count(*) FROM macd_snapshot WHERE open_time = %s AND fast = %s "
            "AND slow = %s AND signal = %s",
            (open_time, fast, slow, signal),
        )
    assert fila is not None
    total = fila[0]
    assert isinstance(total, int)
    return total


def _borrar_snapshots(migrator_db: PsycopgDatabase) -> None:
    """Borra los snapshots con el rol de MIGRACIONES (el de reglas no tiene DELETE)."""
    with migrator_db.transaction() as session:
        session.execute("DELETE FROM macd_snapshot")


class TestMacdBootstrapDesdeElOrigen:
    """Sin ancla: la serie se siembra en el PRIMER cierre del historico.

    Como en ema y rsi y por el mismo motivo: el MACD no es anchor-independiente, asi que
    recortar el bootstrap al inicio de la ventana daria OTRA serie.
    """

    @pytest.mark.parametrize("source_id", _TODAS)
    def test_el_bootstrap_es_el_macd_puro_desde_el_origen(
        self,
        source_id: str,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_macd: None,
    ) -> None:
        _sembrar(persistir_vela)

        serie = _materializar(rules_db, source_id, _open_time(_BARRAS - 1), 4)

        assert serie == _macd_puro()[source_id][-4:]
        # Y NO es el cierre: si el materializador devolviera la ventana de precios tal
        # cual (el fallo mudo mas facil), esta linea lo caza.
        assert serie != _CIERRES[-4:]

    @pytest.mark.parametrize("source_id", _TODAS)
    def test_sin_warm_up_la_serie_tiene_tantos_valores_como_la_ventana(
        self,
        source_id: str,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_macd: None,
    ) -> None:
        # A diferencia del RSI, el MACD da valor DESDE LA BARRA 0: una ventana que
        # abarque el principio del historico sale completa, no recortada.
        _sembrar(persistir_vela)

        serie = _materializar(rules_db, source_id, _open_time(3), 10)

        assert len(serie) == 4  # solo hay 4 barras hasta la 3, y las 4 tienen valor.
        assert serie == _macd_puro()[source_id][:4]
        assert serie[0] == Decimal(0)  # invariante de semilla: macd[0] == 0.

    def test_las_tres_fuentes_dan_series_distintas(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_macd: None,
    ) -> None:
        # Comparten estado, pero NO salida. Si dos publicaran la misma proyeccion, el
        # catalogo ofreceria tres fuentes y el motor solo sabria servir dos.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        series = {sid: _materializar(rules_db, sid, ultimo, 4) for sid in _TODAS}

        assert len({tuple(s) for s in series.values()}) == 3
        for source_id, serie in series.items():
            assert serie == _macd_puro()[source_id][-4:]

    def test_el_bootstrap_persiste_el_estado_de_la_barra_vigente(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_macd: None,
    ) -> None:
        # Materializar DEJA el ancla para la ventana siguiente. Sin esta escritura, cada
        # tick recomputaria desde el origen y el replay acotado no existiria.
        _sembrar(persistir_vela)
        barra = _BARRAS - 1

        _materializar(rules_db, MACD_LINE_SOURCE_ID, _open_time(barra), 4)

        ancla = _leer_ancla(rules_db, _open_time(barra) + 1)
        assert ancla is not None
        ancla_open_time, ema_fast, ema_slow, ema_signal = ancla
        assert ancla_open_time == _open_time(barra)
        # El estado guardado son las EMAs INTERNAS, no las salidas: ema_fast - ema_slow
        # tiene que reproducir la line de esa barra.
        #
        # LA RESTA VA BAJO EL CONTEXTO PINNEADO, como en macd(). Con el contexto por
        # defecto de Decimal (prec 28) el resultado se redondea a 28 digitos
        # significativos y deja de coincidir con la line de prec 34, aunque el estado
        # persistido sea el correcto. Es el mismo motivo por el que el modulo puro pinea
        # el contexto en vez de confiar en el ambiente.
        with localcontext() as ctx:
            ctx.prec = 34
            ctx.rounding = ROUND_HALF_EVEN
            line_desde_el_estado = ema_fast - ema_slow
        assert line_desde_el_estado == _macd_puro()[MACD_LINE_SOURCE_ID][barra]
        assert ema_signal == _macd_puro()[MACD_SIGNAL_SOURCE_ID][barra]

    def test_las_tres_fuentes_dejan_el_MISMO_estado(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_macd: None,
    ) -> None:
        # Las tres materializan por separado (dispatch por SOURCE_ID) y las tres
        # escriben. Como el estado es el mismo, escriben la MISMA fila y el ON CONFLICT
        # DO NOTHING la absorbe: UNA fila, no tres, y ninguna se pisa con otra distinta.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        for source_id in _TODAS:
            _materializar(rules_db, source_id, ultimo, 4)

        assert _contar_snapshots(rules_db, ultimo) == 1

    def test_sin_velas_no_inventa_serie_ni_snapshot(
        self,
        rules_db: PsycopgDatabase,
        limpiar_macd: None,
    ) -> None:
        assert _materializar(rules_db, MACD_LINE_SOURCE_ID, _open_time(0), 5) == ()
        assert _leer_ancla(rules_db, _open_time(_BARRAS)) is None


class TestGateBitExactoDelReplay:
    """EL GATE (ADR-007): replay desde snapshot == macd() puro, bit a bit, en las TRES
    salidas y sea cual sea el snapshot desde el que se replaye.
    """

    @pytest.mark.parametrize("source_id", _TODAS)
    def test_el_replay_desde_el_snapshot_reproduce_el_macd_puro(
        self,
        source_id: str,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_macd: None,
    ) -> None:
        # Dos materializaciones en open_time DISTINTOS: la primera bootstrapea y deja el
        # ancla; la segunda YA replaya desde ella (su ventana empieza tras el ancla).
        _sembrar(persistir_vela)
        completo = _macd_puro()[source_id]

        primera = _materializar(rules_db, source_id, _open_time(20), 3)
        segunda = _materializar(rules_db, source_id, _open_time(29), 3)

        assert primera == completo[18:21]
        assert segunda == completo[27:30]
        # Bit a bit: igualdad de Decimal Y de representacion textual (dos Decimal
        # iguales pueden diferir en exponente; aqui no se admite ni eso).
        assert [str(v) for v in segunda] == [str(v) for v in completo[27:30]]

    @pytest.mark.parametrize("source_id", _TODAS)
    def test_dos_anclas_distintas_dan_la_misma_cola(
        self,
        source_id: str,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_macd: None,
    ) -> None:
        # EL GATE. El replay desde CUALQUIER snapshot valido -- y el bootstrap sin
        # ninguno -- reproduce la MISMA cola. Si esto fallara, el MACD valdria cosas
        # distintas segun donde se hubiera cortado el snapshot, y el snapshot seria una
        # fuente de deriva en vez de una optimizacion.
        _sembrar(persistir_vela)
        completo = _macd_puro()[source_id]
        ultimo = _open_time(29)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, source_id, _open_time(20), 3)  # ancla en la barra 20
        cola_ancla_20 = _materializar(rules_db, source_id, ultimo, 3)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, source_id, _open_time(25), 3)  # ancla en la barra 25
        cola_ancla_25 = _materializar(rules_db, source_id, ultimo, 3)

        _borrar_snapshots(migrator_db)
        cola_sin_ancla = _materializar(rules_db, source_id, ultimo, 3)  # bootstrap

        assert cola_ancla_20 == cola_ancla_25 == cola_sin_ancla
        assert cola_ancla_20 == completo[27:30]
        assert [str(v) for v in cola_ancla_20] == [str(v) for v in completo[27:30]]

    def test_el_estado_replayado_coincide_con_el_del_bootstrap(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_macd: None,
    ) -> None:
        # No basta con que coincidan las SERIES: el ESTADO persistido (las tres EMAs)
        # tambien tiene que ser identico, porque es lo que sembrara el siguiente replay.
        # Si divergiera, las series coincidirian hoy y se separarian una barra despues.
        _sembrar(persistir_vela)
        ultimo = _open_time(29)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, MACD_LINE_SOURCE_ID, _open_time(20), 3)
        _materializar(rules_db, MACD_LINE_SOURCE_ID, ultimo, 3)
        estado_replay = _leer_ancla(rules_db, ultimo + 1)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, MACD_LINE_SOURCE_ID, ultimo, 3)
        estado_bootstrap = _leer_ancla(rules_db, ultimo + 1)

        assert estado_replay is not None
        assert estado_bootstrap is not None
        assert estado_replay == estado_bootstrap
        # Bit a bit tambien en el estado: el exponente del Decimal sobrevive al viaje a
        # numeric y vuelve igual, que es lo que permite encadenar replays sin deriva.
        assert [str(v) for v in estado_replay[1:]] == [
            str(v) for v in estado_bootstrap[1:]
        ]

    @pytest.mark.parametrize("source_id", _TODAS)
    def test_re_materializar_la_misma_barra_no_cambia_nada(
        self,
        source_id: str,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_macd: None,
    ) -> None:
        # IDEMPOTENCIA (append-only, 0026): reevaluar la misma barra recomputa el MISMO
        # estado determinista, asi que el INSERT repetido es un duplicado exacto que el
        # ON CONFLICT DO NOTHING absorbe. Ni serie distinta, ni fila de mas.
        _sembrar(persistir_vela)
        ultimo = _open_time(29)

        una = _materializar(rules_db, source_id, ultimo, 3)
        ancla_tras_una = _leer_ancla(rules_db, ultimo + 1)
        otra = _materializar(rules_db, source_id, ultimo, 3)

        assert otra == una
        assert _contar_snapshots(rules_db, ultimo) == 1
        assert _leer_ancla(rules_db, ultimo + 1) == ancla_tras_una


class TestParamsPorParametroEfectivo:
    """MAT-05 Q2 end-to-end: los tres params de la regla CAMBIAN lo materializado."""

    @pytest.mark.parametrize("source_id", _TODAS)
    def test_otra_parametrizacion_da_otra_serie(
        self,
        source_id: str,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_macd: None,
    ) -> None:
        _sembrar(persistir_vela)
        ultimo = _open_time(29)

        por_defecto = _materializar(rules_db, source_id, ultimo, 3)
        otra = _materializar(
            rules_db, source_id, ultimo, 3, {"fast": 5, "slow": 35, "signal": 5}
        )

        assert por_defecto == _macd_puro()[source_id][-3:]
        assert otra == _macd_puro(5, 35, 5)[source_id][-3:]
        assert otra != por_defecto  # lo que prueba que los params VIAJAN.

    def test_cada_parametrizacion_tiene_su_propio_ancla(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_macd: None,
    ) -> None:
        # Los tres params entran en la PK de macd_snapshot (0026): dos series distintas
        # no pueden colisionar en la misma barra.
        _sembrar(persistir_vela)
        ultimo = _open_time(29)

        _materializar(rules_db, MACD_LINE_SOURCE_ID, ultimo, 3)
        _materializar(
            rules_db,
            MACD_LINE_SOURCE_ID,
            ultimo,
            3,
            {"fast": 5, "slow": 35, "signal": 5},
        )

        ancla_defecto = _leer_ancla(rules_db, ultimo + 1)
        ancla_otra = _leer_ancla(rules_db, ultimo + 1, 5, 35, 5)
        assert ancla_defecto is not None
        assert ancla_otra is not None
        assert ancla_defecto != ancla_otra
        assert _contar_snapshots(rules_db, ultimo) == 1
        assert _contar_snapshots(rules_db, ultimo, 5, 35, 5) == 1

    def test_el_replay_de_una_parametrizacion_no_usa_el_ancla_de_otra(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_macd: None,
    ) -> None:
        # Se deja SOLO el ancla por defecto en una barra intermedia y se materializa la
        # otra parametrizacion despues: si el lector ignorase los params, sembraria
        # macd(5,35,5) con el estado de macd(12,26,9) y la serie se apartaria del puro.
        _sembrar(persistir_vela)
        _materializar(rules_db, MACD_LINE_SOURCE_ID, _open_time(20), 3)

        otra = _materializar(
            rules_db,
            MACD_LINE_SOURCE_ID,
            _open_time(29),
            3,
            {"fast": 5, "slow": 35, "signal": 5},
        )

        assert otra == _macd_puro(5, 35, 5)[MACD_LINE_SOURCE_ID][-3:]

    def test_un_solo_param_override_no_toca_los_otros_dos(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_macd: None,
    ) -> None:
        # ADITIVIDAD end-to-end: pedir solo fast=5 deja slow y signal en sus defaults, y
        # la serie tiene que ser exactamente la de macd(5, 26, 9).
        _sembrar(persistir_vela)
        ultimo = _open_time(29)

        serie = _materializar(rules_db, MACD_LINE_SOURCE_ID, ultimo, 3, {"fast": 5})

        esperado = _macd_puro(5, MACD_SLOW_DEFAULT, MACD_SIGNAL_DEFAULT)
        assert serie == esperado[MACD_LINE_SOURCE_ID][-3:]
