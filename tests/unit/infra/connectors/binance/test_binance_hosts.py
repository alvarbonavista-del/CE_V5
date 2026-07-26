"""PIN de los HOSTS de Binance: el dominio de DATOS, no el geobloqueado. SIN RED.

POR QUE MUERDE ESTE TEST. El conector habla con data-*.binance.vision, que es la API
PUBLICA DE DATOS de Binance: sirve los MISMOS streams y payloads que stream.binance.com
/ api.binance.com pero NO esta geo-restringida (MiCA restringe el SERVICIO, no el feed
publico de datos). Ese cambio de host es el fix ee21f0f, y se descubrio EN CALIENTE:
contra los hosts geobloqueados la validacion no arrancaba.

El riesgo que cubre: los hosts son dos constantes de modulo. Un revert accidental, un
merge que resucite la version vieja del fichero o un "vuelvo a los hosts de siempre"
bien intencionado reintroducirian el fallo SIN PONER NADA EN ROJO -- toda la suite pasa
sin tocar la red, asi que nadie se enteraria hasta la siguiente validacion en caliente,
que es justo donde mas caro sale. Este test es esa red de seguridad: literal y a
proposito, para que cualquier cambio de host tenga que ser DELIBERADO y pasar por aqui.

Si algun dia el cambio es querido (otro dominio de datos, otro puerto), se cambia AQUI
tambien y en el mismo commit: eso es lo que se busca, que quede en el diff.
"""

from __future__ import annotations

from ce_v5.infra.connectors.binance.connector import _REST_BASE, _WS_BASE


def test_el_ws_apunta_al_dominio_de_datos_no_geobloqueado() -> None:
    # Literal: el host, el puerto y el path combinado. El :443 explicito y /stream son
    # parte de lo verificado en caliente (Fase A, 5.32).
    assert _WS_BASE == "wss://data-stream.binance.vision:443/stream"


def test_el_rest_apunta_al_dominio_de_datos_no_geobloqueado() -> None:
    # De aqui cuelgan /api/v3/depth (la foto del libro) y /api/v3/trades (el relleno
    # tras una reconexion): si el host vuelve al geobloqueado, la siembra deja de
    # responder y el libro no arranca.
    assert _REST_BASE == "https://data-api.binance.vision"


def test_ningun_host_geobloqueado_se_cuela() -> None:
    # La cara negativa, por si alguien anade un host nuevo copiando el viejo: los
    # dominios geo-restringidos no pueden aparecer en ninguno de los dos.
    for host in (_WS_BASE, _REST_BASE):
        assert "stream.binance.com" not in host
        assert "api.binance.com" not in host
        assert ".binance.vision" in host
