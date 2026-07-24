"""Tests del MOTOR DEL LIBRO L2 CON ESTADO (ADR-014, ADR-006).

Alimentan el motor con SECUENCIAS CONTROLADAS -- foto + deltas construidos a mano -- y
comprueban lo que lo separa del motor de trades: aqui el ORDEN es la verdad y el estado
IMPORTA. Cero red, cero reloj, cero hilos: el test escribe el guion y el motor lo
aplica.

Los cinco casos del DoD, uno por comportamiento de fondo:
- HUECO: un salto de secuencia -> senal de resync + libro incompleto (fail-safe).
- RESET: una foto reenviada a mitad (Bybit) -> el libro se reconstruye desde ella.
- DUPLICADO: un delta ya aplicado -> ignorado, el libro sigue igual y completo.
- FUERA DE ORDEN: un delta que no encadena -> tratado como hueco (orden obligatorio).
- SNAPSHOT CORRUPTO: una foto malformada -> rechazada por el contrato; el motor NO
  construye un libro invalido.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ce_v5.platform.market.orderbook_book import (
    OrderbookBook,
    RawOrderbookRejected,
    RawOrderbookRejectionReason,
)
from source.families.market import RawOrderbookDelta, RawOrderbookSeed


def _seed(**overrides: object) -> RawOrderbookSeed:
    base: dict[str, object] = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTC-USDT",
        "bids": [("100.0", "1.0"), ("99.0", "2.0")],
        "asks": [("101.0", "1.5"), ("102.0", "3.0")],
        "base_sequence": 100,
    }
    base.update(overrides)
    return RawOrderbookSeed(**base)  # type: ignore[arg-type]


def _delta(**overrides: object) -> RawOrderbookDelta:
    base: dict[str, object] = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTC-USDT",
        "bids": [],
        "asks": [],
    }
    base.update(overrides)
    return RawOrderbookDelta(**base)  # type: ignore[arg-type]


class TestSemilla:
    def test_una_foto_valida_arranca_un_libro_completo(self) -> None:
        book = OrderbookBook()
        # Antes de la foto el libro NO es de fiar: la senal de resync arranca encendida.
        assert not book.seeded
        assert not book.is_complete
        assert book.resync_required

        book.seed(_seed())

        assert book.seeded
        assert book.is_complete
        assert not book.resync_required
        assert book.best_bid() == (Decimal("100.0"), Decimal("1.0"))
        assert book.best_ask() == (Decimal("101.0"), Decimal("1.5"))
        assert book.stream_id() == "market:orderbook:binance:spot:BTC-USDT"

    def test_identidad_al_construir_sin_semilla(self) -> None:
        # Opcion B: un libro puede saber QUE stream es antes de sembrar (para emitir la
        # frontera sin semilla). Trae identidad, pero sin semilla, incompleto y vacio.
        book = OrderbookBook(identity=("binance", "spot", "BTC-USDT"))
        assert book.exchange == "binance"
        assert book.market_type == "spot"
        assert book.symbol == "BTC-USDT"
        assert book.stream_id() == "market:orderbook:binance:spot:BTC-USDT"
        assert not book.seeded
        assert not book.is_complete
        assert book.bids() == {} and book.asks() == {}

    def test_la_semilla_sobrescribe_la_identidad_de_construccion(self) -> None:
        # seed() adopta la identidad de la foto; como _seeded era False no dispara la
        # verificacion de pertenencia. Aqui la foto coincide, y el libro queda completo.
        book = OrderbookBook(identity=("binance", "spot", "BTC-USDT"))
        book.seed(_seed())
        assert book.seeded
        assert book.is_complete
        assert book.stream_id() == "market:orderbook:binance:spot:BTC-USDT"

    def test_un_delta_sin_foto_previa_pide_resync(self) -> None:
        # Sin punto de partida un delta no significa nada: se levanta la senal, no se
        # inventa un libro de la nada.
        book = OrderbookBook()
        book.apply(_delta(first_update_id=101, final_update_id=102))
        assert not book.is_complete
        assert book.resync_required
        assert book.bids() == {}


class TestHueco:
    def test_un_salto_de_secuencia_pide_resync_y_marca_incompleto(self) -> None:
        # Binance encadena por U == u_previo + 1. Tras la foto (lastUpdateId=100), un
        # evento que empieza en 106 se salto el 101..105: hueco. FAIL-SAFE.
        book = OrderbookBook()
        book.seed(_seed())
        assert book.is_complete

        book.apply(_delta(first_update_id=106, final_update_id=110))

        assert not book.is_complete
        assert book.resync_required
        # El motor NO pide la foto por la red: solo levanta la senal (eso es del
        # cableado).

    def test_en_hueco_los_deltas_siguientes_no_recomponen_a_ciegas(self) -> None:
        # Un libro roto no se recompone encadenando: se queda incompleto hasta una foto.
        # Aunque llegue el delta que "tocaba", sigue sin ser de fiar (le falta lo del
        # medio).
        book = OrderbookBook()
        book.seed(_seed())
        book.apply(_delta(first_update_id=106, final_update_id=110))  # hueco

        book.apply(
            _delta(first_update_id=111, final_update_id=112, bids=[("100.0", "9")])
        )

        assert not book.is_complete
        assert book.resync_required
        # El delta NO se aplico: el bid 100.0 sigue como en la foto.
        assert book.bids()[Decimal("100.0")] == Decimal("1.0")

    def test_una_foto_nueva_resuelve_el_hueco(self) -> None:
        # La UNICA salida de un resync: una foto nueva. seed() reconstruye y recupera.
        book = OrderbookBook()
        book.seed(_seed())
        book.apply(_delta(first_update_id=106, final_update_id=110))  # hueco
        assert book.resync_required

        book.seed(
            _seed(base_sequence=200, bids=[("50.0", "7.0")], asks=[("60.0", "8.0")])
        )

        assert book.is_complete
        assert not book.resync_required
        assert book.bids() == {Decimal("50.0"): Decimal("7.0")}


class TestReset:
    def test_una_foto_reenviada_reconstruye_el_libro(self) -> None:
        # Bybit reinicia su updateId a 1 y reenvia una FOTO cuando su servicio se
        # reinicia: ese mensaje NO encadena, RECONSTRUYE el libro desde cero.
        book = OrderbookBook()
        book.seed(_seed(exchange="bybit", base_sequence=50, bids=[("100.0", "1.0")]))
        book.apply(_delta(exchange="bybit", update_id=51, bids=[("99.0", "2.0")]))
        assert book.bids() == {
            Decimal("100.0"): Decimal("1.0"),
            Decimal("99.0"): Decimal("2.0"),
        }

        # A mitad del flujo, un snapshot (u==1, is_snapshot): el libro entero se
        # sustituye.
        book.apply(
            _delta(
                exchange="bybit",
                update_id=1,
                is_snapshot=True,
                bids=[("200.0", "5.0")],
                asks=[("201.0", "6.0")],
            )
        )

        assert book.bids() == {Decimal("200.0"): Decimal("5.0")}
        assert book.asks() == {Decimal("201.0"): Decimal("6.0")}
        assert book.is_complete
        assert not book.resync_required

    def test_un_reset_recupera_de_un_hueco(self) -> None:
        # Un snapshot de Bybit tambien recupera de un resync: es una foto, como seed().
        book = OrderbookBook()
        book.seed(_seed(exchange="bybit", base_sequence=50))
        book.apply(_delta(exchange="bybit", update_id=99))  # salto 51->99: hueco
        assert book.resync_required

        book.apply(
            _delta(
                exchange="bybit", update_id=1, is_snapshot=True, bids=[("10.0", "1.0")]
            )
        )

        assert book.is_complete
        assert not book.resync_required
        assert book.bids() == {Decimal("10.0"): Decimal("1.0")}


class TestDuplicado:
    def test_un_delta_ya_aplicado_se_ignora_sin_cambiar_el_libro(self) -> None:
        # Binance descarta u <= la ultima secuencia aplicada. Un reenvio (el caso normal
        # tras una reconexion) no vuelve a mover el libro ni lo marca incompleto.
        book = OrderbookBook()
        book.seed(_seed())
        book.apply(
            _delta(first_update_id=101, final_update_id=103, bids=[("100.0", "5.0")])
        )
        estado = book.bids()
        assert estado[Decimal("100.0")] == Decimal("5.0")

        # El MISMO delta otra vez: u=103 <= ultimo(103) -> duplicado, ignorado.
        book.apply(
            _delta(first_update_id=101, final_update_id=103, bids=[("100.0", "5.0")])
        )

        assert book.bids() == estado
        assert book.is_complete
        assert not book.resync_required


class TestPuenteBinance:
    """El PRIMER delta de Binance ABARCA la foto (regla oficial I-02): U<=base+1<=u, con
    U posiblemente MENOR que base+1. El fix del hallazgo de la Tanda V.
    """

    def test_el_primer_delta_que_abarca_la_foto_se_aplica(self) -> None:
        # base=100; delta U=98, u=103: U (98) <= base+1 (101) <= u (103) -> ABARCA.
        # Antes se rechazaba como hueco (exigia U==101); ahora engancha el puente.
        book = OrderbookBook()
        book.seed(_seed())  # base_sequence=100
        assert book.is_complete

        book.apply(
            _delta(first_update_id=98, final_update_id=103, bids=[("100.5", "7.0")])
        )

        assert book.is_complete
        assert not book.resync_required
        assert book.sequence == 103  # last avanza al u del puente.
        assert book.bids()[Decimal("100.5")] == Decimal("7.0")

    def test_tras_el_puente_la_continuidad_es_estricta(self) -> None:
        # Enganchado el puente (last=103), el SIGUIENTE ya no abarca: exige U==104.
        book = OrderbookBook()
        book.seed(_seed())
        book.apply(_delta(first_update_id=98, final_update_id=103))  # puente, last=103

        # U=104 == last+1: encadena.
        book.apply(
            _delta(first_update_id=104, final_update_id=105, bids=[("99.0", "9")])
        )
        assert book.is_complete
        assert book.sequence == 105
        assert book.bids()[Decimal("99.0")] == Decimal("9")

        # Un salto tras el puente SI es hueco (ya no hay abarque): U=200 no encadena.
        book.apply(_delta(first_update_id=200, final_update_id=201))
        assert not book.is_complete
        assert book.resync_required

    def test_un_delta_con_u_menor_o_igual_a_base_se_descarta(self) -> None:
        # u <= base es un reenvio ya cubierto por la foto: DUPLICADO, no toca el libro y
        # el puente sigue pendiente. Luego el delta que abarca engancha.
        book = OrderbookBook()
        book.seed(_seed())  # base=100
        book.apply(
            _delta(first_update_id=90, final_update_id=100, bids=[("100.0", "9.0")])
        )
        # Descartado: el bid 100.0 sigue como en la foto (no se aplico el 9.0).
        assert book.bids()[Decimal("100.0")] == Decimal("1.0")
        assert book.is_complete

        # Ahora SI llega el que abarca (U=99 <= 101 <= u=104): engancha.
        book.apply(
            _delta(first_update_id=99, final_update_id=104, bids=[("100.0", "3.0")])
        )
        assert book.is_complete
        assert book.sequence == 104
        assert book.bids()[Decimal("100.0")] == Decimal("3.0")

    def test_si_ningun_delta_abarca_es_hueco_real(self) -> None:
        # base=100; delta U=106 (> base+1=101): NO abarca -> hueco real. FAIL-SAFE.
        book = OrderbookBook()
        book.seed(_seed())
        book.apply(_delta(first_update_id=106, final_update_id=110))
        assert not book.is_complete
        assert book.resync_required

    def test_okx_no_usa_abarque_el_primer_delta_encadena_exacto(self) -> None:
        # OKX: foto (seqId=base); el primer delta encadena por prevSeqId==base EXACTO.
        # Un prevSeqId que "abarcase" (menor que base) es hueco: OKX no tiene span.
        book = OrderbookBook()
        book.seed(_seed(exchange="okx", base_sequence=100))
        book.apply(
            _delta(exchange="okx", seq_id=101, prev_seq_id=100, bids=[("100.0", "2")])
        )
        assert book.is_complete
        assert book.sequence == 101

        book2 = OrderbookBook()
        book2.seed(_seed(exchange="okx", base_sequence=100))
        # prevSeqId=98 < base: no encadena y OKX no abarca -> hueco.
        book2.apply(_delta(exchange="okx", seq_id=105, prev_seq_id=98))
        assert not book2.is_complete
        assert book2.resync_required

    def test_bybit_no_usa_abarque_el_primer_delta_encadena_exacto(self) -> None:
        # Bybit: primer delta u == base+1 EXACTO. Un u que abarcase no existe en Bybit.
        book = OrderbookBook()
        book.seed(_seed(exchange="bybit", base_sequence=100))
        book.apply(_delta(exchange="bybit", update_id=101, bids=[("100.0", "2")]))
        assert book.is_complete
        assert book.sequence == 101

        book2 = OrderbookBook()
        book2.seed(_seed(exchange="bybit", base_sequence=100))
        book2.apply(_delta(exchange="bybit", update_id=105))  # salto: hueco
        assert not book2.is_complete
        assert book2.resync_required

    def test_un_nivel_a_tamano_cero_borra_el_nivel(self) -> None:
        # tamano 0 en un delta no es un nivel a precio cero: es la orden de BORRAR ese
        # nivel, tal como publican los exchanges el vaciado de un nivel del libro.
        book = OrderbookBook()
        book.seed(_seed())
        assert Decimal("99.0") in book.bids()

        book.apply(
            _delta(first_update_id=101, final_update_id=101, bids=[("99.0", "0")])
        )

        assert Decimal("99.0") not in book.bids()
        assert book.is_complete


class TestFueraDeOrden:
    def test_un_delta_que_no_encadena_es_un_hueco(self) -> None:
        # OKX encadena por prevSeqId == seqId del anterior. Si el mensaje 101->102 llega
        # ANTES que el 100->101, su prevSeqId (101) no encaja con lo ultimo aplicado
        # (100): la cadena se rompe. El orden es OBLIGATORIO, asi que se trata como
        # hueco.
        book = OrderbookBook()
        book.seed(_seed(exchange="okx", base_sequence=100))

        book.apply(
            _delta(exchange="okx", seq_id=102, prev_seq_id=101)
        )  # llega antes de tiempo

        assert not book.is_complete
        assert book.resync_required

    def test_okx_en_orden_encadena_y_avanza(self) -> None:
        # El caso bueno de OKX: prevSeqId encaja con lo ultimo aplicado -> aplica y
        # avanza.
        book = OrderbookBook()
        book.seed(_seed(exchange="okx", base_sequence=100))

        book.apply(
            _delta(exchange="okx", seq_id=101, prev_seq_id=100, bids=[("100.0", "9.0")])
        )
        book.apply(
            _delta(exchange="okx", seq_id=102, prev_seq_id=101, asks=[("101.0", "8.0")])
        )

        assert book.is_complete
        assert book.bids()[Decimal("100.0")] == Decimal("9.0")
        assert book.asks()[Decimal("101.0")] == Decimal("8.0")

    def test_okx_keepalive_y_mantenimiento_no_son_hueco(self) -> None:
        # Las DOS excepciones de OKX que NO son hueco: keepalive (seqId==prevSeqId: el
        # libro no cambio) y mantenimiento (seqId<prevSeqId: OKX reinicio su contador).
        # Ninguna marca el libro incompleto ni pide resync.
        book = OrderbookBook()
        book.seed(_seed(exchange="okx", base_sequence=100))
        antes = book.bids()

        book.apply(_delta(exchange="okx", seq_id=100, prev_seq_id=100))  # keepalive
        book.apply(_delta(exchange="okx", seq_id=90, prev_seq_id=100))  # mantenimiento

        assert book.is_complete
        assert not book.resync_required
        assert book.bids() == antes


class TestSnapshotCorrupto:
    def test_un_precio_ilegible_en_la_foto_se_rechaza(self) -> None:
        # La foto es entrada NO confiable (ADR-006). Un precio que no es un numero se
        # rechaza y el motor NO construye un libro invalido: se queda sin arrancar.
        book = OrderbookBook()
        with pytest.raises(RawOrderbookRejected) as excinfo:
            book.seed(_seed(bids=[("abc", "1.0")]))

        assert excinfo.value.reason is RawOrderbookRejectionReason.MALFORMED_NUMBER
        assert not book.seeded
        assert not book.is_complete
        assert book.bids() == {}

    @pytest.mark.parametrize(
        ("bids", "reason"),
        [
            (
                [("0", "1.0")],
                RawOrderbookRejectionReason.CONTRACT_VIOLATION,
            ),  # precio 0
            (
                [("-1", "1.0")],
                RawOrderbookRejectionReason.CONTRACT_VIOLATION,
            ),  # precio <0
            (
                [("NaN", "1.0")],
                RawOrderbookRejectionReason.CONTRACT_VIOLATION,
            ),  # no finito
            (
                [("100.0", "-1")],
                RawOrderbookRejectionReason.CONTRACT_VIOLATION,
            ),  # tamano <0
            (
                [("100.0",)],
                RawOrderbookRejectionReason.CONTRACT_VIOLATION,
            ),  # nivel malformado
        ],
    )
    def test_niveles_que_violan_el_contrato_se_rechazan(
        self, bids: list[object], reason: RawOrderbookRejectionReason
    ) -> None:
        book = OrderbookBook()
        with pytest.raises(RawOrderbookRejected) as excinfo:
            book.seed(_seed(bids=bids))
        assert excinfo.value.reason is reason
        assert not book.seeded

    def test_una_foto_corrupta_no_pisa_un_libro_bueno(self) -> None:
        # ATOMICIDAD: si ya habia un libro bueno, una foto corrupta se rechaza y el
        # libro anterior sigue INTACTO. El motor jamas se queda con un libro a medias.
        book = OrderbookBook()
        book.seed(_seed())
        bueno = book.bids()

        with pytest.raises(RawOrderbookRejected):
            book.seed(_seed(base_sequence=200, bids=[("abc", "1.0")]))

        assert book.bids() == bueno
        assert book.is_complete
        assert book._last_seq == 100  # noqa: SLF001 - no avanzo la secuencia base.

    def test_un_delta_corrupto_se_rechaza_sin_dejar_el_libro_a_medias(self) -> None:
        # Un delta con un lado podrido se valida ENTERO antes de mutar: se rechaza y el
        # libro se queda como estaba. Como no avanza la secuencia, el proximo delta
        # encadenara mal y el hueco saltara por si solo.
        book = OrderbookBook()
        book.seed(_seed())
        antes = book.bids()

        with pytest.raises(RawOrderbookRejected):
            book.apply(
                _delta(
                    first_update_id=101,
                    final_update_id=101,
                    bids=[("98.0", "1.0")],
                    asks=[("abc", "1.0")],  # el segundo lado esta podrido
                )
            )

        # Ni el bid bueno del mismo delta entro: el rechazo es atomico.
        assert book.bids() == antes


class TestPertenencia:
    def test_un_delta_de_otro_simbolo_se_rechaza(self) -> None:
        # ANTI-SUPLANTACION: un delta de OTRO simbolo colado por este stream meteria la
        # profundidad de una moneda en el libro de otra.
        book = OrderbookBook()
        book.seed(_seed())
        with pytest.raises(RawOrderbookRejected) as excinfo:
            book.apply(
                _delta(symbol="ETH-USDT", first_update_id=101, final_update_id=101)
            )
        assert excinfo.value.reason is RawOrderbookRejectionReason.SYMBOL_MISMATCH

    def test_una_foto_de_otro_flujo_tras_arrancar_se_rechaza(self) -> None:
        book = OrderbookBook()
        book.seed(_seed())
        with pytest.raises(RawOrderbookRejected) as excinfo:
            book.seed(_seed(exchange="okx"))
        assert excinfo.value.reason is RawOrderbookRejectionReason.SYMBOL_MISMATCH


class TestExchangeNoSoportado:
    def test_un_delta_de_un_exchange_sin_regla_es_fallo_de_cableado(self) -> None:
        # FAIL-LOUD: el motor solo sabe encadenar los exchanges con adaptador. Un delta
        # de otro no es un dato podrido (rechazable), es un stream que nunca debio
        # llegar aqui.
        book = OrderbookBook()
        book.seed(_seed(exchange="kraken"))
        with pytest.raises(ValueError, match="regla de continuidad"):
            book.apply(_delta(exchange="kraken", update_id=1))
