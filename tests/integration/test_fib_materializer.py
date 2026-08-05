"""GATE ADR-007 de fib.*: replay del grid desde snapshot == bootstrap, BIT A BIT.

Contra PostgreSQL REAL y con los DOS roles que manda la regla 5.20: las velas las
ESCRIBE el rol de INGESTA por el camino real (PostgresCandleWriter, fixture
persistir_vela) y las series las materializa el rol de REGLAS, el unico con el GRANT
SELECT de la 0016 sobre market_candle y con SELECT+INSERT sobre fib_range_snapshot
(0027).

QUE ES EL GATE, Y POR QUE AQUI APRIETA MAS. El rango del grid es PEGAJOSO: solo se mueve
cuando un swing lo supera por >= 0.414 veces su tamano, asi que una barra que no lo
mueve lo CONSERVA y el rango de hoy puede venir de un pivote de hace cientos de barras.
Esa memoria no tiene cota. Si el replay desde snapshot no reprodujera EXACTAMENTE el
rango del bootstrap, la divergencia no se veria en la barra del corte -- se veria mucho
despues, la primera vez que un swing cayera entre los dos umbrales. Por eso el gate
compara las series Y el ESTADO persistido.

LO QUE ESTE FICHERO ANADE SOBRE LOS ANTERIORES. Es el unico materializador que consume
DOS fuentes DERIVADAS (swing.high/swing.low) ademas del cierre, asi que aqui se prueba
tambien la ALINEACION: que el grid empareja cada barra con SU par de pivotes. Y que el
strength efectivo viaja hasta esas series, no solo hasta el snapshot.

Se usa el spec del REGISTRO (SOURCE_MATERIALIZERS), no uno de prueba.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest

from ce_v5.entrypoints.worker_rules.materializers import (
    SOURCE_MATERIALIZERS,
    ParameterizedMaterializer,
    _scalars_to_decimals,
)
from ce_v5.infra.db.fib_range_snapshot import read_fib_range_snapshot_before
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.platform.rules.indicators.fib import (
    FIB_LEVEL_PCT_SOURCE_ID,
    FIB_NEAREST_LEVEL_SOURCE_ID,
    FibOutput,
    fib_output,
)
from ce_v5.platform.rules.indicators.swing import SWING_STRENGTH_DEFAULT
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

# CUANTAS BARRAS. swing.* es WINDOWED con ventana 100: no emite hasta tener 100 cierres,
# asi que por debajo de eso el grid no tiene pivotes con los que arrancar. Con 160
# barras quedan 61 con valor, suficiente para anclar en varios puntos y para que la
# histeresis tenga recorrido.
_BARRAS = 160
_SWING_WINDOW = 100
# Primera barra (indice) en la que swing emite y por tanto el grid puede arrancar.
_PRIMERA_CON_SWING = _SWING_WINDOW - 1
_TODAS = (FIB_NEAREST_LEVEL_SOURCE_ID, FIB_LEVEL_PCT_SOURCE_ID)

Persistir = Callable[[CandlePayload, MarketCandleEventType, int], bool]


# Forma de la serie: SIERRA de tramos monotonos de 5 barras (5 arriba, 5 abajo) sobre
# una tendencia alcista suave. Los tramos de 5 son deliberados y NO son decorativos:
#
# - Un zigzag de 1 barra NO habria servido. symmetric_pivots exige `strength` barras
#   monotonas a cada lado del pivote, asi que con dientes de una barra no se confirma
#   ninguno NI con strength=2 NI con 7, y swing cae en los dos casos a su fallback
#   max/min de ventana: las dos parametrizaciones darian la MISMA serie y el test del
#   param efectivo pasaria sin probar nada.
# - Con tramos de 5, strength=2 SI confirma los pivotes (2 <= 5) y strength=7 NO
#   (7 > 5, cae al fallback). Las dos series difieren de verdad, que es lo que hace
#   falta para demostrar que el strength VIAJA hasta swing.
# - La tendencia y el paso estan calibrados para que la histeresis se mueva un par de
#   veces en las 160 barras: con un rango congelado el GATE pasaria sin ejercitar la
#   recursion, que es justo lo que tiene que vigilar.
_SUBIDA = 5
_PERIODO = 10
_PASO = Decimal(60)
_TENDENCIA = Decimal(8)


def _cierre(indice: int) -> Decimal:
    """Cierre de la barra `indice`: sierra de tramos de 5 sobre tendencia alcista."""
    valor = Decimal(20000)
    for barra in range(indice + 1):
        arriba = (barra % _PERIODO) < _SUBIDA
        valor = valor + _PASO if arriba else valor - _PASO
    return valor + _TENDENCIA * Decimal(indice)


_CIERRES = tuple(_cierre(i) for i in range(_BARRAS))


@pytest.fixture
def limpiar_fib(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """market_candle/fib_range_snapshot/outbox: sin FK a tenant, se limpian a mano.

    Con el rol de MIGRACIONES porque el de reglas NO tiene DELETE sobre
    fib_range_snapshot (append-only, 0027): que el test necesite otro rol para borrar es
    justamente la prueba de que la rendija del motor es solo SELECT+INSERT.
    """

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_candle")
            session.execute("DELETE FROM fib_range_snapshot")
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
    for indice, close in enumerate(_CIERRES):
        assert (
            persistir(
                _vela(indice, close),
                MarketCandleEventType.CANDLE_CLOSED,
                _OPEN + indice,
            )
            is True
        )


def _materializar(
    rules_db: PsycopgDatabase,
    source_id: str,
    open_time: int,
    history_bars: int,
    strength: int | None = None,
) -> tuple[Decimal, ...]:
    """La fuente `source_id` con el spec REAL del registro, con el rol de reglas."""
    spec = SOURCE_MATERIALIZERS[source_id]
    if strength is not None:
        assert isinstance(spec, ParameterizedMaterializer)
        spec = spec.with_params(
            {
                "strength": ScalarValue(
                    scalar_type=ScalarType.INTEGER, integer_value=strength
                )
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
    strength: int = SWING_STRENGTH_DEFAULT,
) -> tuple[int, Decimal, Decimal] | None:
    with rules_db.transaction() as session:
        return read_fib_range_snapshot_before(
            session, _EXCHANGE, _SYMBOL, _TF.value, strength, before_open_time
        )


def _contar_snapshots(
    rules_db: PsycopgDatabase,
    open_time: int,
    strength: int = SWING_STRENGTH_DEFAULT,
) -> int:
    with rules_db.transaction() as session:
        fila = session.fetchone(
            "SELECT count(*) FROM fib_range_snapshot WHERE open_time = %s "
            "AND strength = %s",
            (open_time, strength),
        )
    assert fila is not None
    total = fila[0]
    assert isinstance(total, int)
    return total


def _borrar_snapshots(migrator_db: PsycopgDatabase) -> None:
    """Borra los snapshots con el rol de MIGRACIONES (el de reglas no tiene DELETE)."""
    with migrator_db.transaction() as session:
        session.execute("DELETE FROM fib_range_snapshot")


class TestFibBootstrapDesdeElOrigen:
    """Sin ancla: el rango se siembra en el primer par de pivotes que exista.

    No es una eleccion de diseno sino una necesidad: el rango es pegajoso, asi que
    recortar el bootstrap a la ventana daria OTRO rango.
    """

    @pytest.mark.parametrize("source_id", _TODAS)
    def test_el_bootstrap_emite_serie_y_no_es_el_cierre(
        self,
        source_id: str,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_fib: None,
    ) -> None:
        _sembrar(persistir_vela)

        serie = _materializar(rules_db, source_id, _open_time(_BARRAS - 1), 4)

        assert len(serie) == 4
        # Si el materializador devolviera la ventana de precios tal cual (el fallo mudo
        # mas facil), esta linea lo caza.
        assert serie != _CIERRES[-4:]

    @pytest.mark.parametrize("source_id", _TODAS)
    def test_la_serie_sale_del_rango_persistido_y_del_cierre(
        self,
        source_id: str,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_fib: None,
    ) -> None:
        # El ULTIMO valor de la serie tiene que ser exactamente la proyeccion pura del
        # rango que quedo persistido sobre el cierre de esa barra. Ata las tres piezas
        # -- estado, funcion pura y salida -- en una sola asercion.
        _sembrar(persistir_vela)
        ultimo = _BARRAS - 1

        serie = _materializar(rules_db, source_id, _open_time(ultimo), 3)
        ancla = _leer_ancla(rules_db, _open_time(ultimo) + 1)

        assert ancla is not None
        _, range_high, range_low = ancla
        salida = (
            FibOutput.NEAREST_LEVEL
            if source_id == FIB_NEAREST_LEVEL_SOURCE_ID
            else FibOutput.LEVEL_PCT
        )
        assert serie[-1] == fib_output(range_high, range_low, _CIERRES[ultimo], salida)

    def test_las_dos_fuentes_dan_series_distintas(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_fib: None,
    ) -> None:
        # Comparten rango, pero NO salida: una da un PRECIO y la otra un PORCENTAJE.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        nivel = _materializar(rules_db, FIB_NEAREST_LEVEL_SOURCE_ID, ultimo, 4)
        pct = _materializar(rules_db, FIB_LEVEL_PCT_SOURCE_ID, ultimo, 4)

        assert nivel != pct

    def test_las_dos_fuentes_dejan_el_mismo_rango(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_fib: None,
    ) -> None:
        # Las dos materializan por separado (dispatch por SOURCE_ID) y las dos escriben.
        # Como el estado es el mismo, escriben la MISMA fila y el ON CONFLICT DO NOTHING
        # la absorbe: UNA fila, no dos.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        for source_id in _TODAS:
            _materializar(rules_db, source_id, ultimo, 4)

        assert _contar_snapshots(rules_db, ultimo) == 1

    def test_el_rango_persistido_es_coherente(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_fib: None,
    ) -> None:
        _sembrar(persistir_vela)
        barra = _BARRAS - 1

        _materializar(rules_db, FIB_NEAREST_LEVEL_SOURCE_ID, _open_time(barra), 4)

        ancla = _leer_ancla(rules_db, _open_time(barra) + 1)
        assert ancla is not None
        ancla_open_time, range_high, range_low = ancla
        assert ancla_open_time == _open_time(barra)
        # Rango no degenerado con estos datos: la histeresis se movio de verdad.
        assert range_high > range_low

    def test_sin_velas_no_inventa_serie_ni_snapshot(
        self,
        rules_db: PsycopgDatabase,
        limpiar_fib: None,
    ) -> None:
        assert (
            _materializar(rules_db, FIB_NEAREST_LEVEL_SOURCE_ID, _open_time(0), 5) == ()
        )
        assert _leer_ancla(rules_db, _open_time(_BARRAS)) is None


class TestWarmUpDeSwing:
    """El grid no puede arrancar antes que swing: serie MAS CORTA, nunca rellenada.

    swing.* es WINDOWED con ventana 100. Antes de esa barra no hay pivotes y por tanto
    no hay rango que tender. El materializador devuelve menos valores -- como el warm-up
    del RSI -- y el evaluador lo trata como NOT_EVALUABLE (K3).
    """

    def test_una_ventana_entera_en_warm_up_da_serie_vacia_y_ningun_snapshot(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_fib: None,
    ) -> None:
        # Barra 50: swing todavia no emite (necesita 100 cierres). Sin pivotes no hay
        # rango, y NO se persiste nada: un rango inventado seria un ancla falsa que
        # envenenaria todos los replays posteriores.
        _sembrar(persistir_vela)

        assert (
            _materializar(rules_db, FIB_NEAREST_LEVEL_SOURCE_ID, _open_time(50), 5)
            == ()
        )
        assert _leer_ancla(rules_db, _open_time(_BARRAS)) is None

    def test_una_ventana_a_caballo_del_warm_up_sale_recortada(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_fib: None,
    ) -> None:
        # Se piden 10 barras terminando en la 102, pero solo las 99, 100, 101 y 102
        # tienen swing: salen CUATRO valores, no diez.
        _sembrar(persistir_vela)

        serie = _materializar(
            rules_db, FIB_NEAREST_LEVEL_SOURCE_ID, _open_time(102), 10
        )

        assert len(serie) == 102 - _PRIMERA_CON_SWING + 1 == 4

    def test_la_barra_exacta_del_arranque_ya_emite_un_valor(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_fib: None,
    ) -> None:
        # Frontera: en la barra 99 swing emite su primer valor y el grid ya puede
        # tender.
        _sembrar(persistir_vela)

        serie = _materializar(
            rules_db, FIB_NEAREST_LEVEL_SOURCE_ID, _open_time(_PRIMERA_CON_SWING), 5
        )

        assert len(serie) == 1


class TestGateBitExactoDelReplay:
    """EL GATE (ADR-007): replay desde snapshot == bootstrap, en las DOS salidas y sea
    cual sea el snapshot desde el que se replaye.
    """

    @pytest.mark.parametrize("source_id", _TODAS)
    def test_dos_anclas_distintas_dan_la_misma_cola(
        self,
        source_id: str,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_fib: None,
    ) -> None:
        # El replay desde CUALQUIER snapshot valido -- y el bootstrap sin ninguno --
        # reproduce la MISMA cola. Con un rango pegajoso esto es mas exigente que en los
        # indicadores anteriores: basta con que el estado replayado difiera un tick para
        # que un swing futuro caiga del otro lado del umbral y las series se separen.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, source_id, _open_time(120), 3)  # ancla en la barra 120
        cola_ancla_120 = _materializar(rules_db, source_id, ultimo, 3)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, source_id, _open_time(145), 3)  # ancla en la barra 145
        cola_ancla_145 = _materializar(rules_db, source_id, ultimo, 3)

        _borrar_snapshots(migrator_db)
        cola_sin_ancla = _materializar(rules_db, source_id, ultimo, 3)  # bootstrap

        assert cola_ancla_120 == cola_ancla_145 == cola_sin_ancla
        # Bit a bit: igualdad de Decimal Y de representacion textual (dos Decimal
        # iguales pueden diferir en exponente; aqui no se admite ni eso).
        assert [str(v) for v in cola_ancla_120] == [str(v) for v in cola_sin_ancla]

    def test_el_rango_replayado_coincide_con_el_del_bootstrap(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_fib: None,
    ) -> None:
        # No basta con que coincidan las SERIES: el RANGO persistido tambien tiene que
        # ser identico, porque es lo que sembrara el siguiente replay. Con memoria
        # ilimitada, un rango que difiera hoy puede no notarse hasta muchas barras
        # despues -- exactamente la deriva silenciosa que el snapshot podria introducir.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, FIB_NEAREST_LEVEL_SOURCE_ID, _open_time(120), 3)
        _materializar(rules_db, FIB_NEAREST_LEVEL_SOURCE_ID, ultimo, 3)
        rango_replay = _leer_ancla(rules_db, ultimo + 1)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, FIB_NEAREST_LEVEL_SOURCE_ID, ultimo, 3)
        rango_bootstrap = _leer_ancla(rules_db, ultimo + 1)

        assert rango_replay is not None
        assert rango_bootstrap is not None
        assert rango_replay == rango_bootstrap
        assert [str(v) for v in rango_replay[1:]] == [
            str(v) for v in rango_bootstrap[1:]
        ]

    def test_una_cadena_de_replays_no_deriva(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_fib: None,
    ) -> None:
        # El caso REAL del worker: no una materializacion aislada, sino barra tras
        # barra, cada una anclando en el snapshot que dejo la anterior. Tras encadenar
        # decenas de replays el rango tiene que seguir siendo el del bootstrap de una
        # sola pasada. Es donde una deriva de la histeresis se acumularia y se veria.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        _borrar_snapshots(migrator_db)
        for barra in range(_PRIMERA_CON_SWING, _BARRAS):
            _materializar(rules_db, FIB_NEAREST_LEVEL_SOURCE_ID, _open_time(barra), 1)
        cadena = _materializar(rules_db, FIB_NEAREST_LEVEL_SOURCE_ID, ultimo, 5)
        rango_cadena = _leer_ancla(rules_db, ultimo + 1)

        _borrar_snapshots(migrator_db)
        una_pasada = _materializar(rules_db, FIB_NEAREST_LEVEL_SOURCE_ID, ultimo, 5)
        rango_una_pasada = _leer_ancla(rules_db, ultimo + 1)

        assert cadena == una_pasada
        assert rango_cadena == rango_una_pasada

    @pytest.mark.parametrize("source_id", _TODAS)
    def test_re_materializar_la_misma_barra_no_cambia_nada(
        self,
        source_id: str,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_fib: None,
    ) -> None:
        # IDEMPOTENCIA (append-only, 0027): reevaluar la misma barra recomputa el MISMO
        # rango determinista, asi que el INSERT repetido es un duplicado exacto que el
        # ON CONFLICT DO NOTHING absorbe.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        una = _materializar(rules_db, source_id, ultimo, 3)
        ancla_tras_una = _leer_ancla(rules_db, ultimo + 1)
        otra = _materializar(rules_db, source_id, ultimo, 3)

        assert otra == una
        assert _contar_snapshots(rules_db, ultimo) == 1
        assert _leer_ancla(rules_db, ultimo + 1) == ancla_tras_una


class TestStrengthPorParametroEfectivo:
    """MAT-05 Q2 end-to-end: el strength CAMBIA los pivotes y por tanto el rango.

    Es el unico caso del catalogo en que el param no solo configura al materializador:
    VIAJA a las fuentes que consume (swing.high/swing.low). Si se perdiera por el
    camino, el grid se tenderia sobre pivotes de otra fuerza sin que nadie se enterase.
    """

    def test_otro_strength_da_otro_rango(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_fib: None,
    ) -> None:
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        _materializar(rules_db, FIB_NEAREST_LEVEL_SOURCE_ID, ultimo, 3)
        _materializar(rules_db, FIB_NEAREST_LEVEL_SOURCE_ID, ultimo, 3, strength=7)

        ancla_defecto = _leer_ancla(rules_db, ultimo + 1)
        ancla_siete = _leer_ancla(rules_db, ultimo + 1, strength=7)
        assert ancla_defecto is not None
        assert ancla_siete is not None
        # strength entra en la PK: cada parametrizacion tiene su propia fila.
        assert _contar_snapshots(rules_db, ultimo) == 1
        assert _contar_snapshots(rules_db, ultimo, strength=7) == 1
        # Y los rangos son distintos: los pivotes de fuerza 7 no son los de fuerza 2.
        assert (ancla_defecto[1], ancla_defecto[2]) != (ancla_siete[1], ancla_siete[2])

    def test_el_replay_de_un_strength_no_usa_el_ancla_del_otro(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_fib: None,
    ) -> None:
        # Se deja SOLO el ancla de strength=2 en una barra intermedia y se materializa
        # strength=7 despues: si el lector ignorase el param, sembraria el grid de 7 con
        # el rango de 2 y la serie se apartaria de su bootstrap.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, FIB_NEAREST_LEVEL_SOURCE_ID, _open_time(120), 3)
        con_ancla_ajena = _materializar(
            rules_db, FIB_NEAREST_LEVEL_SOURCE_ID, ultimo, 3, strength=7
        )

        _borrar_snapshots(migrator_db)
        limpio = _materializar(
            rules_db, FIB_NEAREST_LEVEL_SOURCE_ID, ultimo, 3, strength=7
        )

        assert con_ancla_ajena == limpio
