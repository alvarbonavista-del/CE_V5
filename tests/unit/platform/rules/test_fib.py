"""Verificacion de fib.* (nucleo puro) contra referentes EXACTOS independientes."""

from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from ce_v5.platform.rules.indicators.fib import (
    FIB_DIRECTION_SOURCE_ID,
    FIB_FORMULA_VERSION,
    FIB_LEVEL_PCT_SOURCE_ID,
    FIB_LEVELS_SOURCE_ID,
    FIB_NEAREST_LEVEL_SOURCE_ID,
    FibDirection,
    FibOutput,
    declarations,
    direction,
    fib_direction_declaration,
    fib_direction_token,
    fib_level_pct_declaration,
    fib_levels,
    fib_levels_declaration,
    fib_nearest_level_declaration,
    fib_output,
    fib_range_seed,
    fib_range_step,
    level_pct,
    nearest_level,
)
from ce_v5.platform.rules.indicators.swing import SWING_STRENGTH_DEFAULT
from source.datasource import DataSourceDeclaration, MemoryModel, Servibility
from source.rules.scalar import ScalarType

_INSIDE = [
    Fraction(0),
    Fraction(236, 1000),
    Fraction(382, 1000),
    Fraction(1, 2),
    Fraction(618, 1000),
    Fraction(786, 1000),
    Fraction(1),
]
_EXT = [
    Fraction(272, 1000),
    Fraction(414, 1000),
    Fraction(618, 1000),
    Fraction(1),
    Fraction(1618, 1000),
]


def _d(x: object) -> Decimal:
    return Decimal(str(x))


# --- 1. Golden exacto (rango 0..100) ---


def test_levels_exact_golden() -> None:
    lv = fib_levels(_d(100), _d(0))
    assert lv.inside == (
        _d(0),
        _d("23.6"),
        _d("38.2"),
        _d("50"),
        _d("61.8"),
        _d("78.6"),
        _d(100),
    )
    assert lv.above == (_d("127.2"), _d("141.4"), _d("161.8"), _d(200), _d("261.8"))
    assert lv.below == (_d("-27.2"), _d("-41.4"), _d("-61.8"), _d(-100), _d("-161.8"))
    assert lv.ordered_levels[0] == _d("-161.8")
    assert lv.ordered_levels[-1] == _d("261.8")
    assert lv.ordered_pcts[0] == _d("-161.8")
    assert lv.ordered_pcts[-1] == _d("261.8")


def test_nearest_and_pct_and_direction_golden() -> None:
    assert nearest_level(_d(100), _d(0), _d(40)) == _d("38.2")
    assert level_pct(_d(100), _d(0), _d(40)) == _d("38.2")
    assert direction(_d(100), _d(0), _d(40)) is FibDirection.ABOVE


def test_nearest_tie_picks_lower_index() -> None:
    # price=44.1 equidista de 38.2 y 50 ; en empate gana el de indice menor (38.2)
    assert nearest_level(_d(100), _d(0), _d("44.1")) == _d("38.2")


def test_direction_tie_is_above() -> None:
    assert direction(_d(100), _d(0), _d("38.2")) is FibDirection.ABOVE


# --- 2. Diferencial contra referente Fraction ---


def _ref_levels(ph: Decimal, pl: Decimal) -> tuple[list[Fraction], list[Fraction]]:
    PH, PL = Fraction(ph), Fraction(pl)
    rng = PH - PL
    inside = [PL + r * rng for r in _INSIDE]
    above = [PH + r * rng for r in _EXT]
    below = [PL - r * rng for r in _EXT]
    ordered = list(reversed(below)) + inside + above
    below_pcts = [
        Fraction(-272, 10),
        Fraction(-414, 10),
        Fraction(-618, 10),
        Fraction(-100),
        Fraction(-1618, 10),
    ]
    inside_pcts = [
        Fraction(0),
        Fraction(236, 10),
        Fraction(382, 10),
        Fraction(50),
        Fraction(618, 10),
        Fraction(786, 10),
        Fraction(100),
    ]
    above_pcts = [
        Fraction(1272, 10),
        Fraction(1414, 10),
        Fraction(1618, 10),
        Fraction(200),
        Fraction(2618, 10),
    ]
    ordered_pcts = list(reversed(below_pcts)) + inside_pcts + above_pcts
    return ordered, ordered_pcts


def _ref_nearest(ph: Decimal, pl: Decimal, price: Decimal) -> tuple[Fraction, Fraction]:
    levels, pcts = _ref_levels(ph, pl)
    p = Fraction(price)
    best_l, best_p = levels[0], pcts[0]
    best_d = abs(p - levels[0])
    for lv, pc in zip(levels[1:], pcts[1:], strict=False):
        d = abs(p - lv)
        if d < best_d:
            best_d, best_l, best_p = d, lv, pc
    return best_l, best_p


def _synth(n: int, seed: int) -> list[tuple[Decimal, Decimal, Decimal]]:
    x = seed
    out: list[tuple[Decimal, Decimal, Decimal]] = []
    for _ in range(n):
        x = (1103515245 * x + 12345) % 2147483648
        pl = Decimal(x % 50000) / Decimal(100)
        rng = Decimal(1) + Decimal((x // 7) % 50000) / Decimal(100)
        ph = pl + rng
        price = pl - rng + Decimal((x // 13) % 300000) / Decimal(100)
        out.append((ph, pl, price))
    return out


def test_levels_match_fraction_referent() -> None:
    for ph, pl, _price in _synth(300, 314159):
        got = fib_levels(ph, pl)
        ref_levels, _ref_pcts = _ref_levels(ph, pl)
        assert len(got.ordered_levels) == 17
        for g, r in zip(got.ordered_levels, ref_levels, strict=False):
            assert Fraction(g) == r


def test_nearest_pct_direction_match_referent() -> None:
    for ph, pl, price in _synth(400, 271828):
        ref_l, _ref_p = _ref_nearest(ph, pl, price)
        assert Fraction(nearest_level(ph, pl, price)) == ref_l
        exp_pct = (ref_l - Fraction(pl)) / (Fraction(ph) - Fraction(pl)) * 100
        assert Fraction(level_pct(ph, pl, price)) == exp_pct
        exp_dir = FibDirection.ABOVE if Fraction(price) >= ref_l else FibDirection.BELOW
        assert direction(ph, pl, price) is exp_dir


def test_levels_are_context_independent() -> None:
    ph, pl = _d("12345.678"), _d("9876.543")
    with localcontext() as ctx:
        ctx.prec = 6
        a = fib_levels(ph, pl).ordered_levels
    with localcontext() as ctx:
        ctx.prec = 50
        b = fib_levels(ph, pl).ordered_levels
    assert a == b


# --- 3. Version y validaciones ---


def test_formula_version_is_pinned() -> None:
    assert FIB_FORMULA_VERSION == 1


def test_invalid_range_raises() -> None:
    with pytest.raises(ValueError):
        fib_levels(_d(10), _d(10))
    with pytest.raises(ValueError):
        fib_levels(_d(5), _d(10))


# --- 4. Rango con HISTERESIS (P08b-FIB-01): paridad v4 _maybe_retrace ---


def _fold(
    inicial: tuple[Decimal, Decimal], pivotes: list[tuple[Decimal, Decimal]]
) -> tuple[Decimal, Decimal]:
    """Aplica la histeresis sobre una secuencia de pares (swing_high, swing_low)."""
    estado = inicial
    for swing_high, swing_low in pivotes:
        estado = fib_range_step(estado[0], estado[1], swing_high, swing_low)
    return estado


class TestRangoConHisteresis:
    """El rango PEGAJOSO: solo se mueve si el swing lo supera por >= 0.414 del rango.

    Es lo que impide que un pivote que oscila un tick redibuje el grid barra a barra. El
    umbral es un ratio Fibonacci DEFINITORIO (_EXT_RATIOS[1]), no un parametro.
    """

    def test_la_semilla_es_el_primer_par_de_pivotes(self) -> None:
        assert fib_range_seed(_d(110), _d(100)) == (_d(110), _d(100))

    def test_una_superacion_corta_no_mueve_el_rango(self) -> None:
        # rango 10 -> min_dist = 4.14. Un high de 114 lo supera (114 > 110) pero solo
        # por 4 < 4.14: el rango NO se mueve. Este es el corazon de la histeresis.
        assert fib_range_step(_d(110), _d(100), _d(114), _d(100)) == (_d(110), _d(100))
        assert fib_range_step(_d(110), _d(100), _d(110), _d("96.5")) == (
            _d(110),
            _d(100),
        )

    def test_una_superacion_suficiente_si_mueve_el_rango(self) -> None:
        # 115 - 110 = 5 >= 4.14 -> el high se mueve. 100 - 95 = 5 >= 4.14 -> el low
        # tambien. Cada extremo se decide por separado.
        assert fib_range_step(_d(110), _d(100), _d(115), _d(100)) == (_d(115), _d(100))
        assert fib_range_step(_d(110), _d(100), _d(110), _d(95)) == (_d(110), _d(95))

    def test_el_umbral_exacto_mueve_el_rango(self) -> None:
        # Frontera: exactamente min_dist (>= , no >). rango 10 -> min_dist = 4.14.
        assert fib_range_step(_d(110), _d(100), _d("114.14"), _d(100)) == (
            _d("114.14"),
            _d(100),
        )
        # Un tick por debajo del umbral NO mueve.
        assert fib_range_step(_d(110), _d(100), _d("114.13"), _d(100)) == (
            _d(110),
            _d(100),
        )

    def test_un_swing_dentro_del_rango_nunca_lo_encoge(self) -> None:
        # El rango solo se ENSANCHA (o se queda). Un swing interior no lo estrecha: si
        # lo hiciera, el grid perseguiria al precio y la histeresis no serviria de nada.
        assert fib_range_step(_d(200), _d(100), _d(150), _d(120)) == (_d(200), _d(100))

    def test_los_dos_extremos_pueden_moverse_en_el_mismo_paso(self) -> None:
        assert fib_range_step(_d(110), _d(100), _d(130), _d(80)) == (_d(130), _d(80))

    def test_el_min_dist_es_el_del_rango_de_entrada_para_los_dos_extremos(self) -> None:
        # Los dos extremos se miden contra el MISMO min_dist (el del rango que entra).
        # Si se recalculara tras mover el primero, el resultado dependeria del orden de
        # evaluacion y el fold dejaria de ser determinista. rango 10 -> min_dist 4.14
        # para ambos: el high sube (5 >= 4.14) y el low baja (4.5 >= 4.14).
        assert fib_range_step(_d(110), _d(100), _d(115), _d("95.5")) == (
            _d(115),
            _d("95.5"),
        )

    def test_un_rango_degenerado_se_resetea_al_swing_de_la_barra(self) -> None:
        # rng <= 0: min_dist seria 0 (o negativo) y la histeresis dejaria de frenar
        # nada. Se adopta el swing tal cual.
        assert fib_range_step(_d(100), _d(100), _d(120), _d(90)) == (_d(120), _d(90))
        assert fib_range_step(_d(90), _d(100), _d(120), _d(80)) == (_d(120), _d(80))

    def test_la_pegajosidad_es_memoria_ilimitada(self) -> None:
        # LA razon de que fib.* sea RECURSIVE y necesite snapshot: cien barras de ruido
        # que no alcanzan el umbral dejan el rango EXACTAMENTE donde estaba, asi que el
        # rango vigente puede venir de un pivote de hace cientos de barras.
        inicial = (_d(110), _d(100))
        ruido = [(_d(113), _d("97")) for _ in range(100)]
        assert _fold(inicial, ruido) == inicial

    def test_el_fold_es_determinista(self) -> None:
        inicial = (_d(110), _d(100))
        pivotes = [
            (_d(114), _d(100)),
            (_d(115), _d(99)),
            (_d(118), _d(94)),
            (_d(118), _d(80)),
        ]
        assert _fold(inicial, pivotes) == _fold(inicial, pivotes)

    def test_replay_desde_un_estado_intermedio_da_la_misma_cola(self) -> None:
        # EL GATE en frio: cortar la secuencia por cualquier punto y seguir desde el
        # estado de ese corte reproduce el MISMO rango final. Es lo que legitima el
        # snapshot; el test de integracion lo repite contra PostgreSQL.
        inicial = (_d(110), _d(100))
        pivotes = [
            (_d(114), _d(100)),
            (_d(120), _d(100)),
            (_d(120), _d(88)),
            (_d(121), _d(88)),
            (_d(150), _d(60)),
        ]
        completo = _fold(inicial, pivotes)
        for corte in range(len(pivotes) + 1):
            intermedio = _fold(inicial, pivotes[:corte])
            assert _fold(intermedio, pivotes[corte:]) == completo


class TestSalidasDelGrid:
    """fib_output: las dos proyecciones servibles, y el borde del rango degenerado."""

    def test_las_salidas_son_las_funciones_puras(self) -> None:
        for price in (_d(40), _d(0), _d(100), _d(-200), _d(500)):
            assert fib_output(
                _d(100), _d(0), price, FibOutput.NEAREST_LEVEL
            ) == nearest_level(_d(100), _d(0), price)
            assert fib_output(_d(100), _d(0), price, FibOutput.LEVEL_PCT) == level_pct(
                _d(100), _d(0), price
            )

    def test_las_dos_salidas_no_son_la_misma(self) -> None:
        # Si fib_output cruzara las proyecciones, el test de arriba seguiria pasando
        # solo si nivel y porcentaje coincidieran. Con este rango no coinciden.
        rango_alto, rango_bajo, price = _d(20000), _d(19000), _d("19400")
        assert fib_output(
            rango_alto, rango_bajo, price, FibOutput.NEAREST_LEVEL
        ) != fib_output(rango_alto, rango_bajo, price, FibOutput.LEVEL_PCT)

    def test_rango_degenerado_emite_el_fallback_definido(self) -> None:
        # fib_levels RECHAZA un rango <= 0 (y debe seguir haciendolo: la funcion pura se
        # queda fiel). Pero un materializador no puede dejar un hueco a media serie, asi
        # que el borde emite un valor DEFINIDO: el propio rango colapsado y 0%.
        with pytest.raises(ValueError):
            nearest_level(_d(100), _d(100), _d(42))
        assert fib_output(_d(100), _d(100), _d(42), FibOutput.NEAREST_LEVEL) == _d(100)
        assert fib_output(_d(100), _d(100), _d(42), FibOutput.LEVEL_PCT) == _d(0)
        # Tambien con el rango INVERTIDO (que solo puede venir de datos rotos).
        assert fib_output(_d(90), _d(100), _d(42), FibOutput.NEAREST_LEVEL) == _d(90)
        assert fib_output(_d(90), _d(100), _d(42), FibOutput.LEVEL_PCT) == _d(0)


class TestDeclaraciones:
    """Cara declarativa: las dos fuentes fib.* escalares (P08b-FIB-01)."""

    def test_los_dos_source_id(self) -> None:
        assert (
            fib_nearest_level_declaration().source_id
            == FIB_NEAREST_LEVEL_SOURCE_ID
            == "fib.nearest_level"
        )
        assert (
            fib_level_pct_declaration().source_id
            == FIB_LEVEL_PCT_SOURCE_ID
            == "fib.level_pct"
        )

    @pytest.mark.parametrize(
        "d",
        [fib_nearest_level_declaration(), fib_level_pct_declaration()],
    )
    def test_forma_comun_de_las_dos(self, d: DataSourceDeclaration) -> None:
        assert d.servibility == Servibility.CONTINUOUS
        # RECURSIVE: el rango pegajoso es memoria sin cota (de ahi la 0027).
        assert d.memory_model == MemoryModel.RECURSIVE
        assert d.value_type == ScalarType.DECIMAL
        assert d.shared_evaluation is True
        assert d.sharing_scope.value == "public_cross_tenant"

    @pytest.mark.parametrize(
        "d",
        [fib_nearest_level_declaration(), fib_level_pct_declaration()],
    )
    def test_consume_las_tres_series_de_las_que_se_alimenta(
        self, d: DataSourceDeclaration
    ) -> None:
        # Primer DataSource del catalogo que consume DOS fuentes derivadas a la vez.
        assert d.consumes == ("swing.high", "swing.low", "market.close")

    @pytest.mark.parametrize(
        "d",
        [fib_nearest_level_declaration(), fib_level_pct_declaration()],
    )
    def test_strength_en_cache_key_y_overridable_default_dos(
        self, d: DataSourceDeclaration
    ) -> None:
        assert d.overridable_params == ("strength",)
        assert "strength" in d.cache_key_schema
        assert {p.name for p in d.params} <= set(d.cache_key_schema)
        (strength,) = d.params
        assert strength.value_type == ScalarType.INTEGER
        assert strength.default is not None
        assert strength.default.integer_value == SWING_STRENGTH_DEFAULT == 2

    @pytest.mark.parametrize(
        "d",
        [fib_nearest_level_declaration(), fib_level_pct_declaration()],
    )
    def test_los_ratios_fibonacci_no_son_parametros(
        self, d: DataSourceDeclaration
    ) -> None:
        # D6: los ratios (el 0.414 de la histeresis incluido) son DEFINITORIOS. Si
        # aparecieran como param o en la cache_key, el indicador dejaria de tener una
        # identidad estable y dos reglas podrian pedir "fib" y hablar de cosas
        # distintas.
        nombres = {p.name for p in d.params}
        assert nombres == {"strength"}
        for prohibido in ("ratio", "ratios", "min_dist", "hysteresis", "touch_pct"):
            assert prohibido not in d.cache_key_schema

    def test_declarations_publica_las_tres_servibles_mas_fib_levels(self) -> None:
        publicadas = declarations()
        assert len(publicadas) == 4
        ids = {d.source_id for d in publicadas}
        assert ids == {
            FIB_NEAREST_LEVEL_SOURCE_ID,
            FIB_LEVEL_PCT_SOURCE_ID,
            FIB_DIRECTION_SOURCE_ID,
            FIB_LEVELS_SOURCE_ID,
        }

    def test_levels_ya_no_esta_diferida_es_non_servible(self) -> None:
        # P08b-D1-04 (LOTE 5): fib.levels (17 niveles) sigue sin materializador -- un
        # VECTOR por barra no lo representa ningun ScalarType, ni el carrier de D1 (que
        # resolvio CATEGORICO, no vectorial) -- pero YA entra al catalogo vivo como
        # NODO DECLARADO NON_SERVIBLE, calcada de vp.hvn/vp.lvn: se conoce, no se sirve.
        ids = {d.source_id for d in declarations()}
        assert FIB_LEVELS_SOURCE_ID in ids
        assert fib_levels_declaration().servibility == Servibility.NON_SERVIBLE


class TestDeclaracionDireccion:
    """fib.direction: la primera fuente CATEGORICA servible del catalogo (LOTE 5).

    Comparte rango, snapshot (0027) y param con sus dos hermanas numericas; lo unico
    que cambia es el value_type. Que esa simetria se mantenga es lo que garantiza que
    las tres se alinean barra a barra.
    """

    def test_es_string_continuous_recursive(self) -> None:
        d = fib_direction_declaration()
        assert d.source_id == FIB_DIRECTION_SOURCE_ID == "fib.direction"
        assert d.value_type == ScalarType.STRING
        assert d.servibility == Servibility.CONTINUOUS
        # RECURSIVE: no anade estado propio, pero HEREDA el del rango con histeresis.
        assert d.memory_model == MemoryModel.RECURSIVE

    def test_comparte_forma_con_nearest_level(self) -> None:
        # MISMOS params, cache_key y consumes: si divergieran, direction se tenderia
        # sobre un rango distinto del de nearest_level y dejarian de ser coherentes.
        direccion = fib_direction_declaration()
        nivel = fib_nearest_level_declaration()
        assert direccion.cache_key_schema == nivel.cache_key_schema
        assert direccion.consumes == nivel.consumes
        assert direccion.overridable_params == nivel.overridable_params
        assert [p.name for p in direccion.params] == [p.name for p in nivel.params]
        assert direccion.sharing_scope == nivel.sharing_scope

    def test_solo_cambia_el_value_type(self) -> None:
        # Y lo que SI cambia, cambia: numerica vs categorica.
        assert fib_nearest_level_declaration().value_type == ScalarType.DECIMAL
        assert fib_direction_declaration().value_type == ScalarType.STRING


class TestDeclaracionFibLevels:
    """fib.levels: NON_SERVIBLE, declaracion-only (P08b-D1-04, LOTE 5).

    Calcada de vp.hvn/vp.lvn: comparte rango, snapshot (0027) y param con sus tres
    hermanas servibles -- si divergiera, un consumidor futuro (I-02) que se apoyara en
    su `consumes` para derivar una fuente escalar leeria un grafo distinto del que
    alimenta a nearest_level/level_pct/direction.
    """

    def test_es_non_servible_decimal_nominal_recursive(self) -> None:
        d = fib_levels_declaration()
        assert d.source_id == FIB_LEVELS_SOURCE_ID == "fib.levels"
        assert d.servibility == Servibility.NON_SERVIBLE
        assert d.value_type == ScalarType.DECIMAL
        assert d.memory_model == MemoryModel.RECURSIVE

    def test_comparte_forma_con_nearest_level(self) -> None:
        niveles = fib_levels_declaration()
        nivel = fib_nearest_level_declaration()
        assert niveles.cache_key_schema == nivel.cache_key_schema
        assert niveles.consumes == nivel.consumes
        assert niveles.overridable_params == nivel.overridable_params
        assert [p.name for p in niveles.params] == [p.name for p in nivel.params]
        assert niveles.sharing_scope == nivel.sharing_scope

    def test_solo_cambia_la_servibility(self) -> None:
        assert fib_nearest_level_declaration().servibility == Servibility.CONTINUOUS
        assert fib_levels_declaration().servibility == Servibility.NON_SERVIBLE


class TestTokenDeDireccion:
    """fib_direction_token: el token servible, sin vocabulario nuevo."""

    def test_los_tokens_son_los_del_enum_existente(self) -> None:
        # NO se inventa vocabulario: los dos unicos tokens posibles son los .value del
        # FibDirection que ya devolvia direction().
        posibles = {m.value for m in FibDirection}
        assert posibles == {"above", "below"}
        for price in (_d(-500), _d(0), _d(40), _d(100), _d(500)):
            assert fib_direction_token(_d(100), _d(0), price) in posibles

    def test_coincide_con_la_funcion_pura_direction(self) -> None:
        # El token ES direction(...).value: una sola fuente de verdad para la regla.
        for price in (_d(-200), _d("38.2"), _d(40), _d(99), _d(300)):
            assert fib_direction_token(_d(100), _d(0), price) == (
                direction(_d(100), _d(0), price).value
            )

    def test_above_y_below_se_distinguen_de_verdad(self) -> None:
        # Muerde: un token constante pasaria los dos tests de arriba.
        assert fib_direction_token(_d(100), _d(0), _d(500)) == "above"
        assert fib_direction_token(_d(100), _d(0), _d(-500)) == "below"

    def test_rango_degenerado_usa_el_mismo_criterio_que_el_fallback_numerico(
        self,
    ) -> None:
        # fib_levels rechaza el rango cero, pero el materializador no puede dejar hueco:
        # con el grid colapsado en range_high, la direccion sale de comparar el precio
        # con ESE nivel -- el mismo que fib_output devuelve como nearest_level.
        with pytest.raises(ValueError):
            direction(_d(100), _d(100), _d(150))
        assert fib_output(_d(100), _d(100), _d(150), FibOutput.NEAREST_LEVEL) == _d(100)
        assert fib_direction_token(_d(100), _d(100), _d(150)) == "above"
        assert fib_direction_token(_d(100), _d(100), _d(50)) == "below"
        # Frontera: price == nivel colapsado -> ABOVE, como el empate de direction().
        assert fib_direction_token(_d(100), _d(100), _d(100)) == "above"
