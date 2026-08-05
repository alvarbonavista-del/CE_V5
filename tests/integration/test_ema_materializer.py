"""GATE ADR-007 de ema.value: replay desde snapshot == ema() puro, BIT A BIT.

Contra PostgreSQL REAL y con los DOS roles que manda la regla 5.20: las velas las
ESCRIBE el rol de INGESTA por el camino real (PostgresCandleWriter, fixture
persistir_vela) y la
serie la materializa el rol de REGLAS, el unico con el GRANT SELECT de la 0016 sobre
market_candle y con SELECT+INSERT sobre ema_snapshot (0023). Con un doble en memoria no
se probarian ni los grants ni el SQL del rango.

QUE ES EL GATE. ema.value es RECURSIVE: EMA[T] depende de EMA[T-1]. El materializador no
recomputa desde el origen en cada tick, sino que ANCLA el replay en un snapshot.
Eso solo es legitimo si la serie replayada es IDENTICA -- no aproximada: identica en el
ultimo digito -- a la que daria el ema() puro desde el origen, y si esa identidad NO
depende de donde se cortase el snapshot. Si fallara, el snapshot dejaria de ser una
optimizacion y pasaria a ser una fuente de deriva: el mismo EMA valdria cosas distintas
segun cuando se hubiera evaluado la regla.

Se usa el spec del REGISTRO (SOURCE_MATERIALIZERS), no uno de prueba: lo que valida
el cableado de produccion, componiendo las cuatro piezas -- ventana OHLCV, ancla de
snapshot, rango de cierres y ema_from_anchor -- mas la PERSISTENCIA del snapshot de la
barra vigente.
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
from ce_v5.infra.db.ema_snapshot import read_ema_snapshot_before
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.platform.rules.indicators.ema import (
    EMA_PERIOD_DEFAULT,
    EMA_SOURCE_ID,
    ema,
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

# Cierres FIJOS y todos DISTINTOS, y que suben y bajan: si el materializador devolviera
# la ventana desplazada, desordenada o el propio cierre en vez del EMA, con una serie
# monotona podria colar por casualidad; con esta no. Doce barras bastan: lo que se mide
# es la identidad del replay, no el regimen asintotico del filtro.
_CIERRES = tuple(
    Decimal(v)
    for v in (
        "20000.25",
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
    )
)
_BARRAS = len(_CIERRES)

Persistir = Callable[[CandlePayload, MarketCandleEventType, int], bool]


@pytest.fixture
def limpiar_ema(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """market_candle/ema_snapshot/outbox: sin FK a ningun tenant, se limpian a mano.

    Con el rol de MIGRACIONES porque el de reglas NO tiene DELETE sobre ema_snapshot
    (append-only, 0023): que el test necesite otro rol para borrar es justamente la
    prueba de que la rendija del motor es solo SELECT+INSERT.
    """

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_candle")
            session.execute("DELETE FROM ema_snapshot")
            session.execute("DELETE FROM outbox")

    _wipe()
    yield
    _wipe()


def _open_time(indice: int) -> int:
    return _OPEN + indice * _TF.duration_ms


def _vela(indice: int, close: Decimal) -> CandleClosedPayload:
    """La vela CERRADA numero `indice`, con su cierre propio.

    El rango OHLC se DERIVA del cierre (el contrato exige high >= open/close y
    low <= open/close). Lo que mide este fichero es el cierre; el resto del cuerpo solo
    tiene que ser coherente.
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


def _sembrar(persistir: Persistir) -> None:
    """Las _BARRAS velas cerradas del flujo, por el camino REAL del rol de ingesta."""
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


def _ema_puro(period: int = EMA_PERIOD_DEFAULT) -> tuple[Decimal, ...]:
    """La VERDAD contra la que se mide todo replay: ema() sobre la serie ENTERA."""
    return ema(list(_CIERRES), period)


def _materializar(
    rules_db: PsycopgDatabase,
    open_time: int,
    history_bars: int,
    period: int | None = None,
) -> tuple[Decimal, ...]:
    """ema.value con el spec REAL del registro, con el rol de reglas.

    period=None usa la instancia del registro (default 20); con period liga una copia
    por with_params, igual que hace el dispatch cuando el plan trae el override.
    """
    spec = SOURCE_MATERIALIZERS[EMA_SOURCE_ID]
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
    rules_db: PsycopgDatabase, before_open_time: int, period: int = EMA_PERIOD_DEFAULT
) -> tuple[int, Decimal] | None:
    with rules_db.transaction() as session:
        return read_ema_snapshot_before(
            session, _EXCHANGE, _SYMBOL, _TF.value, period, before_open_time
        )


def _contar_snapshots(
    rules_db: PsycopgDatabase, open_time: int, period: int = EMA_PERIOD_DEFAULT
) -> int:
    with rules_db.transaction() as session:
        fila = session.fetchone(
            "SELECT count(*) FROM ema_snapshot WHERE open_time = %s AND period = %s",
            (open_time, period),
        )
    assert fila is not None
    total = fila[0]
    assert isinstance(total, int)
    return total


def _borrar_snapshots(migrator_db: PsycopgDatabase) -> None:
    """Borra los snapshots con el rol de MIGRACIONES (el de reglas no tiene DELETE)."""
    with migrator_db.transaction() as session:
        session.execute("DELETE FROM ema_snapshot")


class TestEmaBootstrapDesdeElOrigen:
    """Sin ancla: la serie se siembra en el PRIMER cierre del historico, no en la
    ventana.

    Es la diferencia de fondo con el INTEGRATOR cvd, cuyo bootstrap SI puede arrancar en
    el inicio de la ventana (su valor absoluto es anchor-dependiente y su forma no). El
    EMA no tiene esa propiedad: EMA[0] == close[0] (invariante P08b-08) y cualquier otra
    semilla produce OTRA serie, no una version acotada de la misma.
    """

    def test_el_bootstrap_es_el_ema_puro_desde_el_origen(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_ema: None,
    ) -> None:
        _sembrar(persistir_vela)
        completo = _ema_puro()

        serie = _materializar(rules_db, _open_time(_BARRAS - 1), 4)

        assert serie == completo[-4:]
        # Y NO es el cierre: si el materializador devolviera la ventana de precios tal
        # cual (el fallo mudo mas facil), esta linea lo caza.
        assert serie != _CIERRES[-4:]

    def test_una_ventana_que_no_llega_al_origen_sigue_dando_el_ema_del_origen(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_ema: None,
    ) -> None:
        # LA condicion del bootstrap: aunque la regla pida 2 barras, el EMA de esas dos
        # barras es el que sale de arrastrar la recurrencia desde la barra 0. Sembrar en
        # el inicio de la ventana daria numeros distintos, y este test es lo que lo
        # impide.
        _sembrar(persistir_vela)
        completo = _ema_puro()

        serie = _materializar(rules_db, _open_time(_BARRAS - 1), 2)

        assert serie == completo[-2:]
        semilla_de_ventana = ema(list(_CIERRES[-2:]), EMA_PERIOD_DEFAULT)
        assert serie != semilla_de_ventana  # no se siembra en la ventana.

    def test_el_bootstrap_persiste_el_snapshot_de_la_barra_vigente(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_ema: None,
    ) -> None:
        # Materializar DEJA el ancla para la ventana siguiente. Sin esta escritura, cada
        # tick recomputaria desde el origen y el replay acotado no existiria.
        _sembrar(persistir_vela)
        completo = _ema_puro()
        barra = _BARRAS - 1

        _materializar(rules_db, _open_time(barra), 4)

        assert _leer_ancla(rules_db, _open_time(barra) + 1) == (
            _open_time(barra),
            completo[barra],
        )

    def test_el_snapshot_de_una_barra_intermedia_es_el_ema_de_esa_barra(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_ema: None,
    ) -> None:
        # La recurrencia es CAUSAL: el EMA de la barra k no depende de nada posterior,
        # asi que materializar hasta k deja un snapshot que vale exactamente lo que
        # el elemento k de la serie completa. Esa igualdad es la que hace que el ancla
        # sirva de semilla legitima.
        _sembrar(persistir_vela)
        completo = _ema_puro()

        _materializar(rules_db, _open_time(6), 3)

        assert _leer_ancla(rules_db, _open_time(7)) == (_open_time(6), completo[6])

    def test_sin_velas_no_inventa_serie_ni_snapshot(
        self,
        rules_db: PsycopgDatabase,
        limpiar_ema: None,
    ) -> None:
        # Sin base no hay EMA: tupla vacia (NOT_EVALUABLE, K3) y NINGUN snapshot.
        # Persistir el snapshot de una barra que no existe plantaria un ancla falsa que
        # envenenaria todos los replays posteriores.
        assert _materializar(rules_db, _open_time(0), 5) == ()
        assert _leer_ancla(rules_db, _open_time(_BARRAS)) is None


class TestGateBitExactoDelReplay:
    """EL GATE (ADR-007): replay desde snapshot == ema() puro, bit a bit, y sea cual sea
    el snapshot desde el que se replaye.
    """

    def test_el_replay_desde_el_snapshot_reproduce_el_ema_puro(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_ema: None,
    ) -> None:
        # Dos materializaciones en open_time DISTINTOS: la primera bootstrapea y deja el
        # ancla; la segunda YA replaya desde ella (su ventana empieza tras el ancla).
        # La serie de la segunda tiene que ser la cola exacta del ema() del origen.
        _sembrar(persistir_vela)
        completo = _ema_puro()

        primera = _materializar(rules_db, _open_time(6), 3)
        segunda = _materializar(rules_db, _open_time(11), 3)

        assert primera == completo[4:7]
        assert segunda == completo[9:12]
        # Bit a bit: igualdad de Decimal Y de representacion textual (dos Decimal
        # pueden diferir en exponente; aqui no se admite ni eso).
        assert [str(v) for v in segunda] == [str(v) for v in completo[9:12]]

    def test_dos_anclas_distintas_dan_la_misma_cola(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_ema: None,
    ) -> None:
        # 4.3 EL GATE. El replay desde CUALQUIER snapshot valido -- y el bootstrap sin
        # ninguno -- reproduce la MISMA cola. Si esto fallara, el EMA valdria cosas
        # distintas segun donde se hubiera cortado el snapshot, y el snapshot seria una
        # fuente de deriva en vez de una optimizacion.
        _sembrar(persistir_vela)
        completo = _ema_puro()
        ultimo = _open_time(11)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, _open_time(6), 3)  # ancla en la barra 6
        cola_ancla_6 = _materializar(rules_db, ultimo, 3)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, _open_time(8), 3)  # ancla en la barra 8
        cola_ancla_8 = _materializar(rules_db, ultimo, 3)

        _borrar_snapshots(migrator_db)
        cola_sin_ancla = _materializar(rules_db, ultimo, 3)  # bootstrap

        assert cola_ancla_6 == cola_ancla_8 == cola_sin_ancla
        assert cola_ancla_6 == completo[9:12]

    def test_re_materializar_la_misma_barra_no_cambia_nada(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_ema: None,
    ) -> None:
        # IDEMPOTENCIA (append-only, 0023): reevaluar la misma barra recomputa el MISMO
        # valor determinista, asi que el INSERT repetido es un duplicado exacto que el
        # ON CONFLICT DO NOTHING absorbe. Ni serie distinta, ni fila de mas.
        _sembrar(persistir_vela)
        ultimo = _open_time(11)

        una = _materializar(rules_db, ultimo, 3)
        ancla_tras_una = _leer_ancla(rules_db, ultimo + 1)
        otra = _materializar(rules_db, ultimo, 3)

        assert otra == una
        assert _contar_snapshots(rules_db, ultimo) == 1
        assert _leer_ancla(rules_db, ultimo + 1) == ancla_tras_una


class TestPeriodPorParametroEfectivo:
    """MAT-05 Q2 end-to-end: el period de la regla CAMBIA el hecho materializado.

    Contra PostgreSQL real y por el spec del REGISTRO (no uno de prueba). Si el param se
    perdiera por el camino, las dos series saldrian iguales y este test peta.
    """

    def test_period_nueve_da_otra_serie_y_es_el_ema_puro_de_nueve(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_ema: None,
    ) -> None:
        _sembrar(persistir_vela)
        ultimo = _open_time(11)

        por_defecto = _materializar(rules_db, ultimo, 3)
        de_nueve = _materializar(rules_db, ultimo, 3, period=9)

        assert por_defecto == _ema_puro()[9:12]
        assert de_nueve == _ema_puro(9)[9:12]
        assert de_nueve != por_defecto  # lo que prueba que el param VIAJA.

    def test_cada_period_tiene_su_propio_ancla(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_ema: None,
    ) -> None:
        # period entra en la PK de ema_snapshot (0023): dos series distintas no pueden
        # colisionar en la misma barra, y el ancla de ema(9) NUNCA siembra un ema(20).
        _sembrar(persistir_vela)
        ultimo = _open_time(11)

        _materializar(rules_db, ultimo, 3)
        _materializar(rules_db, ultimo, 3, period=9)

        ancla_20 = _leer_ancla(rules_db, ultimo + 1)
        ancla_9 = _leer_ancla(rules_db, ultimo + 1, period=9)
        assert ancla_20 == (ultimo, _ema_puro()[11])
        assert ancla_9 == (ultimo, _ema_puro(9)[11])
        assert ancla_20 != ancla_9
        assert _contar_snapshots(rules_db, ultimo) == 1
        assert _contar_snapshots(rules_db, ultimo, period=9) == 1

    def test_el_replay_de_un_period_no_usa_el_ancla_del_otro(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_ema: None,
    ) -> None:
        # Se deja SOLO el ancla de period=20 en una barra intermedia y se materializa
        # ema(9) despues: si el lector ignorase el period, sembraria ema(9) con el valor
        # de ema(20) y la serie se apartaria del ema() puro de 9.
        _sembrar(persistir_vela)
        _materializar(rules_db, _open_time(6), 3)  # ancla SOLO de period=20

        de_nueve = _materializar(rules_db, _open_time(11), 3, period=9)

        assert de_nueve == _ema_puro(9)[9:12]
