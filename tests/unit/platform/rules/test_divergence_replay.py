"""El REPLAY y la PROYECCION DENSA de divergence.* (LOTE 5, dictamen P08b-D1-05).

test_divergence.py verifica la FORMULA contra un referente independiente. Este fichero
verifica las dos piezas que la hacen SERVIBLE y que son codigo nuevo (regla 5.11):

  1. EL REPLAY. Que sembrar desde el estado de una barra intermedia da exactamente los
     mismos eventos que detectar sobre la historia entera -- el GATE de ADR-007 en su
     forma PURA, sin BD de por medio. El GATE con snapshot real vive en
     tests/integration/test_divergence_materializer.py; aqui se aisla la propiedad
     matematica, que es donde una deriva se explica.
  2. LA PROYECCION DENSA. Que el token de una barra y sus cuatro flags cuentan la misma
     historia que la lista de eventos, incluida la barra en que coinciden dos.

Y el candado de que detect_divergences NO es una segunda implementacion: si alguien
forkeara la formula para el replay, el equivalente de abajo lo caza.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ce_v5.platform.rules.indicators.divergence import (
    DIVERGENCE_HIDDEN_BEAR_SOURCE_ID,
    DIVERGENCE_HIDDEN_BULL_SOURCE_ID,
    DIVERGENCE_KIND_NONE,
    DIVERGENCE_KIND_SOURCE_ID,
    DIVERGENCE_REGULAR_BEAR_SOURCE_ID,
    DIVERGENCE_REGULAR_BULL_SOURCE_ID,
    Divergence,
    DivergenceKind,
    DivergenceOutput,
    DivergenceState,
    PivotObservation,
    declarations,
    detect_divergences,
    divergence_flag,
    divergence_kind_token,
    divergence_replay,
    divergence_seed,
)
from ce_v5.platform.rules.indicators.rsi import RSI_SOURCE_ID, wilder_rsi
from ce_v5.platform.rules.rawclose import MARKET_CLOSE_SOURCE_ID
from source.datasource import MemoryModel, Servibility
from source.rules.scalar import ScalarType

_STRENGTH = 2
_PERIOD = 14


def _synthetic_ohlc(
    n: int, seed: int
) -> tuple[list[Decimal], list[Decimal], list[Decimal]]:
    """OHLC deterministico (LCG), el MISMO generador que test_divergence.py.

    Se repite a proposito en vez de importarse de alli: los dos ficheros prueban cosas
    distintas y compartir el fixture ataria uno al otro sin necesidad. Valores muy
    dispersos, sin empates practicos.
    """
    x = seed
    highs: list[Decimal] = []
    lows: list[Decimal] = []
    closes: list[Decimal] = []
    for _ in range(n):
        x = (1103515245 * x + 12345) % 2147483648
        mid = Decimal(x % 500000) / Decimal(1000)
        spread = Decimal(1) + Decimal((x // 500000) % 300) / Decimal(100)
        c_off = (Decimal((x // 7) % 400) - Decimal(200)) / Decimal(100)
        highs.append(mid + spread)
        lows.append(mid - spread)
        closes.append(mid + c_off)
    return highs, lows, closes


class TestElReplayEsLaMismaFormula:
    """detect_divergences ES divergence_replay sembrado vacio, no su gemelo."""

    def test_sembrar_vacio_sobre_la_historia_entera_da_lo_mismo(self) -> None:
        # Si un dia se forkeara la formula para el replay, este equivalente lo caza:
        # las dos caras tienen que ser literalmente el mismo recorrido.
        highs, lows, closes = _synthetic_ohlc(400, 1234567)
        rsi = wilder_rsi(closes, _PERIOD)

        _, eventos = divergence_replay(highs, lows, rsi, divergence_seed(), _STRENGTH)

        assert eventos == detect_divergences(highs, lows, closes, _STRENGTH, _PERIOD)
        assert len(eventos) > 0  # el fixture detecta divergencias de verdad

    def test_el_estado_vacio_no_tiene_pivote_en_ningun_lado(self) -> None:
        assert divergence_seed() == DivergenceState(last_high=None, last_low=None)

    def test_las_series_de_distinta_longitud_se_rechazan(self) -> None:
        with pytest.raises(ValueError, match="misma longitud"):
            divergence_replay(
                [Decimal(1)], [Decimal(1), Decimal(2)], [None], divergence_seed()
            )

    def test_un_strength_fuera_de_dominio_se_rechaza(self) -> None:
        with pytest.raises(ValueError, match="strength"):
            divergence_replay([], [], [], divergence_seed(), strength=0)


class TestGatePuroDelReplay:
    """EL GATE (ADR-007) en su forma pura: reanudar == no haber parado nunca.

    Es la propiedad de la que depende TODO el diseno RECURSIVE: si el estado "ultimo
    pivote de cada lado" no bastara, aqui se veria como eventos que aparecen, faltan o
    cambian de pareja al cortar la historia por un sitio u otro.
    """

    def _cortar(
        self, corte: int, n: int = 400, seed: int = 1234567
    ) -> tuple[tuple[Divergence, ...], tuple[Divergence, ...]]:
        """(eventos antes del corte + eventos al reanudar, eventos de una sola pasada).

        LA COMPARACION ES POR CONCATENACION, no por "los eventos posteriores al corte",
        y
        la diferencia importa: un pivote anclado en las ultimas `strength` barras del
        primer tramo AUN NO ESTA CONFIRMADO alli (le faltan barras a la derecha), asi
        que
        no lo emite el primer tramo y SI lo emite el segundo -- con su indice, que es
        ANTERIOR al corte. Eso no es una fuga del replay: es la causalidad de
        symmetric_pivots (DEC-PROVISIONAL-02), y detect_divergences sobre la serie corta
        hace exactamente lo mismo. Lo que el GATE exige es que la UNION sea la serie
        completa, sin repetidos, sin huecos y en el mismo orden.
        """
        highs, lows, closes = _synthetic_ohlc(n, seed)
        rsi = wilder_rsi(closes, _PERIOD)

        estado_en_corte, primeros = divergence_replay(
            highs[:corte], lows[:corte], rsi[:corte], divergence_seed(), _STRENGTH
        )
        _, reanudados = divergence_replay(highs, lows, rsi, estado_en_corte, _STRENGTH)
        completos = detect_divergences(highs, lows, closes, _STRENGTH, _PERIOD)
        return (primeros + reanudados, completos)

    @pytest.mark.parametrize("corte", [50, 137, 200, 313])
    def test_reanudar_en_cualquier_barra_da_los_mismos_eventos(
        self, corte: int
    ) -> None:
        unidos, completos = self._cortar(corte)
        assert unidos == completos

    @pytest.mark.parametrize("corte", [50, 137, 200, 313])
    def test_ningun_evento_se_emite_dos_veces_al_reanudar(self, corte: int) -> None:
        # El dedup por ancla en accion: si el segundo tramo reprocesara pivotes ya
        # contabilizados, la concatenacion tendria duplicados y seria mas larga que la
        # pasada unica -- un doble disparo de la misma divergencia en produccion.
        unidos, completos = self._cortar(corte)
        assert len(unidos) == len(completos)
        assert len({(d.index, d.kind) for d in unidos}) == len(unidos)

    def test_el_corte_elegido_deja_eventos_a_los_dos_lados(self) -> None:
        # Muerde: si el corte cayera en una zona sin divergencias, los tests de arriba
        # compararian dos tuplas vacias y pasarian sin probar nada.
        highs, lows, closes = _synthetic_ohlc(400, 1234567)
        completos = detect_divergences(highs, lows, closes, _STRENGTH, _PERIOD)
        assert any(d.index < 200 for d in completos)
        assert any(d.index >= 200 for d in completos)

    def test_una_cadena_de_replays_encadenados_no_deriva(self) -> None:
        # El caso REAL del worker: no un corte aislado, sino barra tras barra, cada una
        # sembrando desde el estado que dejo la anterior. Es donde una deriva del estado
        # se acumularia -- y donde NO se veria en la barra del corte, sino mucho
        # despues,
        # la primera vez que un pivote se emparejara con la pareja equivocada.
        highs, lows, closes = _synthetic_ohlc(300, 987654)
        rsi = wilder_rsi(closes, _PERIOD)

        estado = divergence_seed()
        cadena: list[Divergence] = []
        for barra in range(1, len(highs) + 1):
            estado_barra, eventos = divergence_replay(
                highs[:barra], lows[:barra], rsi[:barra], estado, _STRENGTH
            )
            cadena.extend(eventos)
            estado = estado_barra

        assert tuple(cadena) == detect_divergences(
            highs, lows, closes, _STRENGTH, _PERIOD
        )

    def test_el_estado_encadenado_es_el_de_una_sola_pasada(self) -> None:
        # No basta con que coincidan los EVENTOS: el ESTADO tambien, porque es lo que
        # sembrara el siguiente replay.
        highs, lows, closes = _synthetic_ohlc(300, 987654)
        rsi = wilder_rsi(closes, _PERIOD)

        estado = divergence_seed()
        for barra in range(1, len(highs) + 1):
            estado, _ = divergence_replay(
                highs[:barra], lows[:barra], rsi[:barra], estado, _STRENGTH
            )
        una_pasada, _ = divergence_replay(
            highs, lows, rsi, divergence_seed(), _STRENGTH
        )

        assert estado == una_pasada
        assert estado.last_high is not None
        assert estado.last_low is not None


class TestDedupPorAncla:
    """El tramo puede empezar ANTES del ultimo pivote guardado sin contar dos veces.

    Es lo que permite al materializador leer el contexto izquierdo que symmetric_pivots
    exige. Sin el dedup, ese contexto reinyectaria pivotes ya procesados y la cadena se
    emparejaria consigo misma.
    """

    def test_reprocesar_el_tramo_entero_desde_el_estado_final_no_emite_nada(
        self,
    ) -> None:
        highs, lows, closes = _synthetic_ohlc(300, 55555)
        rsi = wilder_rsi(closes, _PERIOD)
        final, primeros = divergence_replay(
            highs, lows, rsi, divergence_seed(), _STRENGTH
        )
        assert len(primeros) > 0

        estado_otra_vez, repetidos = divergence_replay(
            highs, lows, rsi, final, _STRENGTH
        )

        assert repetidos == ()
        assert estado_otra_vez == final

    def test_un_pivote_con_ancla_anterior_a_la_del_estado_se_ignora(self) -> None:
        # Estado adelantado a mano hasta la ultima barra: NINGUN pivote del tramo tiene
        # ancla posterior, asi que no puede salir ni un evento.
        highs, lows, closes = _synthetic_ohlc(200, 24680)
        rsi = wilder_rsi(closes, _PERIOD)
        ultimo = len(highs) - 1
        estado = DivergenceState(
            last_high=PivotObservation(ultimo, highs[ultimo], rsi[ultimo]),
            last_low=PivotObservation(ultimo, lows[ultimo], rsi[ultimo]),
        )

        _, eventos = divergence_replay(highs, lows, rsi, estado, _STRENGTH)

        assert eventos == ()


class TestPivoteSinRsi:
    """Un pivote en el warm-up de Wilder no produce evento, pero SI avanza la cadena."""

    def test_sin_rsi_no_hay_evento_pero_el_pivote_queda_de_ultimo(self) -> None:
        # rsi todo a None: hay pivotes (la geometria no depende del RSI) y ni un evento,
        # pero el estado tiene que quedar apuntando al ultimo pivote de cada lado. Si en
        # vez de eso se cortara la cadena, el primer par tras el warm-up se perderia.
        highs, lows, closes = _synthetic_ohlc(200, 13579)
        sin_rsi: list[Decimal | None] = [None] * len(closes)

        estado, eventos = divergence_replay(
            highs, lows, sin_rsi, divergence_seed(), _STRENGTH
        )

        assert eventos == ()
        assert estado.last_high is not None
        assert estado.last_low is not None
        assert estado.last_high.rsi is None


class TestProyeccionDensa:
    """De la lista DISPERSA de eventos al valor POR BARRA que sirve la fuente."""

    def test_una_barra_sin_evento_es_none_y_cuatro_false(self) -> None:
        assert divergence_kind_token(frozenset()) == DIVERGENCE_KIND_NONE
        for output in (
            DivergenceOutput.REGULAR_BULL,
            DivergenceOutput.REGULAR_BEAR,
            DivergenceOutput.HIDDEN_BULL,
            DivergenceOutput.HIDDEN_BEAR,
        ):
            assert divergence_flag(frozenset(), output) is False

    def test_el_token_de_un_evento_unico_es_su_propio_kind(self) -> None:
        for kind in DivergenceKind:
            assert divergence_kind_token({kind}) == kind.value

    def test_none_no_colisiona_con_ningun_kind(self) -> None:
        # Una regla que pida divergence.kind != 'none' se rompería en silencio si algun
        # tipo se llamara igual que la ausencia.
        assert DIVERGENCE_KIND_NONE not in {k.value for k in DivergenceKind}

    def test_dos_eventos_en_la_misma_barra_colapsan_por_la_prioridad_de_v4(
        self,
    ) -> None:
        # Un lado bajista y otro alcista pueden coincidir (los maximos salen de HIGH y
        # los minimos de LOW). kind se queda con el de MAYOR prioridad -- el orden de
        # deteccion de v4, no un criterio nuevo.
        assert (
            divergence_kind_token(
                {DivergenceKind.REGULAR_BULL, DivergenceKind.REGULAR_BEAR}
            )
            == DivergenceKind.REGULAR_BEAR.value
        )
        assert (
            divergence_kind_token(
                {DivergenceKind.HIDDEN_BULL, DivergenceKind.HIDDEN_BEAR}
            )
            == DivergenceKind.HIDDEN_BEAR.value
        )
        assert (
            divergence_kind_token(
                {DivergenceKind.HIDDEN_BEAR, DivergenceKind.REGULAR_BULL}
            )
            == DivergenceKind.REGULAR_BULL.value
        )

    def test_la_precedencia_de_kind_es_la_del_orden_de_detect_divergences(self) -> None:
        # ATA LAS DOS CARAS: el token de una barra tiene que ser el kind del PRIMER
        # evento que detect_divergences lista para ella. Si la precedencia se tocara sin
        # tocar _PRIORITY (o al reves), las dos se contradirian sobre los mismos datos.
        highs, lows, closes = _synthetic_ohlc(400, 1234567)
        eventos = detect_divergences(highs, lows, closes, _STRENGTH, _PERIOD)
        por_barra: dict[int, list[DivergenceKind]] = {}
        for evento in eventos:
            por_barra.setdefault(evento.index, []).append(evento.kind)

        for kinds in por_barra.values():
            assert divergence_kind_token(set(kinds)) == kinds[0].value

    def test_los_flags_no_colapsan_cuando_kind_si(self) -> None:
        # La razon de ser de los cuatro flags: en la barra en que coinciden dos
        # divergencias, kind solo puede contar una y los flags cuentan las dos.
        kinds = {DivergenceKind.REGULAR_BULL, DivergenceKind.REGULAR_BEAR}
        assert divergence_flag(kinds, DivergenceOutput.REGULAR_BULL) is True
        assert divergence_flag(kinds, DivergenceOutput.REGULAR_BEAR) is True
        assert divergence_flag(kinds, DivergenceOutput.HIDDEN_BULL) is False

    def test_cada_flag_mira_solo_su_tipo(self) -> None:
        for output in (
            DivergenceOutput.REGULAR_BULL,
            DivergenceOutput.REGULAR_BEAR,
            DivergenceOutput.HIDDEN_BULL,
            DivergenceOutput.HIDDEN_BEAR,
        ):
            propios = {k for k in DivergenceKind if divergence_flag({k}, output)}
            assert propios == {DivergenceKind(output.value)}

    def test_pedir_un_flag_de_kind_falla_ruidoso(self) -> None:
        # kind no es un flag: no hay un booleano "hubo kind". Pedirlo es un error de
        # cableado y se ve como tal, no como un false silencioso.
        with pytest.raises(ValueError, match="divergence_kind_token"):
            divergence_flag(frozenset(), DivergenceOutput.KIND)


class TestDeclaraciones:
    """Las CINCO fuentes tal como entran al catalogo vivo."""

    def test_son_cinco_con_los_source_id_esperados(self) -> None:
        assert {d.source_id for d in declarations()} == {
            DIVERGENCE_KIND_SOURCE_ID,
            DIVERGENCE_REGULAR_BULL_SOURCE_ID,
            DIVERGENCE_REGULAR_BEAR_SOURCE_ID,
            DIVERGENCE_HIDDEN_BULL_SOURCE_ID,
            DIVERGENCE_HIDDEN_BEAR_SOURCE_ID,
        }

    def test_kind_es_string_y_los_cuatro_flags_boolean(self) -> None:
        por_id = {d.source_id: d for d in declarations()}
        assert por_id[DIVERGENCE_KIND_SOURCE_ID].value_type is ScalarType.STRING
        for source_id in (
            DIVERGENCE_REGULAR_BULL_SOURCE_ID,
            DIVERGENCE_REGULAR_BEAR_SOURCE_ID,
            DIVERGENCE_HIDDEN_BULL_SOURCE_ID,
            DIVERGENCE_HIDDEN_BEAR_SOURCE_ID,
        ):
            assert por_id[source_id].value_type is ScalarType.BOOLEAN

    def test_las_cinco_son_continuous_y_recursive(self) -> None:
        # CONTINUOUS pese a que el fenomeno es disperso: hay valor por barra ('none' /
        # false), que es lo que la servibilidad mide. RECURSIVE porque el pivote previo
        # no tiene cota -- es lo que justifica el snapshot de la 0028.
        for declaration in declarations():
            assert declaration.servibility is Servibility.CONTINUOUS
            assert declaration.memory_model is MemoryModel.RECURSIVE

    def test_las_cinco_declaran_los_dos_params_y_los_llevan_en_la_cache_key(
        self,
    ) -> None:
        # strength decide QUE pivotes hay y rsi_period QUE RSI se lee en ellos: las dos
        # parametrizaciones dan series DISTINTAS y por eso no pueden compartir clave.
        for declaration in declarations():
            assert {p.name for p in declaration.params} == {"strength", "rsi_period"}
            assert set(declaration.overridable_params) == {"strength", "rsi_period"}
            assert declaration.cache_key_schema == (
                "exchange",
                "symbol",
                "timeframe",
                "strength",
                "rsi_period",
            )

    def test_consume_rsi_y_el_cierre_pero_no_swing(self) -> None:
        # swing.* trabaja sobre la serie de CIERRES y divergence sobre las de HIGH y
        # LOW:
        # declararla seria decir que se usa algo que no se usa.
        for declaration in declarations():
            assert set(declaration.consumes) == {RSI_SOURCE_ID, MARKET_CLOSE_SOURCE_ID}
