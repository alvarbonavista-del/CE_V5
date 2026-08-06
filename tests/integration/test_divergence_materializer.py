"""GATE ADR-007 de divergence.*: replay desde snapshot == bootstrap, BIT A BIT.

Contra PostgreSQL REAL y con los DOS roles que manda la regla 5.20: las velas las
ESCRIBE el rol de INGESTA por el camino real (PostgresCandleWriter, fixture
persistir_vela) y las series las materializa el rol de REGLAS, el unico con el GRANT
SELECT de la 0016 sobre market_candle y con SELECT+INSERT sobre divergence_snapshot
(0028).

QUE ES EL GATE, Y POR QUE AQUI APRIETA DE OTRA MANERA. El estado no es un numero que se
suaviza: es el ULTIMO PIVOTE DE CADA LADO. Si el replay reanudara desde el pivote
equivocado -- o se saltara uno --, la divergencia no saldria "un poco distinta": saldria
emparejada contra otra barra, con otro kind, o no saldria. Y no se veria en la barra del
corte, sino la siguiente vez que un pivote cerrara par. Por eso el gate compara la SERIE
DENSA y el ESTADO persistido.

LO QUE ESTE FICHERO ANADE SOBRE LOS ANTERIORES:
  - Es la primera fuente BOOLEAN del catalogo que recorre el pipeline, y la primera que
    proyecta DENSO ('none'/false por barra) un fenomeno DISPERSO.
  - Es la primera que se replaya sobre las series de HIGH y de LOW (read_ohlcv_range),
    no sobre los cierres.
  - Su tramo de replay no arranca en el ancla sino `strength` barras antes del pivote
    guardado mas antiguo: el contexto izquierdo que symmetric_pivots exige. Si ese
    arranque estuviera mal calculado, el GATE lo canta.

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
)
from ce_v5.infra.db.divergence_snapshot import (
    DivergenceSnapshot,
    read_divergence_snapshot_before,
)
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.platform.rules.indicators.divergence import (
    DIVERGENCE_HIDDEN_BEAR_SOURCE_ID,
    DIVERGENCE_HIDDEN_BULL_SOURCE_ID,
    DIVERGENCE_KIND_NONE,
    DIVERGENCE_KIND_SOURCE_ID,
    DIVERGENCE_REGULAR_BEAR_SOURCE_ID,
    DIVERGENCE_REGULAR_BULL_SOURCE_ID,
    DivergenceKind,
    detect_divergences,
    divergence_kind_token,
)
from ce_v5.platform.rules.indicators.rsi import RSI_PERIOD_DEFAULT
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

# CUANTAS BARRAS. El warm-up del RSI se come las 14 primeras y los pivotes necesitan
# recorrido para que la cadena tenga varios pares por lado. Con 260 barras el fixture
# produce los CUATRO tipos de divergencia (2 regular_bear, 4 hidden_bear, 3
# regular_bull,
# 1 hidden_bull), que es lo que hace que el golden denso pruebe algo.
_BARRAS = 260
_SEED = 1234567

_FLAGS = (
    (DIVERGENCE_REGULAR_BULL_SOURCE_ID, DivergenceKind.REGULAR_BULL),
    (DIVERGENCE_REGULAR_BEAR_SOURCE_ID, DivergenceKind.REGULAR_BEAR),
    (DIVERGENCE_HIDDEN_BULL_SOURCE_ID, DivergenceKind.HIDDEN_BULL),
    (DIVERGENCE_HIDDEN_BEAR_SOURCE_ID, DivergenceKind.HIDDEN_BEAR),
)
_TODAS = (DIVERGENCE_KIND_SOURCE_ID, *[sid for sid, _ in _FLAGS])

Persistir = Callable[[CandlePayload, MarketCandleEventType, int], bool]


def _ohlc() -> tuple[
    tuple[Decimal, ...], tuple[Decimal, ...], tuple[Decimal, ...], tuple[Decimal, ...]
]:
    """OHLC deterministico (LCG) con velas COHERENTES: low <= open,close <= high.

    Los desplazamientos de open y close son FRACCIONES del spread de la vela, no
    cantidades sueltas: asi la vela siempre es valida (el contrato la rechazaria si no)
    y
    a la vez los tres precios se mueven de forma independiente. Eso ultimo importa aqui
    mas que en ningun otro test del catalogo, porque divergence saca sus pivotes de las
    series de HIGH y de LOW y su RSI de la de CLOSE: si los tres fueran el mismo numero
    desplazado, un materializador que leyera la serie equivocada pasaria el test.
    """
    x = _SEED
    opens: list[Decimal] = []
    highs: list[Decimal] = []
    lows: list[Decimal] = []
    closes: list[Decimal] = []
    for _ in range(_BARRAS):
        x = (1103515245 * x + 12345) % 2147483648
        mid = Decimal(x % 500000) / Decimal(1000)
        spread = Decimal(1) + Decimal((x // 500000) % 300) / Decimal(100)
        c_off = spread * (Decimal((x // 7) % 201) - Decimal(100)) / Decimal(100)
        o_off = spread * (Decimal((x // 13) % 201) - Decimal(100)) / Decimal(100)
        opens.append(mid + o_off)
        highs.append(mid + spread)
        lows.append(mid - spread)
        closes.append(mid + c_off)
    return (tuple(opens), tuple(highs), tuple(lows), tuple(closes))


_OPENS, _HIGHS, _LOWS, _CLOSES = _ohlc()

# EL REFERENTE del golden denso: la funcion pura sobre la MISMA serie, sin BD ni replay
# de por medio. El materializador tiene que reproducirlo barra a barra.
_EVENTOS = detect_divergences(
    _HIGHS, _LOWS, _CLOSES, SWING_STRENGTH_DEFAULT, RSI_PERIOD_DEFAULT
)


def _kinds_por_barra() -> dict[int, set[DivergenceKind]]:
    por_barra: dict[int, set[DivergenceKind]] = {}
    for evento in _EVENTOS:
        por_barra.setdefault(evento.index, set()).add(evento.kind)
    return por_barra


_KINDS = _kinds_por_barra()


@pytest.fixture
def limpiar_divergence(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """market_candle/divergence_snapshot/rsi_snapshot/outbox: sin FK a tenant, a mano.

    Con el rol de MIGRACIONES porque el de reglas NO tiene DELETE sobre
    divergence_snapshot (append-only, 0028): que el test necesite otro rol para borrar
    es
    justamente la prueba de que la rendija del motor es solo SELECT+INSERT.

    rsi_snapshot entra en la limpieza porque divergence CONSUME rsi.value por el
    registro
    y ese camino tambien escribe su estado: dejarlo sucio entre tests haria que el RSI
    de
    una prueba sembrara la siguiente.
    """

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_candle")
            session.execute("DELETE FROM divergence_snapshot")
            session.execute("DELETE FROM rsi_snapshot")
            session.execute("DELETE FROM outbox")

    _wipe()
    yield
    _wipe()


def _open_time(indice: int) -> int:
    return _OPEN + indice * _TF.duration_ms


def _vela(indice: int) -> CandleClosedPayload:
    open_time = _open_time(indice)
    return CandleClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange=_EXCHANGE,
        market_type=MarketType.SPOT,
        symbol=_SYMBOL,
        timeframe=_TF,
        open_time=open_time,
        close_time=open_time + _TF.duration_ms,
        open=_OPENS[indice],
        high=_HIGHS[indice],
        low=_LOWS[indice],
        close=_CLOSES[indice],
        volume=Decimal("12.5"),
    )


def _sembrar(persistir: Persistir) -> None:
    """Las _BARRAS velas cerradas, por el camino REAL del rol de ingesta."""
    for indice in range(_BARRAS):
        assert (
            persistir(
                _vela(indice), MarketCandleEventType.CANDLE_CLOSED, _OPEN + indice
            )
            is True
        )


def _materializar(
    rules_db: PsycopgDatabase,
    source_id: str,
    open_time: int,
    history_bars: int,
    strength: int | None = None,
    rsi_period: int | None = None,
) -> tuple[ScalarValue, ...]:
    """La fuente `source_id` con el spec REAL del registro, con el rol de reglas.

    NO se abre el carrier: divergence.* no sirve Decimal, asi que el ScalarValue se mira
    tal cual llega -- que es justamente lo que hay que comprobar (el tipo viaja EN el
    dato, D1).
    """
    spec = SOURCE_MATERIALIZERS[source_id]
    overrides: dict[str, ScalarValue] = {}
    if strength is not None:
        overrides["strength"] = ScalarValue(
            scalar_type=ScalarType.INTEGER, integer_value=strength
        )
    if rsi_period is not None:
        overrides["rsi_period"] = ScalarValue(
            scalar_type=ScalarType.INTEGER, integer_value=rsi_period
        )
    if overrides:
        assert isinstance(spec, ParameterizedMaterializer)
        spec = spec.with_params(overrides)
    with rules_db.transaction() as session:
        return spec.materialize(
            session, _EXCHANGE, _SYMBOL, _TF.value, open_time, history_bars
        )


def _leer_ancla(
    rules_db: PsycopgDatabase,
    before_open_time: int,
    strength: int = SWING_STRENGTH_DEFAULT,
    rsi_period: int = RSI_PERIOD_DEFAULT,
) -> DivergenceSnapshot | None:
    with rules_db.transaction() as session:
        return read_divergence_snapshot_before(
            session,
            _EXCHANGE,
            _SYMBOL,
            _TF.value,
            strength,
            rsi_period,
            before_open_time,
        )


def _contar_snapshots(
    rules_db: PsycopgDatabase,
    open_time: int,
    strength: int = SWING_STRENGTH_DEFAULT,
    rsi_period: int = RSI_PERIOD_DEFAULT,
) -> int:
    with rules_db.transaction() as session:
        fila = session.fetchone(
            "SELECT count(*) FROM divergence_snapshot WHERE open_time = %s "
            "AND strength = %s AND rsi_period = %s",
            (open_time, strength, rsi_period),
        )
    assert fila is not None
    total = fila[0]
    assert isinstance(total, int)
    return total


def _borrar_snapshots(migrator_db: PsycopgDatabase) -> None:
    """Borra los snapshots con el rol de MIGRACIONES (el de reglas no tiene DELETE)."""
    with migrator_db.transaction() as session:
        session.execute("DELETE FROM divergence_snapshot")


class TestElFixtureMuerde:
    """Control del propio fixture: sin esto, los golden podrian pasar vacios."""

    def test_el_fixture_produce_los_cuatro_tipos_de_divergencia(self) -> None:
        assert {e.kind for e in _EVENTOS} == set(DivergenceKind)

    def test_hay_al_menos_un_alcista_y_un_bajista(self) -> None:
        kinds = {e.kind for e in _EVENTOS}
        assert kinds & {DivergenceKind.REGULAR_BULL, DivergenceKind.HIDDEN_BULL}
        assert kinds & {DivergenceKind.REGULAR_BEAR, DivergenceKind.HIDDEN_BEAR}

    def test_los_eventos_son_DISPERSOS(self) -> None:
        # La razon de ser de la proyeccion densa: la inmensa mayoria de las barras no
        # tiene nada. Si el fixture tuviera evento en casi todas, un materializador que
        # devolviera siempre el mismo token pasaria el golden.
        assert 0 < len(_KINDS) < _BARRAS // 10

    def test_algun_par_salta_mas_barras_que_cualquier_ventana_corta(self) -> None:
        # EL ARGUMENTO DEL RECURSIVE, medido sobre el propio fixture: hay pares cuyos
        # dos
        # pivotes distan varias barras. Una ventana acotada que no los cubriera perderia
        # el evento o lo emparejaria contra otro pivote.
        assert max(e.index - e.prev_index for e in _EVENTOS) >= 5


class TestGoldenDenso:
    """La serie servida, barra a barra, contra la funcion pura sobre los mismos
    datos."""

    def test_kind_reproduce_los_eventos_barra_a_barra(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_divergence: None,
    ) -> None:
        _sembrar(persistir_vela)

        serie = _materializar(
            rules_db, DIVERGENCE_KIND_SOURCE_ID, _open_time(_BARRAS - 1), _BARRAS
        )

        assert len(serie) == _BARRAS
        esperado = tuple(
            divergence_kind_token(_KINDS.get(i, set())) for i in range(_BARRAS)
        )
        assert tuple(v.string_value for v in serie) == esperado

    @pytest.mark.parametrize(("source_id", "kind"), _FLAGS)
    def test_cada_flag_reproduce_solo_su_tipo(
        self,
        source_id: str,
        kind: DivergenceKind,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_divergence: None,
    ) -> None:
        _sembrar(persistir_vela)

        serie = _materializar(rules_db, source_id, _open_time(_BARRAS - 1), _BARRAS)

        esperado = tuple(kind in _KINDS.get(i, set()) for i in range(_BARRAS))
        assert tuple(v.boolean_value for v in serie) == esperado
        # Muerde: un flag constante (todo False) pasaria una igualdad mal escrita.
        assert any(esperado)

    def test_las_barras_sin_evento_son_none_y_false(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_divergence: None,
    ) -> None:
        # La proyeccion DENSA de un fenomeno DISPERSO: la ausencia se sirve, no se
        # calla.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        kinds = _materializar(rules_db, DIVERGENCE_KIND_SOURCE_ID, ultimo, _BARRAS)
        sin_evento = [i for i in range(_BARRAS) if i not in _KINDS]
        assert len(sin_evento) > 0
        assert all(kinds[i].string_value == DIVERGENCE_KIND_NONE for i in sin_evento)

        for source_id, _ in _FLAGS:
            flags = _materializar(rules_db, source_id, ultimo, _BARRAS)
            assert all(flags[i].boolean_value is False for i in sin_evento)

    def test_kind_y_los_flags_no_se_contradicen(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_divergence: None,
    ) -> None:
        # Las cinco salen del MISMO recorrido: si una barra dice 'regular_bear' en kind,
        # el flag regular_bear de esa barra tiene que estar en true. Al reves NO se
        # exige
        # -- kind colapsa por prioridad --, y por eso la implicacion es en un solo
        # sentido.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        kinds = _materializar(rules_db, DIVERGENCE_KIND_SOURCE_ID, ultimo, _BARRAS)
        for source_id, kind in _FLAGS:
            flags = _materializar(rules_db, source_id, ultimo, _BARRAS)
            for i in range(_BARRAS):
                if kinds[i].string_value == kind.value:
                    assert flags[i].boolean_value is True, i

    def test_el_tipo_viaja_en_el_dato(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_divergence: None,
    ) -> None:
        # D1: el evaluador ramifica por ScalarValue.scalar_type sin consultar el
        # catalogo. divergence.* es la primera fuente BOOLEAN que recorre el pipeline.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        for valor in _materializar(rules_db, DIVERGENCE_KIND_SOURCE_ID, ultimo, 8):
            assert valor.scalar_type is ScalarType.STRING
            assert valor.decimal_value is None
            assert valor.string_value is not None

        for source_id, _ in _FLAGS:
            for valor in _materializar(rules_db, source_id, ultimo, 8):
                assert valor.scalar_type is ScalarType.BOOLEAN
                assert valor.decimal_value is None
                assert valor.boolean_value is not None


class TestGateBitExactoDelReplay:
    """EL GATE (ADR-007): replay desde snapshot == bootstrap, sea cual sea el ancla."""

    @pytest.mark.parametrize("source_id", _TODAS)
    def test_dos_anclas_distintas_dan_la_misma_cola(
        self,
        source_id: str,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_divergence: None,
    ) -> None:
        # El replay desde CUALQUIER snapshot valido -- y el bootstrap sin ninguno --
        # reproduce la MISMA cola. Aqui basta con que el tramo de replay arranque una
        # barra tarde para que un pivote se pierda y el par siguiente se empareje contra
        # el pivote equivocado.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, source_id, _open_time(180), 3)  # ancla en la barra 180
        cola_ancla_180 = _materializar(rules_db, source_id, ultimo, 12)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, source_id, _open_time(233), 3)  # ancla en la barra 233
        cola_ancla_233 = _materializar(rules_db, source_id, ultimo, 12)

        _borrar_snapshots(migrator_db)
        cola_sin_ancla = _materializar(rules_db, source_id, ultimo, 12)  # bootstrap

        assert cola_ancla_180 == cola_ancla_233 == cola_sin_ancla

    def test_el_estado_replayado_coincide_con_el_del_bootstrap(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_divergence: None,
    ) -> None:
        # No basta con que coincidan las SERIES: el ESTADO persistido tambien, porque es
        # lo que sembrara el siguiente replay. Un pivote que difiera hoy puede no
        # notarse
        # hasta que cierre par -- exactamente la deriva silenciosa que el snapshot
        # podria
        # introducir.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, DIVERGENCE_KIND_SOURCE_ID, _open_time(180), 3)
        _materializar(rules_db, DIVERGENCE_KIND_SOURCE_ID, ultimo, 3)
        estado_replay = _leer_ancla(rules_db, ultimo + 1)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, DIVERGENCE_KIND_SOURCE_ID, ultimo, 3)
        estado_bootstrap = _leer_ancla(rules_db, ultimo + 1)

        assert estado_replay is not None
        assert estado_bootstrap is not None
        assert estado_replay == estado_bootstrap
        # Bit a bit: dos Decimal iguales pueden diferir en exponente; aqui no se admite.
        assert str(estado_replay) == str(estado_bootstrap)

    def test_una_cadena_de_replays_no_deriva(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_divergence: None,
    ) -> None:
        # El caso REAL del worker: no una materializacion aislada, sino barra tras
        # barra,
        # cada una anclando en el snapshot que dejo la anterior. Tras encadenar decenas
        # de
        # replays la serie y el estado tienen que seguir siendo los del bootstrap de una
        # sola pasada. Es donde una deriva de la cadena de pivotes se acumularia.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        _borrar_snapshots(migrator_db)
        for barra in range(_BARRAS):
            _materializar(rules_db, DIVERGENCE_KIND_SOURCE_ID, _open_time(barra), 1)
        cadena = _materializar(rules_db, DIVERGENCE_KIND_SOURCE_ID, ultimo, 20)
        estado_cadena = _leer_ancla(rules_db, ultimo + 1)

        _borrar_snapshots(migrator_db)
        una_pasada = _materializar(rules_db, DIVERGENCE_KIND_SOURCE_ID, ultimo, 20)
        estado_una_pasada = _leer_ancla(rules_db, ultimo + 1)

        assert cadena == una_pasada
        assert estado_cadena == estado_una_pasada

    def test_la_cadena_barra_a_barra_ve_cada_evento_en_su_barra(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_divergence: None,
    ) -> None:
        # CAUSALIDAD (DEC-PROVISIONAL-02). Un pivote anclado en la barra a no se
        # confirma
        # hasta a+strength, asi que materializar SOLO la barra a no puede declarar su
        # evento todavia: hace falta llegar a a+strength. Este test fija ese contrato --
        # que es el mismo que aplica detect_divergences sobre una serie cerrada -- en
        # vez
        # de dejarlo a la interpretacion.
        _sembrar(persistir_vela)
        _borrar_snapshots(migrator_db)
        barra = max(_KINDS)  # el ultimo evento del fixture
        esperado = divergence_kind_token(_KINDS[barra])

        # Materializando hasta la barra del evento, aun le faltan barras a la derecha.
        en_su_barra = _materializar(
            rules_db, DIVERGENCE_KIND_SOURCE_ID, _open_time(barra), 1
        )
        assert en_su_barra[0].string_value == DIVERGENCE_KIND_NONE

        # Con strength barras mas, el pivote esta confirmado y el evento aparece EN SU
        # BARRA, no en la actual.
        confirmado = _materializar(
            rules_db,
            DIVERGENCE_KIND_SOURCE_ID,
            _open_time(barra + SWING_STRENGTH_DEFAULT),
            SWING_STRENGTH_DEFAULT + 1,
        )
        assert confirmado[0].string_value == esperado

    @pytest.mark.parametrize("source_id", _TODAS)
    def test_re_materializar_la_misma_barra_no_cambia_nada(
        self,
        source_id: str,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_divergence: None,
    ) -> None:
        # IDEMPOTENCIA (append-only, 0028): reevaluar la misma barra recomputa el MISMO
        # estado determinista, asi que el INSERT repetido es un duplicado exacto que el
        # ON CONFLICT DO NOTHING absorbe.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        una = _materializar(rules_db, source_id, ultimo, 5)
        estado_tras_una = _leer_ancla(rules_db, ultimo + 1)
        otra = _materializar(rules_db, source_id, ultimo, 5)

        assert otra == una
        assert _contar_snapshots(rules_db, ultimo) == 1
        assert _leer_ancla(rules_db, ultimo + 1) == estado_tras_una

    def test_las_cinco_fuentes_dejan_el_mismo_estado(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_divergence: None,
    ) -> None:
        # Las cinco materializan por separado (dispatch por SOURCE_ID) y las cinco
        # escriben. Como el estado es el mismo, escriben la MISMA fila y el ON CONFLICT
        # DO NOTHING la absorbe: UNA fila, no cinco.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        for source_id in _TODAS:
            _materializar(rules_db, source_id, ultimo, 5)

        assert _contar_snapshots(rules_db, ultimo) == 1


class TestEstadoPersistido:
    """Que se guarda de verdad en la 0028, y que es coherente con la formula."""

    def test_el_estado_es_el_ultimo_pivote_de_cada_lado(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_divergence: None,
    ) -> None:
        # ATA EL SNAPSHOT A LA FORMULA: el precio guardado de cada lado tiene que ser el
        # de la barra que dice su open_time, en la serie que le toca (HIGH para maximos,
        # LOW para minimos). Si el materializador guardara el low en el campo del high
        # --
        # o el cierre en cualquiera de los dos --, esto revienta.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        _materializar(rules_db, DIVERGENCE_KIND_SOURCE_ID, ultimo, 5)
        estado = _leer_ancla(rules_db, ultimo + 1)

        assert estado is not None
        assert estado.open_time == ultimo
        assert estado.last_high is not None
        assert estado.last_low is not None
        indice_high = (estado.last_high.open_time - _OPEN) // _TF.duration_ms
        indice_low = (estado.last_low.open_time - _OPEN) // _TF.duration_ms
        assert estado.last_high.price == _HIGHS[indice_high]
        assert estado.last_low.price == _LOWS[indice_low]

    def test_sin_velas_no_inventa_serie_ni_estado(
        self,
        rules_db: PsycopgDatabase,
        limpiar_divergence: None,
    ) -> None:
        assert (
            _materializar(rules_db, DIVERGENCE_KIND_SOURCE_ID, _open_time(0), 5) == ()
        )
        assert _leer_ancla(rules_db, _open_time(_BARRAS)) is None

    def test_en_el_warm_up_del_rsi_hay_serie_pero_ningun_evento(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_divergence: None,
    ) -> None:
        # Un pivote sin RSI (warm-up de Wilder) no cierra par, pero la fuente SIGUE
        # sirviendo un valor por barra: 'none'. Que la serie salga entera y vacia de
        # eventos es lo que distingue "no paso nada" de "no se pudo saber" -- lo segundo
        # seria una serie mas corta, y aqui no lo es.
        _sembrar(persistir_vela)

        serie = _materializar(
            rules_db, DIVERGENCE_KIND_SOURCE_ID, _open_time(RSI_PERIOD_DEFAULT), 10
        )

        assert len(serie) == 10
        assert {v.string_value for v in serie} == {DIVERGENCE_KIND_NONE}


class TestParamsEfectivos:
    """MAT-05 Q2 end-to-end: los DOS params cambian la serie y no cruzan snapshots."""

    def test_otro_strength_da_otra_cadena_de_pivotes(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_divergence: None,
    ) -> None:
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        por_defecto = _materializar(
            rules_db, DIVERGENCE_KIND_SOURCE_ID, ultimo, _BARRAS
        )
        con_cinco = _materializar(
            rules_db, DIVERGENCE_KIND_SOURCE_ID, ultimo, _BARRAS, strength=5
        )

        assert por_defecto != con_cinco
        # strength entra en la PK: cada parametrizacion tiene su propia fila.
        assert _contar_snapshots(rules_db, ultimo) == 1
        assert _contar_snapshots(rules_db, ultimo, strength=5) == 1

    def test_otro_rsi_period_da_otra_serie(
        self,
        rules_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_divergence: None,
    ) -> None:
        # LO QUE ESTE TEST DEMUESTRA DE VERDAD: que rsi_period no se queda en la
        # cache_key sino que VIAJA hasta la serie de rsi.value que alimenta al fold. Si
        # se perdiera por el camino, los pivotes serian los mismos (no dependen del RSI)
        # y la serie saldria IDENTICA a la del period por defecto.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        por_defecto = _materializar(
            rules_db, DIVERGENCE_KIND_SOURCE_ID, ultimo, _BARRAS
        )
        con_tres = _materializar(
            rules_db, DIVERGENCE_KIND_SOURCE_ID, ultimo, _BARRAS, rsi_period=3
        )

        assert por_defecto != con_tres
        assert _contar_snapshots(rules_db, ultimo, rsi_period=3) == 1

    def test_el_replay_de_una_parametrizacion_no_usa_el_ancla_de_otra(
        self,
        rules_db: PsycopgDatabase,
        migrator_db: PsycopgDatabase,
        persistir_vela: Persistir,
        limpiar_divergence: None,
    ) -> None:
        # Se deja SOLO el ancla por defecto en una barra intermedia y se materializa
        # otra
        # parametrizacion despues: si el lector ignorase los params, reanudaria la
        # cadena
        # de 2/14 como si fuera la de 5/3 y la serie se apartaria de su bootstrap.
        _sembrar(persistir_vela)
        ultimo = _open_time(_BARRAS - 1)

        _borrar_snapshots(migrator_db)
        _materializar(rules_db, DIVERGENCE_KIND_SOURCE_ID, _open_time(180), 3)
        con_ancla_ajena = _materializar(
            rules_db, DIVERGENCE_KIND_SOURCE_ID, ultimo, 12, strength=5, rsi_period=3
        )

        _borrar_snapshots(migrator_db)
        limpio = _materializar(
            rules_db, DIVERGENCE_KIND_SOURCE_ID, ultimo, 12, strength=5, rsi_period=3
        )

        assert con_ancla_ajena == limpio
