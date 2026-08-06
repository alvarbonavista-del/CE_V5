"""Tests del bloque L2 de notrade: las 4 features del libro (P08c-CONF-05).

Ventanas construidas a mano con BookDepth inyectado: el nucleo es puro (sin BD, sin
sesion, sin contrato de la familia orderbook), asi que se prueba en aislamiento total.

Cubre los casos del pre-registro (equilibrado/lopsided, liquidez estable/salto, muro,
libro fino), el FAIL-SAFE de OBS-1 (barra sin frontier -> L2 = 0 y el score BAJA), la
aditividad sobre FP/Flow y el determinismo.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from ce_v5.platform.rules.notrade import (
    BookDepth,
    NoTradeCandle,
    NoTradeParams,
    NoTradeState,
    evaluate_no_trade,
)

_D = Decimal
_PARAMS = NoTradeParams()
_BARRAS = 10


def _libro(bid: str, ask: str, mayor: str = "50", medio: str = "50") -> BookDepth:
    return BookDepth(
        bid_size=_D(bid),
        ask_size=_D(ask),
        max_level_size=_D(mayor),
        mean_level_size=_D(medio),
    )


_LIBRO_NEUTRO = _libro("500", "500")


def _vela(indice: int, book: BookDepth | None = _LIBRO_NEUTRO) -> NoTradeCandle:
    """Vela corriente; solo el libro distingue los casos."""
    return NoTradeCandle(
        delta=_D(10 + indice % 3),
        volume=_D(100),
        high=_D(105),
        low=_D(95),
        close=_D(100 + indice % 2),
        open=_D(100),
        book=book,
    )


def _ventana(ultimo_libro: BookDepth | None) -> list[NoTradeCandle]:
    """Ventana neutra de 10 barras; la ULTIMA lleva el libro bajo prueba.

    Todas las barras previas comparten el mismo libro neutro, asi que cualquier
    diferencia de score entre dos llamadas sale del libro de la ultima barra y de nada
    mas.
    """
    base = [_vela(i) for i in range(_BARRAS - 1)]
    return [*base, replace(_vela(_BARRAS - 1), book=ultimo_libro)]


def _l2(ultimo_libro: BookDepth | None) -> Decimal:
    signal = evaluate_no_trade(_ventana(ultimo_libro), _PARAMS)
    assert signal is not None
    return signal.l2_instability


# --- Las cuatro features --------------------------------------------------------


def test_libro_equilibrado_no_dispara_desequilibrio() -> None:
    """(a) Bid y ask iguales: |B - A| = 0, la feature de desequilibrio no aporta."""
    equilibrado = _l2(_libro("500", "500"))
    lopsided = _l2(_libro("1000", "1"))
    assert lopsided > equilibrado


def test_libro_del_todo_a_un_lado_dispara_el_desequilibrio() -> None:
    """(b) Un lado vacio es |B - A| / (B + A) ~ 1, el maximo de la feature."""
    assert _l2(_libro("1000", "0")) > _l2(_libro("500", "500"))


def test_un_muro_muy_por_encima_de_la_media_dispara_el_spoof_proxy() -> None:
    """(c) max/mean alto = un nivel que sobresale del resto del libro.

    Es un PROXY y el nombre lo dice: distinguir un muro que se retira de uno real exige
    el delta-log, diferido a v5.1 en la 0020. Aqui solo se mide que sobresale.
    """
    normal = _l2(_libro("500", "500", mayor="50", medio="50"))
    muro = _l2(_libro("500", "500", mayor="500", medio="50"))
    assert muro > normal


def test_un_libro_fino_dispara_thin_book() -> None:
    """(d) Poca profundidad total = mas toxicidad. Es el UNICO de los cuatro que
    invierte: la columna lleva profundidad cruda y el 1 - norm() se aplica al final.
    """
    profundo = _l2(_libro("5000", "5000"))
    fino = _l2(_libro("1", "1"))
    assert fino > profundo


def test_un_salto_de_liquidez_dispara_liquidity_shift() -> None:
    """(e) |D_t - D_{t-1}| / D_{t-1}: el cambio RELATIVO frente a la barra anterior.

    Las nueve primeras barras tienen profundidad 1000; la ultima la multiplica por 20.
    Se compara contra una ultima barra que mantiene la profundidad, cambiando SOLO eso.
    """
    estable = _l2(_libro("500", "500"))
    salto = _l2(_libro("10000", "10000"))
    assert salto > estable


# --- FAIL-SAFE de OBS-1 ---------------------------------------------------------


def test_sin_frontier_el_bloque_l2_es_cero() -> None:
    """(f) Barra sin libro -> L2 = 0. No se proyecta el valor de la barra anterior."""
    assert _l2(None) == _D(0)


def test_sin_frontier_el_score_BAJA_nunca_sube() -> None:
    """OBS-1, y es la asercion que mas importa del fichero.

    MISMA barra final salvo el libro: sin frontier el score tiene que quedarse en
    FP + Flow (limite inferior) y FP y Flow tienen que salir IDENTICOS. Si el fail-safe
    proyectara el valor anterior, o si los otros dos bloques se rescalaran para "tapar"
    el hueco, esta comparacion lo caza. Inventar toxicidad donde no se observo es peor
    que quedarse corto: el score gobierna un veto de entrada.
    """
    con = evaluate_no_trade(_ventana(_libro("1000", "1", mayor="600")), _PARAMS)
    sin = evaluate_no_trade(_ventana(None), _PARAMS)
    assert con is not None
    assert sin is not None
    assert sin.no_trade_score <= con.no_trade_score
    assert sin.l2_instability == _D(0)
    assert sin.no_trade_score == sin.footprint_ineff + sin.flow_dislocation
    # Los otros dos bloques NO se enteran de que L2 falto.
    assert sin.footprint_ineff == con.footprint_ineff
    assert sin.flow_dislocation == con.flow_dislocation


def test_una_ventana_entera_sin_libro_no_normaliza_una_columna_inventada() -> None:
    """Sin libro en NINGUNA barra, L2 = 0 y no 0.5 * 35.

    Muerde de verdad: _norm devuelve 0,5 ante una columna constante, asi que rellenar
    con ceros y normalizar habria dado ~17,5 de toxicidad fantasma en cada barra de un
    flujo sin libro. Por eso _l2_columns devuelve None en vez de una columna de ceros.
    """
    sin_libro = [_vela(i, book=None) for i in range(_BARRAS)]
    signal = evaluate_no_trade(sin_libro, _PARAMS)
    assert signal is not None
    assert signal.l2_instability == _D(0)
    assert signal.no_trade_score == signal.footprint_ineff + signal.flow_dislocation


# --- Aditividad, tope y pesos ---------------------------------------------------


def test_los_sub_pesos_l2_suman_uno() -> None:
    """(g) 0.30 + 0.30 + 0.20 + 0.20 = 1: el bloque escala a su peso y no mas."""
    p = _PARAMS
    total = p.w_imbalance_vol + p.w_liquidity_shift + p.w_spoof_proxy + p.w_thin_book
    assert total == _D(1)


def test_los_tres_bloques_suman_cien() -> None:
    """(h) 40 + 25 + 35: el tope TEORICO pasa de 65 a 100 sin tocar FP ni Flow."""
    p = _PARAMS
    assert p.fp_block_weight + p.flow_block_weight + p.l2_block_weight == _D(100)


def test_el_score_es_la_suma_de_los_tres_bloques_sin_rescalar() -> None:
    """(i) score = FP + Flow + L2, pesos ABSOLUTOS. Ningun bloque se renormaliza."""
    signal = evaluate_no_trade(_ventana(_libro("1000", "1")), _PARAMS)
    assert signal is not None
    assert signal.no_trade_score == (
        signal.footprint_ineff + signal.flow_dislocation + signal.l2_instability
    )
    assert signal.l2_instability > _D(0)


def test_el_bloque_l2_nunca_pasa_de_su_peso() -> None:
    """L2 acotado a [0, 35]: las cuatro features estan en [0,1] y sus pesos suman 1."""
    for libro in (
        _libro("1000", "0", mayor="1000", medio="1"),
        _libro("1", "1"),
        _libro("5000", "5000"),
        _libro("0.001", "0"),
    ):
        valor = _l2(libro)
        assert _D(0) <= valor <= _D(35)


def test_el_estado_toxic_ya_es_alcanzable() -> None:
    """(j) EL DESTOPE, medido donde se nota: el estado.

    Con el bloque L2 fijo a 0 el score no pasaba de 65, asi que la banda TOXIC (> 80)
    era INALCANZABLE por construccion -- estaba declarada y muerta --. Con L2 vivo se
    alcanza. Se comprueba el estado y no un numero exacto porque el maximo depende de
    la ventana; lo que importa es que la banda dejo de ser decorativa.
    """
    extremas: list[NoTradeCandle] = []
    for indice in range(_BARRAS):
        toxica = indice == _BARRAS - 1
        signo = 1 if indice % 2 == 0 else -1
        extremas.append(
            NoTradeCandle(
                delta=_D(10000000) * signo if toxica else _D(1) * signo,
                volume=_D(1000000) if toxica else _D(1),
                # OHLC COHERENTE (low <= open, close <= high): el cierre a mitad de un
                # rango minusculo. Con una vela imposible (close por encima del high) el
                # score sale mas alto, pero probaria algo que el mercado no puede dar.
                high=_D("100.01") if toxica else _D(200),
                low=_D(100) if toxica else _D(1),
                open=_D(100) if toxica else _D(50),
                close=_D("100.005") if toxica else _D(50),
                # Libro DIMINUTO y del todo desequilibrado, con un nivel dominante: las
                # cuatro features al maximo a la vez (thin_book exige profundidad
                # minima, y eso es compatible con estar del todo a un lado).
                book=_libro("0.001", "0", mayor="0.001", medio="0.0000001")
                if toxica
                else _libro("100000", "100000"),
            )
        )
    signal = evaluate_no_trade(extremas, _PARAMS)
    assert signal is not None
    assert signal.no_trade_score > _D("80")
    assert signal.state is NoTradeState.TOXIC
    # Y el bloque L2 llego a su peso completo: las cuatro features NO se estorban.
    assert signal.l2_instability > _D("34.9")


def test_es_determinista_bit_a_bit() -> None:
    """ADR-007: misma ventana -> mismo veredicto, digito a digito."""
    ventana = _ventana(_libro("1000", "1", mayor="600"))
    una = evaluate_no_trade(ventana, _PARAMS)
    otra = evaluate_no_trade(ventana, _PARAMS)
    assert una == otra
    assert una is not None
    assert otra is not None
    assert str(una.l2_instability) == str(otra.l2_instability)
    assert str(una.no_trade_score) == str(otra.no_trade_score)
