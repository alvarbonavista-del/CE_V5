"""Tests de vp.*: declaraciones ADR-008 + nucleo determinista del volume profile (P08c).

Dos bloques. (1) DECLARACIONES: forma ADR-008 de vp.poc/vah/val, bin_count como
parametro con su default, y el grafo consumes contra market.footprint (completo ->
valida; sin la base -> fail-loud). (2) NUCLEO: verificacion NUMERICA contra referente
CALCULADO a mano desde footprint conocido (DEC-VP-INPUT-01: paridad SEMANTICA, no
numerica contra v4). El volumen-a-precio es dado y el POC/VA/VAH/VAL correcto se
deriva a mano siguiendo la definicion (bin center, expansion 70%, empate al alza).
HVN/LVN NO se prueban aqui: llegan tras su AHP.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ce_v5.platform.rules.catalog import DataSourceCatalog, MissingDependencyError
from ce_v5.platform.rules.rawfootprint import (
    MARKET_FOOTPRINT_SOURCE_ID,
    market_footprint_declaration,
)
from ce_v5.platform.rules.volume_profile import (
    VP_HVN_SOURCE_ID,
    VP_LVN_SOURCE_ID,
    VP_POC_SOURCE_ID,
    VP_VAH_SOURCE_ID,
    VP_VAL_SOURCE_ID,
    VolumeNodeParams,
    VolumeProfileError,
    compute_volume_nodes,
    compute_volume_profile,
    select_hvn_price,
    select_lvn_price,
    vp_hvn_declaration,
    vp_lvn_declaration,
    vp_poc_declaration,
    vp_vah_declaration,
    vp_val_declaration,
)
from source.datasource import MemoryModel, Servibility, SourceType
from source.families.footprint import FootprintCell, FootprintClosedPayload
from source.families.market import MarketType, Timeframe
from source.rules.scalar import ScalarType
from source.time import MaturityState

_TF = Timeframe.M1
_OPEN = 1_784_073_600_000  # alineado a M1 (divisible por 60_000).


class TestDeclaraciones:
    def test_las_tres_salidas_comparten_forma_adr008(self) -> None:
        for declaration in (
            vp_poc_declaration(),
            vp_vah_declaration(),
            vp_val_declaration(),
        ):
            assert declaration.source_type is SourceType.OBSERVABLE
            assert declaration.servibility is Servibility.CONTINUOUS
            assert declaration.memory_model is MemoryModel.WINDOWED
            assert declaration.value_type is ScalarType.DECIMAL
            assert declaration.consumes == (MARKET_FOOTPRINT_SOURCE_ID,)

    def test_source_ids_distintos_y_esperados(self) -> None:
        ids = {
            vp_poc_declaration().source_id,
            vp_vah_declaration().source_id,
            vp_val_declaration().source_id,
        }
        assert ids == {VP_POC_SOURCE_ID, VP_VAH_SOURCE_ID, VP_VAL_SOURCE_ID}
        assert len(ids) == 3

    def test_bin_count_es_parametro_con_default_50(self) -> None:
        # bin_count es PARAMETRO (entra en la cache_key); value_area_pct NO: es
        # definicion fija, no un tunable.
        declaration = vp_poc_declaration()
        params = {param.name: param for param in declaration.params}
        assert set(params) == {"bin_count"}
        bin_count = params["bin_count"]
        assert bin_count.value_type is ScalarType.INTEGER
        assert bin_count.default is not None
        assert bin_count.default.integer_value == 50
        assert "bin_count" in declaration.cache_key_schema
        assert "market_type" in declaration.cache_key_schema

    def test_dag_completo_valida_con_market_footprint(self) -> None:
        catalog = DataSourceCatalog()
        catalog.register(market_footprint_declaration())
        catalog.register(vp_poc_declaration())
        catalog.register(vp_vah_declaration())
        catalog.register(vp_val_declaration())
        catalog.validate()  # no lanza: grafo completo y aciclico.

    def test_vp_sin_market_footprint_falla_el_dag(self) -> None:
        # vp.* consume market.footprint: sin registrarla el grafo esta incompleto.
        catalog = DataSourceCatalog()
        catalog.register(vp_poc_declaration())
        with pytest.raises(MissingDependencyError):
            catalog.validate()


def _footprint(
    levels: list[tuple[str, str, str]],
    *,
    exchange: str = "binance",
    symbol: str = "BTC-USDT",
    open_time: int = _OPEN,
) -> FootprintClosedPayload:
    """Un footprint cerrado valido a partir de (precio, buy, sell) por nivel.

    Ordena las celdas por precio y cuadra los totales de barra, como exige el contrato.
    Para el volume profile solo cuenta buy+sell por precio; el reparto es libre.
    """
    cells = tuple(
        FootprintCell(
            price=Decimal(p),
            buy_volume=Decimal(b),
            sell_volume=Decimal(s),
            delta=Decimal(b) - Decimal(s),
        )
        for p, b, s in sorted(levels, key=lambda level: Decimal(level[0]))
    )
    bar_buy = sum((c.buy_volume for c in cells), Decimal(0))
    bar_sell = sum((c.sell_volume for c in cells), Decimal(0))
    return FootprintClosedPayload(
        exchange=exchange,
        market_type=MarketType.SPOT,
        symbol=symbol,
        timeframe=_TF,
        open_time=open_time,
        close_time=open_time + _TF.duration_ms,
        cells=cells,
        bar_buy_volume=bar_buy,
        bar_sell_volume=bar_sell,
        bar_delta=bar_buy - bar_sell,
        trade_count=len(cells),
        is_complete=True,
        maturity_state=MaturityState.CLOSED,
    )


class TestNucleoDeterminista:
    def test_precio_unico_da_poc_va_degenerados(self) -> None:
        # Todo el volumen a un precio: rango nulo -> POC=VAH=VAL=ese precio.
        profile = compute_volume_profile(
            [_footprint([("100", "10", "0")])], bin_count=5
        )
        assert profile.poc == Decimal("100")
        assert profile.vah == Decimal("100")
        assert profile.val == Decimal("100")

    def test_poc_y_value_area_sobre_un_perfil_conocido(self) -> None:
        # Precios 100..104, volumen 10/20/40/20/10 (total 100). bin_count=5 -> min=100,
        # max=104, ancho=0.8. Bins: {0:10,1:20,2:40,3:20,4:10}. POC=bin2 (centro 102.0).
        # VA 70% (objetivo 70): 40 -> +bin3(20, empate al alza)=60 -> +bin1(20)=80>=70.
        # VAL=centro bin1=101.2 ; VAH=centro bin3=102.8.
        profile = compute_volume_profile(
            [
                _footprint(
                    [
                        ("100", "10", "0"),
                        ("101", "20", "0"),
                        ("102", "40", "0"),
                        ("103", "20", "0"),
                        ("104", "10", "0"),
                    ]
                )
            ],
            bin_count=5,
        )
        assert profile.poc == Decimal("102.0")
        assert profile.vah == Decimal("102.8")
        assert profile.val == Decimal("101.2")

    def test_agrega_el_volumen_por_precio_a_traves_de_varias_barras(self) -> None:
        # El precio 102 aparece en DOS barras (30 y 20): el perfil suma 50 en 102.
        # Precios 100/102/104 -> vol 10/50/10, bin_count=5 (ancho 0.8): POC=bin de 102,
        # VA ya cubierta por el POC (50 de 70) -> POC=VAH=VAL=102.0.
        window = [
            _footprint([("100", "10", "0"), ("102", "30", "0")]),
            _footprint([("102", "20", "0"), ("104", "10", "0")]),
        ]
        profile = compute_volume_profile(window, bin_count=5)
        assert profile.poc == Decimal("102.0")
        assert profile.vah == Decimal("102.0")
        assert profile.val == Decimal("102.0")


class TestBordes:
    def test_ventana_vacia_es_error(self) -> None:
        with pytest.raises(VolumeProfileError, match="ventana vacia"):
            compute_volume_profile([], bin_count=5)

    def test_bin_count_no_positivo_es_error(self) -> None:
        with pytest.raises(VolumeProfileError, match="bin_count"):
            compute_volume_profile([_footprint([("100", "1", "0")])], bin_count=0)

    def test_mezclar_flujos_es_error(self) -> None:
        with pytest.raises(VolumeProfileError, match="mezcla flujos"):
            compute_volume_profile(
                [
                    _footprint([("100", "1", "0")], symbol="BTC-USDT"),
                    _footprint([("100", "1", "0")], symbol="ETH-USDT"),
                ],
                bin_count=5,
            )


class TestNodos:
    # Perfil de 10 bins (bin_width=1.0: min=100, max=110). Volumen por precio -> bin:
    # 100->b0=80, 103->b3=10, 105->b5=100 (POC), 107->b7=10, 110->b9=10. total=210,
    # ocupados=5, media=42. HVN si vol>media*1.5=63 y a >3 bins del POC: b0(80) a 5 del
    # POC -> HVN (centro 100.5); b5 es POC (excluido). VA cubre todo el rango [b0,b9].
    # LVN si vol<media*0.3=12.6 dentro de la VA: bins vacios (0) y b3/b7 (10). Orden por
    # volumen asc + indice asc, dedup a >3 bins: quedan b1 (centro 101.5) y b6 (106.5).
    _WINDOW = [
        _footprint(
            [
                ("100", "80", "0"),
                ("103", "10", "0"),
                ("105", "100", "0"),
                ("107", "10", "0"),
                ("110", "10", "0"),
            ]
        )
    ]

    def test_hvn_y_lvn_sobre_perfil_conocido(self) -> None:
        nodes = compute_volume_nodes(self._WINDOW, bin_count=10)
        assert nodes.hvn == (Decimal("100.5"),)
        assert nodes.lvn == (Decimal("101.5"), Decimal("106.5"))

    def test_precio_unico_no_da_nodos(self) -> None:
        # Rango nulo: sin distribucion, no hay HVN ni LVN.
        nodes = compute_volume_nodes([_footprint([("100", "10", "0")])], bin_count=10)
        assert nodes.hvn == ()
        assert nodes.lvn == ()

    def test_subir_el_umbral_hvn_por_parametro_lo_vacia(self) -> None:
        # Con hvn_multiplier alto (5.0), ningun bin supera media*5=210 -> sin HVN.
        params = VolumeNodeParams(hvn_multiplier=Decimal("5.0"))
        nodes = compute_volume_nodes(self._WINDOW, bin_count=10, params=params)
        assert nodes.hvn == ()


class TestSelectNodePrice:
    """select_hvn_price/select_lvn_price [DICTAMEN P08c-PIVOT-08-bis]: el PRIMER
    candidato del orden interno de deteccion (mismo _volume_node_indices que
    compute_volume_nodes), antes de reordenar por precio. Los umbrales se relajan via
    VolumeNodeParams para aislar la regla de SELECCION (mayor/menor volumen, empate a
    menor precio) de la mecanica de umbral/Value Area, ya cubierta por TestNodos.
    """

    def _hvn_window(self, low: str, high: str) -> list[FootprintClosedPayload]:
        # bin_count=9, min=100 max=109 (precio 109 cae en el ultimo bin por el tope de
        # _build_bins) -> bin_width=(109-100)/9=1.0 EXACTO. POC en idx4 (vol dominante,
        # a distancia 4 > adjacency_bins=3 de ambos extremos, no los excluye).
        return [
            _footprint([("100", low, "0"), ("104", "1000", "0"), ("109", high, "0")])
        ]

    def test_hvn_de_distinto_volumen_elige_el_de_mayor_volumen(self) -> None:
        window = self._hvn_window("50", "30")
        params = VolumeNodeParams(hvn_multiplier=Decimal("0"))
        assert select_hvn_price(window, bin_count=9, params=params) == Decimal("100.5")

    def test_hvn_empate_de_volumen_elige_el_de_menor_precio(self) -> None:
        window = self._hvn_window("50", "50")
        params = VolumeNodeParams(hvn_multiplier=Decimal("0"))
        assert select_hvn_price(window, bin_count=9, params=params) == Decimal("100.5")

    def test_hvn_sin_candidato_sobre_umbral_cae_al_poc(self) -> None:
        # [PARIDAD v4] TestNodos._WINDOW con el umbral por defecto (5.0): ningun bin
        # supera media*5=210 -> sin candidatos. Fallback: el POC (idx5, centro 105.5).
        params = VolumeNodeParams(hvn_multiplier=Decimal("5.0"))
        result = select_hvn_price(TestNodos._WINDOW, bin_count=10, params=params)
        assert result == Decimal("105.5")

    def test_lvn_de_distinto_volumen_elige_el_de_menor_volumen(self) -> None:
        # 9 bins consecutivos (sin huecos: idx0..8 todos ocupados) para que el rango
        # de Value Area completo (value_area_pct=1) no meta bins vacios como candidatos
        # LVN espurios. idx0=5 (menor), idx8=9, resto=6 de relleno, POC=1000 en idx4.
        window = [
            _footprint(
                [
                    ("100", "5", "0"),
                    ("101", "6", "0"),
                    ("102", "6", "0"),
                    ("103", "6", "0"),
                    ("104", "1000", "0"),
                    ("105", "6", "0"),
                    ("106", "6", "0"),
                    ("107", "6", "0"),
                    ("109", "9", "0"),
                ]
            )
        ]
        params = VolumeNodeParams(lvn_multiplier=Decimal("1000"))
        result = select_lvn_price(
            window, bin_count=9, value_area_pct=Decimal("1"), params=params
        )
        assert result == Decimal("100.5")

    def test_lvn_sin_candidato_bajo_umbral_cae_al_bin_ocupado_de_menor_volumen(
        self,
    ) -> None:
        # [PARIDAD v4] TestNodos._WINDOW con lvn_multiplier=0: ni siquiera los bins
        # vacios (volumen 0) cumplen "< 0" -> sin candidatos. Fallback: el bin OCUPADO
        # de menor volumen (b3/b7/b9 empatan a 10; menor indice = b3, centro 103.5).
        params = VolumeNodeParams(lvn_multiplier=Decimal("0"))
        result = select_lvn_price(TestNodos._WINDOW, bin_count=10, params=params)
        assert result == Decimal("103.5")

    def test_precio_unico_da_hvn_lvn_degenerados(self) -> None:
        window = [_footprint([("100", "10", "0")])]
        assert select_hvn_price(window, bin_count=5) == Decimal("100")
        assert select_lvn_price(window, bin_count=5) == Decimal("100")


class TestNodosDeclaraciones:
    def test_vp_hvn_lvn_son_non_servible_windowed_de_footprint(self) -> None:
        for declaration in (vp_hvn_declaration(), vp_lvn_declaration()):
            assert declaration.source_type is SourceType.OBSERVABLE
            assert declaration.servibility is Servibility.NON_SERVIBLE
            assert declaration.memory_model is MemoryModel.WINDOWED
            assert declaration.value_type is ScalarType.DECIMAL
            assert declaration.consumes == (MARKET_FOOTPRINT_SOURCE_ID,)

    def test_source_ids(self) -> None:
        assert vp_hvn_declaration().source_id == VP_HVN_SOURCE_ID
        assert vp_lvn_declaration().source_id == VP_LVN_SOURCE_ID

    def test_hvn_declara_solo_sus_parametros_en_la_cache_key(self) -> None:
        declaration = vp_hvn_declaration()
        names = {param.name for param in declaration.params}
        assert names == {"bin_count", "hvn_multiplier", "adjacency_bins", "hvn_max"}
        for name in names:
            assert name in declaration.cache_key_schema
        # El umbral de LVN no interviene en HVN: no debe ensuciar su cache_key.
        assert "lvn_multiplier" not in declaration.cache_key_schema

    def test_lvn_declara_solo_sus_parametros_en_la_cache_key(self) -> None:
        declaration = vp_lvn_declaration()
        names = {param.name for param in declaration.params}
        assert names == {"bin_count", "lvn_multiplier", "adjacency_bins", "lvn_max"}
        for name in names:
            assert name in declaration.cache_key_schema
        assert "hvn_multiplier" not in declaration.cache_key_schema

    def test_dag_valida_con_market_footprint(self) -> None:
        catalog = DataSourceCatalog()
        catalog.register(market_footprint_declaration())
        catalog.register(vp_hvn_declaration())
        catalog.register(vp_lvn_declaration())
        catalog.validate()  # completa y aciclica.

    def test_vp_hvn_sin_footprint_falla_el_dag(self) -> None:
        catalog = DataSourceCatalog()
        catalog.register(vp_hvn_declaration())
        with pytest.raises(MissingDependencyError):
            catalog.validate()
