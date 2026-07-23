"""Tests del endpoint publico de lectura de velas (T-05), sin base de datos.

Lo que se prueba aqui es lo que NO necesita PostgreSQL: que los limites de la peticion
se rechazan ANTES de tocar nada, que un flujo sin historico responde "no hay dato" en
vez de fallar, y que la consulta sale con el tope y el recorte que dice el contrato de
la ruta. La lectura contra velas REALES (dedup por correccion, orden, recorte) vive en
tests/integration: eso no lo puede probar un doble.

EL DOBLE NO INVENTA FILAS: devuelve siempre cero. Un doble que fabricara velas estaria
probando su propia imaginacion.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ce_v5.entrypoints.api.composition import ApiContext
from ce_v5.entrypoints.api.routes_market import router
from ce_v5.infra.db.ports import Session, SqlParams

_RUTA = "/v1/public/market/candles"
_FLUJO = "exchange=binance&symbol=BTC-USDT&timeframe=1m"

# El mayor bigint que PostgreSQL admite en open_time. Es el centinela "sin tope" que la
# ruta usa cuando no le dan up_to: un tope que no excluye ninguna vela. Se escribe aqui
# a mano A PROPOSITO -- si un dia alguien lo sustituyera por un now(), este test lo
# cazaria; comparar contra la misma constante del modulo no probaria nada (ADR-007).
_MAX_BIGINT = 9_223_372_036_854_775_807


class _SesionSinVelas:
    """Doble minimo de Session: apunta lo que se le pide y no devuelve ninguna fila."""

    def __init__(self) -> None:
        self.consultas: list[tuple[str, SqlParams]] = []

    def execute(self, query: str, params: SqlParams = None) -> None:
        self.consultas.append((query, params))

    def fetchone(self, query: str, params: SqlParams = None) -> None:
        self.consultas.append((query, params))
        return None

    def fetchall(
        self, query: str, params: SqlParams = None
    ) -> list[tuple[object, ...]]:
        self.consultas.append((query, params))
        return []


class _BaseSinVelas:
    """Doble minimo de Database: una base viva donde ese flujo no tiene historico."""

    def __init__(self) -> None:
        self.sesion = _SesionSinVelas()

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        yield self.sesion

    def close(self) -> None:
        pass


class _ContextoSoloMercado:
    """Lo unico del ApiContext que esta ruta toca."""

    def __init__(self, market_db: _BaseSinVelas) -> None:
        self.market_db = market_db


@pytest.fixture
def base() -> _BaseSinVelas:
    return _BaseSinVelas()


@pytest.fixture
def client(base: _BaseSinVelas) -> Iterator[TestClient]:
    """La ruta sola, con el UNICO trozo de contexto que usa: la base de mercado.

    No se monta la aplicacion entera porque este endpoint no depende de nada mas: no
    tiene identidad, ni politica, ni tenant. Si un dia empezara a necesitarlos, este
    montaje dejaria de compilar y seria la primera senal.
    """
    app = FastAPI()
    app.include_router(router)
    app.state.context = cast(ApiContext, cast(Any, _ContextoSoloMercado(base)))
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("limit", [0, -1, 1001, 100000])
def test_un_limit_fuera_de_rango_se_rechaza(client: TestClient, limit: int) -> None:
    # Se rechaza en la FRONTERA, antes de consultar nada: un limit de 100000 no llega a
    # convertirse en una consulta que el servidor tenga que servir gratis.
    assert client.get(f"{_RUTA}?{_FLUJO}&limit={limit}").status_code == 422


def test_un_limit_en_los_bordes_se_acepta(client: TestClient) -> None:
    assert client.get(f"{_RUTA}?{_FLUJO}&limit=1").status_code == 200
    assert client.get(f"{_RUTA}?{_FLUJO}&limit=1000").status_code == 200


def test_un_flujo_sin_historico_devuelve_la_lista_vacia_con_200(
    client: TestClient,
) -> None:
    # AUSENCIA DE DATO NO ES ERROR. Un 404 o un 500 aqui le diria al cliente que algo va
    # mal cuando lo unico que pasa es que todavia no hay velas de ese flujo.
    respuesta = client.get(f"{_RUTA}?{_FLUJO}")

    assert respuesta.status_code == 200
    assert respuesta.json() == []


# -- BORDE: lo mal formado falla en ALTO (ADR-006) ----------------------------
#
# Un flujo SIN HISTORICO responde 200 con lista vacia; una peticion MAL FORMADA responde
# 422. Confundir los dos casos es el fallo silencioso: quien pregunta con la forma
# nativa del exchange recibiria "no hay dato" y no podria distinguirlo de un mercado
# sin velas.
# En un grafico serian dos lienzos en blanco identicos por motivos opuestos.


def test_el_simbolo_NATIVO_del_exchange_se_rechaza(client: TestClient) -> None:
    # 'BTCUSDT' es como llama Binance al par; el contrato usa SIEMPRE la forma canonica
    # BASE-QUOTE. Y la vuelta NO se puede calcular: 'BTCUSDT' podria ser BTC-USDT o
    # BT-CUSDT. Aceptarlo en silencio seria invitar a consultar el historico de una
    # moneda creyendo consultar el de otra.
    respuesta = client.get(f"{_RUTA}?exchange=binance&symbol=BTCUSDT&timeframe=1m")

    assert respuesta.status_code == 422


@pytest.mark.parametrize(
    "symbol", ["btc-usdt", "BTC_USDT", "-USDT", "BTC-", "BTC--USDT", ""]
)
def test_un_simbolo_no_canonico_se_rechaza(client: TestClient, symbol: str) -> None:
    respuesta = client.get(f"{_RUTA}?exchange=binance&symbol={symbol}&timeframe=1m")

    assert respuesta.status_code == 422


@pytest.mark.parametrize("timeframe", ["2m", "1M", "", "1h30m", "basura"])
def test_un_timeframe_fuera_del_vocabulario_se_rechaza(
    client: TestClient, timeframe: str
) -> None:
    # El vocabulario de timeframes es CERRADO (ADR-005). '2m' es un intervalo REAL de
    # otros exchanges que este sistema no sirve: rechazarlo en el borde evita una
    # consulta que solo podria devolver vacio.
    respuesta = client.get(
        f"{_RUTA}?exchange=binance&symbol=BTC-USDT&timeframe={timeframe}"
    )

    assert respuesta.status_code == 422


@pytest.mark.parametrize("timeframe", ["1m", "5m", "15m", "1h", "4h", "1d"])
def test_los_seis_timeframes_del_contrato_se_aceptan(
    client: TestClient, timeframe: str
) -> None:
    respuesta = client.get(
        f"{_RUTA}?exchange=binance&symbol=BTC-USDT&timeframe={timeframe}"
    )

    assert respuesta.status_code == 200


def test_un_simbolo_canonico_SIN_DATOS_sigue_siendo_200_vacio(
    client: TestClient,
) -> None:
    # LA OTRA MITAD DE LA REGLA, la que evita pasarse de celo: un par canonico
    # perfectamente valido del que aun no hay velas NO es un error. Si esto diera 422,
    # un flujo recien suscrito pareceria una peticion equivocada.
    respuesta = client.get(f"{_RUTA}?exchange=binance&symbol=SOL-USDT&timeframe=1m")

    assert respuesta.status_code == 200
    assert respuesta.json() == []


def test_un_exchange_desconocido_NO_se_rechaza(client: TestClient) -> None:
    # exchange queda como cadena libre a proposito: el catalogo crece (T-03 anadio OKX
    # y Bybit). Un exchange que no conocemos no es una peticion mal formada, es una
    # peticion sobre algo que todavia no existe -> 200 vacio.
    respuesta = client.get(
        f"{_RUTA}?exchange=exchange_que_no_existe&symbol=BTC-USDT&timeframe=1m"
    )

    assert respuesta.status_code == 200
    assert respuesta.json() == []


def test_sin_up_to_el_tope_es_el_centinela_y_no_un_reloj(
    client: TestClient, base: _BaseSinVelas
) -> None:
    # ADR-007: este camino de lectura NO tiene reloj. Sin up_to, el tope es el maximo
    # bigint (no excluye ninguna vela) y "las mas recientes" lo resuelve el ORDER BY
    # del historico. Con un now() ahi, el tope dejaria de ser esta constante.
    client.get(f"{_RUTA}?{_FLUJO}")

    _, params = base.sesion.consultas[-1]
    assert params is not None
    assert list(params)[4] == _MAX_BIGINT


def test_up_to_viaja_como_tope_y_limit_como_recorte(
    client: TestClient, base: _BaseSinVelas
) -> None:
    client.get(f"{_RUTA}?{_FLUJO}&limit=7&up_to=1784073600000")

    _, params = base.sesion.consultas[-1]
    assert params is not None
    valores = list(params)
    assert valores[4] == 1_784_073_600_000
    assert valores[5] == 7


def test_el_limit_por_defecto_son_500_velas(
    client: TestClient, base: _BaseSinVelas
) -> None:
    client.get(f"{_RUTA}?{_FLUJO}")

    _, params = base.sesion.consultas[-1]
    assert params is not None
    assert list(params)[5] == 500


def test_el_market_type_va_fijado_a_spot(
    client: TestClient, base: _BaseSinVelas
) -> None:
    # v5.0 solo tiene spot y el pin lo pone la funcion de lectura: el cliente NO puede
    # pedir otro mercado, porque no hay parametro por el que pedirlo.
    client.get(f"{_RUTA}?{_FLUJO}")

    _, params = base.sesion.consultas[-1]
    assert params is not None
    assert list(params)[1] == "spot"


@pytest.mark.parametrize(
    "query",
    [
        "symbol=BTC-USDT&timeframe=1m",
        "exchange=binance&timeframe=1m",
        "exchange=binance&symbol=BTC-USDT",
    ],
)
def test_falta_un_parametro_del_flujo_y_se_rechaza(
    client: TestClient, query: str
) -> None:
    # Un flujo son TRES datos. Sin uno de ellos no hay nada que leer, y adivinarlo
    # (un exchange "por defecto") seria servir velas de un mercado que nadie pidio.
    assert client.get(f"{_RUTA}?{query}").status_code == 422
