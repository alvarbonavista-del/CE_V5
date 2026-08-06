"""Tests del nucleo de imbalance apilado y de sus dos fuentes (P08c-CONF-05).

Celdas construidas a mano: el nucleo es puro (sin BD, sin sesion), asi que se prueba en
aislamiento total. Cubre los casos del pre-registro F5, el puente de vocabulario
ask/bid <-> buy/sell (donde esto se invierte si se lee deprisa), la mordida de los dos
parametros y el modelo de memoria declarado.
"""

from __future__ import annotations

from decimal import Decimal

from ce_v5.platform.rules.imbalance import (
    IMBALANCE_BUY_STACK_SOURCE_ID,
    IMBALANCE_RATIO,
    IMBALANCE_SELL_STACK_SOURCE_ID,
    MIN_STACK,
    declarations,
    detect_stacked_imbalance,
)
from ce_v5.platform.rules.rawfootprint import MARKET_FOOTPRINT_SOURCE_ID
from source.datasource import MemoryModel, Servibility, SourceType
from source.families.footprint import FootprintCell
from source.rules.scalar import ScalarType

_D = Decimal
_UN_TERCIO = _D(1) / _D(3)
_DOS_TERCIOS = _D(2) / _D(3)


def _celda(price: int, buy: int, sell: int) -> FootprintCell:
    """Una celda del footprint. delta se valida contra buy-sell en el contrato."""
    return FootprintCell(
        price=_D(price),
        buy_volume=_D(buy),
        sell_volume=_D(sell),
        delta=_D(buy) - _D(sell),
    )


# --- Casos del pre-registro -----------------------------------------------------


def test_sin_desequilibrio_no_hay_pila() -> None:
    """Barra equilibrada (10 contra 10 en todos los niveles): 0 por los dos lados."""
    cells = [_celda(100 + i, 10, 10) for i in range(4)]
    assert detect_stacked_imbalance(cells) == (_D(0), _D(0))


def test_tres_niveles_apilados_dan_fuerza_plena() -> None:
    """Corrida de 3 == MIN_STACK -> min(1, 3/3) = 1, y el otro lado se queda en 0."""
    cells = [_celda(100, 10, 10), *(_celda(101 + i, 30, 1) for i in range(3))]
    buy, sell = detect_stacked_imbalance(cells)
    assert buy == _D(1)
    assert sell == _D(0)


def test_una_sola_corrida_da_un_tercio() -> None:
    """Corrida de 1 -> min(1, 1/3). El sell_volume alto de 101 corta la racha en 102."""
    cells = [
        _celda(100, 10, 10),
        _celda(101, 30, 10),
        _celda(102, 10, 10),
        _celda(103, 10, 10),
    ]
    buy, sell = detect_stacked_imbalance(cells)
    assert buy == _UN_TERCIO
    assert sell == _D(0)


def test_los_dos_lados_pueden_estar_activos_a_la_vez() -> None:
    """No son excluyentes: venta apilada abajo y compra apilada arriba, misma barra."""
    cells = [
        _celda(100, 1, 60),
        _celda(101, 1, 60),
        _celda(102, 1, 1),
        _celda(103, 30, 1),
        _celda(104, 30, 1),
    ]
    buy, sell = detect_stacked_imbalance(cells)
    assert buy == _DOS_TERCIOS
    assert sell == _DOS_TERCIOS


def test_se_toma_la_corrida_MAXIMA_no_la_ultima() -> None:
    """Dos rachas en la misma barra: manda la LARGA, aunque no sea la que cierra.

    Se mide con min_stack=6 A PROPOSITO: con el 3 por defecto la racha larga satura a 1
    y el test pasaria igual aunque el nucleo devolviera la ultima racha o la suma. Con 6
    los dos candidatos se separan (3/6 = 0.5 frente a 1/6) y la asercion distingue de
    verdad cual se tomo.
    """
    cells = [
        _celda(100, 10, 10),
        # Racha larga, 3 niveles (30 >= 3 * el sell de abajo).
        _celda(101, 30, 1),
        _celda(102, 30, 1),
        _celda(103, 30, 1),
        # Corte real: 1 < 3 * 1.
        _celda(104, 1, 1),
        # Racha corta, 1 nivel.
        _celda(105, 30, 1),
    ]
    buy, _ = detect_stacked_imbalance(cells, min_stack=6)
    assert buy == _D("0.5")


# --- El puente de vocabulario y los guardas -------------------------------------


def test_el_imbalance_de_compra_mira_al_nivel_de_ABAJO() -> None:
    """DIAGONAL, no vertical: buy[P] contra sell[P-1], nunca contra sell[P].

    Si comparase el MISMO nivel consigo mismo, esta barra puntuaria (30 >= 3*1 dentro de
    la celda 101). Como mira abajo, 30 contra el sell de 100 (que vale 90) no llega.
    """
    cells = [_celda(100, 1, 90), _celda(101, 30, 1)]
    buy, _ = detect_stacked_imbalance(cells)
    assert buy == _D(0)


def test_el_imbalance_de_venta_mira_al_nivel_de_ARRIBA() -> None:
    """Simetrico: sell[P] contra buy[P+1]."""
    cells = [_celda(100, 1, 30), _celda(101, 1, 1)]
    _, sell = detect_stacked_imbalance(cells)
    assert sell == _UN_TERCIO


def test_denominador_cero_con_agresion_viva_SI_cuenta() -> None:
    """Agresion compradora sin nada enfrente es el desequilibrio maximo, no empate."""
    cells = [_celda(100, 0, 0), *(_celda(101 + i, 5, 0) for i in range(3))]
    buy, _ = detect_stacked_imbalance(cells)
    assert buy == _D(1)


def test_una_barra_MUERTA_no_puntua_como_pila_perfecta() -> None:
    """EL GUARDA QUE MAS IMPORTA. Sin el `> 0`, 0 >= 3*0 seria True en cada nivel y una
    barra sin un solo trade saldria con fuerza 1 por los dos lados -- ruido puro
    inyectado en F5 justo en las barras que menos informacion tienen.
    """
    cells = [_celda(100 + i, 0, 0) for i in range(5)]
    assert detect_stacked_imbalance(cells) == (_D(0), _D(0))


def test_menos_de_dos_celdas_no_tiene_pareja_diagonal() -> None:
    assert detect_stacked_imbalance([]) == (_D(0), _D(0))
    assert detect_stacked_imbalance([_celda(100, 50, 0)]) == (_D(0), _D(0))


# --- Mordida de los parametros y determinismo -----------------------------------


def test_el_ratio_gobierna_el_umbral() -> None:
    """Justo en el limite (30 == 3*10) cuenta; un pelo por debajo (29) no."""
    justo = [_celda(100, 10, 10), _celda(101, 30, 10), _celda(102, 10, 10)]
    debajo = [_celda(100, 10, 10), _celda(101, 29, 10), _celda(102, 10, 10)]
    assert detect_stacked_imbalance(justo)[0] == _UN_TERCIO
    assert detect_stacked_imbalance(debajo)[0] == _D(0)
    # Y subiendo el ratio a 10, la misma barra "justa" deja de contar.
    assert detect_stacked_imbalance(justo, ratio=_D(10))[0] == _D(0)


def test_min_stack_gobierna_la_escala_de_la_fuerza() -> None:
    """Misma corrida de 3: con MIN_STACK 3 satura a 1; con 5 se queda en 0.6."""
    cells = [_celda(100, 10, 10), *(_celda(101 + i, 30, 1) for i in range(3))]
    assert detect_stacked_imbalance(cells)[0] == _D(1)
    assert detect_stacked_imbalance(cells, min_stack=5)[0] == _D("0.6")


def test_es_determinista_bit_a_bit() -> None:
    """ADR-007: misma entrada -> misma salida, digito a digito."""
    cells = [_celda(100, 10, 10), _celda(101, 30, 10), _celda(102, 10, 10)]
    una = detect_stacked_imbalance(cells)
    otra = detect_stacked_imbalance(cells)
    assert una == otra
    assert [str(v) for v in una] == [str(v) for v in otra]


def test_las_semillas_son_las_del_pre_registro() -> None:
    assert IMBALANCE_RATIO == _D("3.0")
    assert MIN_STACK == 3


# --- Declaracion al catalogo ----------------------------------------------------


def test_las_dos_fuentes_se_declaran_point_local() -> None:
    """POINT_LOCAL y no WINDOWED (DICTAMEN P08c-CONF-05).

    La pila se mide DENTRO de la vela con ratio y minimo fijos: no hay umbral adaptativo
    ni percentil que pida ventana. Declararla WINDOWED dejaria en el contrato una
    ventana que el nucleo no lee, y cegaria las primeras barras con un warm-up sin
    motivo. Este candado impide que se cambie sin darse cuenta.
    """
    decls = declarations()
    assert {d.source_id for d in decls} == {
        IMBALANCE_BUY_STACK_SOURCE_ID,
        IMBALANCE_SELL_STACK_SOURCE_ID,
    }
    for d in decls:
        assert d.source_type is SourceType.OBSERVABLE
        assert d.servibility is Servibility.CONTINUOUS
        assert d.memory_model is MemoryModel.POINT_LOCAL
        assert d.value_type is ScalarType.DECIMAL
        # DAG honesto: solo el footprint. El nucleo no toca OHLC.
        assert d.consumes == (MARKET_FOOTPRINT_SOURCE_ID,)
        assert d.overridable_params == ()
        assert {p.name for p in d.params} == {"imbalance_ratio", "min_stack"}
        # Los dos parametros entran en la clave: cambiarlos cambia la identidad.
        assert "imbalance_ratio" in d.cache_key_schema
        assert "min_stack" in d.cache_key_schema
