"""Validacion en caliente de P06b: la puerta publica, contra el proceso REAL.

NO es un test: es una DEMOSTRACION VIVA, y por eso habla con la API por HTTP y por
WebSocket como lo haria cualquier cliente de fuera. Los tests de integracion usan el
TestClient de Starlette, que habla con la aplicacion ASGI directamente y NO pasa por
Uvicorn: hay fallos que solo existen en el proceso de verdad (el primero, que sin una
implementacion de WebSocket instalada Uvicorn rechaza toda conexion WS). Este arnes es
lo que los destapa.

Ejecuta las TRES validaciones que exige el Roadmap:
  1. Login + suscripcion realtime autenticada (el token JAMAS en la URL).
  2. El cliente NO puede imponer identidad: cuatro vias, las cuatro cerradas.
  3. Fail-closed en el borde, EN CALIENTE: un kill switch activado por el operador desde
     otra terminal corta la capability SIN reiniciar la API, y en segundos (no en los
     60 s del TTL del cache: eso probaria caducidad, no invalidacion por evento).

CERO DATOS REALES: el email es inventado, en ejemplo.test.

SIN REINTENTOS SILENCIOSOS: cuando el arnes espera a que un evento se propague, IMPRIME
cada intento. Un reintento que no se ve es un fallo que no se ve.

TRES ENTORNOS, NO TRES PERSONAS. La separacion de poderes de CA-03 es entre PROCESOS,
no entre seres humanos: la API corre con un entorno del que se ELIMINA
CE_V5_OPERATOR_DATABASE_URL (de eso se encarga run_p06b_hot_validation.py) y la
herramienta de operador se ejecuta como PROCESO APARTE, el unico que recibe esa
credencial. Por eso el modo AUTOMATICO (el de por defecto) puede orquestarlo todo sin
debilitar nada: Alvaro es el decisor, no el director de orquesta.

Uso:
  python tools/validate_p06b_api.py            # automatico: el arnes mueve el switch
  python tools/validate_p06b_api.py --manual   # interactivo: lo mueve un humano
Variables: CE_V5_API_BASE_URL (por defecto http://127.0.0.1:8000). En modo automatico,
ademas, CE_V5_OPERATOR_DATABASE_URL (la credencial del operador).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx2 as httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

_BASE_URL = os.environ.get("CE_V5_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
_WS_URL = _BASE_URL.replace("https://", "wss://").replace("http://", "ws://")
_WS_URL = f"{_WS_URL}/v1/realtime"

# Email INVENTADO y unico por ejecucion: ejemplo.test es un dominio reservado que no
# existe. Unico para no chocar con el limitador de intentos de una ejecucion anterior.
_EMAIL = f"p06b-demo-{uuid4().hex[:10]}@ejemplo.test"
_PASSWORD = "contrasena-falsa-de-demostracion"

_REALTIME_CAP = "subscribe_realtime"
_SENSITIVE_CAP = "execute_order"
_TOPIC = "user"

# TTL del cache del capability set en la API (composition._MAX_STALENESS_MS).
_CACHE_TTL_SECONDS = 60
# Cuanto se espera a que el kill switch llegue POR EVENTO. Muy por debajo del TTL: si
# hiciera falta mas, es que no llego por evento, y eso seria un fallo que hay que ver.
_ESPERA_MAXIMA_SEGUNDOS = 20.0
_INTERVALO_SONDEO_SEGUNDOS = 0.5

_UUID_AJENO = "00000000-0000-4000-8000-0000000000ff"

_OPERATOR_DSN_VAR = "CE_V5_OPERATOR_DATABASE_URL"
_ACTOR = "arnes-p06b"
_MOTIVO_ACTIVAR = "validacion en caliente P06b"
_MOTIVO_SOLTAR = "fin de la validacion en caliente P06b"
_MOTIVO_LIMPIEZA = "limpieza previa del arnes de validacion"


def _say(mensaje: str = "") -> None:
    print(mensaje, flush=True)


def _paso(numero: str, titulo: str) -> None:
    _say(f"--- {numero} {titulo}")


def _titulo(texto: str) -> None:
    _say()
    _say("=" * 78)
    _say(texto)
    _say("=" * 78)


class ValidacionFallida(RuntimeError):
    """Algo no salio como debia. Se imprime el fallo REAL, no se disimula."""


@dataclass(frozen=True, slots=True)
class ResultadoWs:
    """Lo que devolvio el canal: un mensaje, o un cierre con su codigo real."""

    mensaje: str | None
    close_code: int | None
    close_reason: str | None

    def __str__(self) -> str:
        if self.mensaje is not None:
            return f"mensaje del servidor: {self.mensaje}"
        return (
            f"CERRADO por el servidor: code={self.close_code} "
            f"reason={self.close_reason!r}"
        )


def _operador(*args: str) -> str:
    """La TERCERA TERMINAL, hecha proceso: el operator_cli como SUBPROCESO.

    ESTE es el unico proceso al que el arnes le pasa la credencial de operador
    (CE_V5_OPERATOR_DATABASE_URL). El proceso de la API se lanza con un entorno del que
    esa variable se ha ELIMINADO (run_p06b_hot_validation.py), y su propio guardia de
    arranque aborta si la encuentra. La separacion de poderes de CA-03 es entre PROCESOS
    con entornos distintos, no entre personas con terminales distintas: automatizar la
    orquestacion no debilita nada.

    Su salida se imprime TAL CUAL: es la evidencia de que el operador hizo lo que dice
    que hizo. Si el subproceso falla, se lanza ValidacionFallida con su stderr; aqui no
    se traga ningun error.
    """
    if not os.environ.get(_OPERATOR_DSN_VAR, "").strip():
        raise ValidacionFallida(
            f"Falta {_OPERATOR_DSN_VAR}: el modo automatico necesita la credencial de "
            "OPERADOR para mover el kill switch. Exportala en el entorno del ARNES (no "
            "en el de la API) o usa --manual."
        )
    orden = [sys.executable, "-m", "ce_v5.entrypoints.operator_cli", *args]
    _say(f"    [operador] {' '.join(args)}")
    terminado = subprocess.run(  # noqa: S603 - orden fija, sin shell
        orden,
        capture_output=True,
        text=True,
        env=dict(os.environ),
        check=False,
    )
    for linea in terminado.stdout.splitlines():
        _say(f"    [operador] {linea}")
    if terminado.returncode != 0:
        raise ValidacionFallida(
            f"El operator_cli fallo (codigo {terminado.returncode}). Su stderr:\n"
            f"{terminado.stderr.strip()}"
        )
    return terminado.stdout


def _kill_switch_id(salida: str) -> str:
    """El id que imprimio el operador tras activar."""
    for linea in salida.splitlines():
        if linea.startswith("kill_switch_id:"):
            return linea.split(":", 1)[1].strip()
    raise ValidacionFallida(
        f"El operador no imprimio ningun kill_switch_id. Su salida fue:\n{salida}"
    )


def _switches_activos(target_ref: str) -> list[str]:
    """Los kill switches ACTIVOS sobre esa capability, segun el operador."""
    activos: list[str] = []
    for linea in _operador("kill-switch", "list").splitlines():
        campos = linea.split()
        # Formato de operator_cli._print_kill_switches: id scope target_ref=... ...
        # estado ... El estado es una palabra suelta: 'activo' o 'inactivo'. Se compara
        # ENTERA, porque 'inactivo' contiene 'activo' y un 'in' se cuela solo.
        if len(campos) < 3 or "activo" not in campos:
            continue
        if f"target_ref={target_ref}" in campos:
            activos.append(campos[0])
    return activos


def _soltar_switches(target_ref: str, motivo: str) -> None:
    """Desactiva todo switch activo sobre la capability. Idempotente."""
    activos = _switches_activos(target_ref)
    if not activos:
        _say(f"    no hay ningun kill switch activo sobre {target_ref}.")
        return
    for kill_switch_id in activos:
        _operador(
            "kill-switch",
            "deactivate",
            "--id",
            kill_switch_id,
            "--reason",
            motivo,
            "--actor",
            _ACTOR,
        )


def _limpieza_previa(automatico: bool) -> None:
    """Deja la plataforma sin switches puestos ANTES de demostrar nada.

    Una ejecucion anterior interrumpida (un Ctrl+C, o un fallo en mitad del paso 3) deja
    el switch activado. Con el puesto, la VALIDACION 1 se encuentra el borde cerrado: la
    suscripcion realtime muere con un 4403 y el arnes aborta acusando al canal de algo
    que hizo la politica.

    En modo --manual no se toca nada: el operador es un humano y esta herramienta no
    mueve switches a su espalda. Se le AVISA y decide el.
    """
    # PASO 0, y no mas tarde: el estado que deja una ejecucion ANTERIOR no puede decidir
    # el resultado de esta. Con un kill switch vivo de una ronda previa, la validacion 1
    # se encuentra el borde cerrado y aborta sin demostrar nada, culpando al canal de
    # algo que hizo la politica. Un arnes que depende de que la ejecucion anterior
    # terminara bien no sirve para nada.
    _titulo("PASO 0: la demostracion no hereda el estado de nadie")

    if automatico:
        _soltar_switches(_REALTIME_CAP, _MOTIVO_LIMPIEZA)
        return

    if not os.environ.get(_OPERATOR_DSN_VAR, "").strip():
        _say(f"  Modo manual sin {_OPERATOR_DSN_VAR}: no puedo ni MIRAR si hay kill")
        _say(
            f"  switches activos sobre {_REALTIME_CAP}. Compruebalo tu antes de seguir:"
        )
        _say("    python -m ce_v5.entrypoints.operator_cli kill-switch list")
        return

    activos = _switches_activos(_REALTIME_CAP)
    if not activos:
        _say(f"  No hay ningun kill switch activo sobre {_REALTIME_CAP}. Se puede")
        _say("  empezar: la validacion 1 encontrara el borde abierto.")
        return

    _say(
        f"  AVISO: hay {len(activos)} kill switch(es) ACTIVO(s) sobre {_REALTIME_CAP}."
    )
    _say("  Con el puesto, la validacion 1 se estrella contra el borde cerrado y este")
    _say(
        "  arnes aborta sin demostrar nada. SUELTALOS antes de seguir (modo manual: no"
    )
    _say("  los toco yo):")
    for kill_switch_id in activos:
        _say("    python -m ce_v5.entrypoints.operator_cli kill-switch deactivate \\")
        _say(f'        --id {kill_switch_id} --reason "limpieza" --actor alvaro')
    input("  Pulsa ENTER cuando los hayas soltado (o Ctrl+C para parar)... ")


def _recorte(token: str) -> str:
    """Los primeros 12 caracteres. Un log con un token entero es un token filtrado."""
    return f"{token[:12]}... ({len(token)} caracteres, no se imprime entero)"


async def _hablar_ws(
    access_token: str, subscribe: dict[str, Any], timeout: float = 10.0
) -> ResultadoWs:
    """Autentica por el PRIMER MENSAJE y se suscribe. Devuelve la respuesta real.

    El token NO va en la URL: la URL se imprime en el paso 1.3 para que se vea.
    """
    try:
        async with connect(_WS_URL, open_timeout=timeout) as ws:
            await ws.send(json.dumps({"type": "auth", "access_token": access_token}))
            await ws.send(json.dumps(subscribe))
            crudo = await asyncio.wait_for(ws.recv(), timeout)
            texto = crudo if isinstance(crudo, str) else crudo.decode("utf-8")
            return ResultadoWs(mensaje=texto, close_code=None, close_reason=None)
    except ConnectionClosed as cerrado:
        return ResultadoWs(
            mensaje=None, close_code=cerrado.code, close_reason=cerrado.reason
        )
    except TimeoutError:
        return ResultadoWs(
            mensaje=None, close_code=None, close_reason="sin respuesta a tiempo"
        )


def _suscripcion(access_token: str, subscribe: dict[str, Any]) -> ResultadoWs:
    return asyncio.run(_hablar_ws(access_token, subscribe))


def _capabilities(cliente: httpx.Client, token: str) -> dict[str, Any]:
    respuesta = cliente.get(
        "/v1/capabilities",
        params=[("capability", _REALTIME_CAP), ("capability", _SENSITIVE_CAP)],
        headers={"Authorization": f"Bearer {token}"},
    )
    if respuesta.status_code != 200:
        raise ValidacionFallida(
            f"GET /v1/capabilities devolvio {respuesta.status_code}: {respuesta.text}"
        )
    cuerpo: dict[str, Any] = respuesta.json()
    return cuerpo


def _decision(cuerpo: dict[str, Any], capability_id: str) -> dict[str, Any]:
    for vista in cuerpo["decisions"]:
        if vista["capability_id"] == capability_id:
            decision: dict[str, Any] = vista
            return decision
    raise ValidacionFallida(f"La API no devolvio decision para {capability_id}.")


def _imprimir_decisiones(cuerpo: dict[str, Any]) -> None:
    _say(f"    policy_version={cuerpo['policy_version']} advisory={cuerpo['advisory']}")
    for vista in cuerpo["decisions"]:
        _say(
            f"    {vista['capability_id']:<20} {vista['decision'].upper():<6} "
            f"reason_code={vista['reason_code']:<28} "
            f"sensitive={vista['sensitive']} kill_switch_id={vista['kill_switch_id']}"
        )


def _esperar_decision(
    cliente: httpx.Client, token: str, capability_id: str, esperada: str
) -> tuple[dict[str, Any], float]:
    """Sondea /v1/capabilities hasta que la decision sea la esperada. SIN silencio.

    Cada intento se imprime: si el cambio tardara lo que dura el TTL del cache, se veria
    en la cuenta de intentos, y eso significaria que NO llego por evento.
    """
    empezado = time.monotonic()
    intento = 0
    while time.monotonic() - empezado < _ESPERA_MAXIMA_SEGUNDOS:
        intento += 1
        cuerpo = _capabilities(cliente, token)
        decision = _decision(cuerpo, capability_id)
        transcurrido = time.monotonic() - empezado
        _say(
            f"    sondeo {intento:>2} (t+{transcurrido:5.2f}s): "
            f"{capability_id} = {decision['decision'].upper()} "
            f"({decision['reason_code']})"
        )
        if decision["decision"].lower() == esperada:
            return cuerpo, transcurrido
        time.sleep(_INTERVALO_SONDEO_SEGUNDOS)
    raise ValidacionFallida(
        f"{capability_id} no llego a {esperada.upper()} en "
        f"{_ESPERA_MAXIMA_SEGUNDOS:.0f}s. El evento NO se propago: eso es el fallo, y "
        "no se disimula con mas espera."
    )


def _validacion_1(cliente: httpx.Client) -> str:
    _titulo("VALIDACION 1: login + suscripcion realtime AUTENTICADA")

    _paso("1.1", "registro y login por HTTP")
    alta = cliente.post(
        "/v1/auth/register", json={"email": _EMAIL, "password": _PASSWORD}
    )
    _say(f"    POST /v1/auth/register -> {alta.status_code}")
    if alta.status_code != 201:
        raise ValidacionFallida(f"El registro fallo: {alta.status_code} {alta.text}")

    entrada = cliente.post(
        "/v1/auth/login", json={"email": _EMAIL, "password": _PASSWORD}
    )
    _say(f"    POST /v1/auth/login    -> {entrada.status_code}")
    if entrada.status_code != 200:
        raise ValidacionFallida(f"El login fallo: {entrada.status_code} {entrada.text}")
    sesion = entrada.json()
    token: str = sesion["access_token"]
    _say(f"    access_token = {_recorte(token)}")
    _say(f"    email de demo (INVENTADO) = {_EMAIL}")
    _say("    el refresh token NO esta en el cuerpo: viaja en cookie httpOnly.")

    _paso("1.2", "GET /v1/me: la identidad la resuelve el BACKEND")
    yo = cliente.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    if yo.status_code != 200:
        raise ValidacionFallida(f"GET /v1/me fallo: {yo.status_code} {yo.text}")
    identidad = yo.json()
    _say(f"    user_id   = {identidad['user_id']}")
    _say(f"    tenant_id = {identidad['tenant_id']}  <- lo resolvio el BACKEND")
    _say("    el cliente no mando ningun tenant: no hay donde ponerlo.")

    _paso("1.3", "WebSocket: el token va en el PRIMER MENSAJE, jamas en la URL")
    _say(f"    URL = {_WS_URL}")
    _say("    (mirala bien: no lleva token ni identidad. Una URL acaba en los logs del")
    _say("     servidor, en el historial y en el Referer; un token ahi es un token")
    _say("     publicado.)")
    resultado = _suscripcion(token, {"type": "subscribe", "topic": _TOPIC})
    _say(f"    {resultado}")
    if resultado.mensaje is None:
        raise ValidacionFallida(
            "La suscripcion realtime NO recibio ACK. Si el cierre no dice nada util, "
            "sospecha del proceso real: Uvicorn necesita 'websockets' INSTALADO para "
            "servir el protocolo (por eso es dependencia)."
        )
    ack = json.loads(resultado.mensaje)
    if ack.get("type") != "ack":
        raise ValidacionFallida(f"Se esperaba un ACK y llego: {resultado.mensaje}")
    _say(f"    ACK real: topic={ack['topic']} checkpoint={ack['checkpoint']}")
    _say("    SUSCRIPCION AUTENTICADA VIVA: sesion verificada + capability concedida.")
    return token


def _validacion_2(
    cliente: httpx.Client, token: str, user_id: str, tenant_id: str
) -> None:
    _titulo("VALIDACION 2 (a): EL CLIENTE NO PUEDE IMPONER IDENTIDAD")
    cabecera = {"Authorization": f"Bearer {token}"}

    _paso("2.1", "GET /v1/me?user_id=<uuid AJENO>")
    respuesta = cliente.get("/v1/me", params={"user_id": _UUID_AJENO}, headers=cabecera)
    devuelto = respuesta.json()
    _say(f"    pedido : user_id={_UUID_AJENO}")
    _say(f"    real   : {respuesta.status_code} {devuelto}")
    if devuelto["user_id"] != user_id:
        raise ValidacionFallida("La query CAMBIO la identidad. Esto es un agujero.")
    _say("    la query se IGNORA: devuelve la identidad de la sesion, no la pedida.")

    _paso("2.2", "GET /v1/me con cabecera X-User-Id ajena")
    respuesta = cliente.get("/v1/me", headers={**cabecera, "X-User-Id": _UUID_AJENO})
    devuelto = respuesta.json()
    _say(f"    pedido : X-User-Id: {_UUID_AJENO}")
    _say(f"    real   : {respuesta.status_code} {devuelto}")
    if devuelto["user_id"] != user_id or devuelto["tenant_id"] != tenant_id:
        raise ValidacionFallida("La cabecera CAMBIO la identidad. Esto es un agujero.")
    _say("    la cabecera se IGNORA: nadie la lee. No existe ese camino.")

    _paso("2.3", "POST /v1/auth/login con tenant_id colado en el cuerpo")
    respuesta = cliente.post(
        "/v1/auth/login",
        json={"email": _EMAIL, "password": _PASSWORD, "tenant_id": _UUID_AJENO},
    )
    _say(f"    real   : {respuesta.status_code}")
    _say(f"    cuerpo : {respuesta.text[:200]}")
    if respuesta.status_code != 422:
        raise ValidacionFallida(
            f"Se esperaba 422 (el contrato prohibe campos extra) y llego "
            f"{respuesta.status_code}."
        )
    _say("    422: el CONTRATO lo rechaza (extra='forbid'). No hay donde poner un")
    _say("    tenant, asi que no se puede colar uno.")

    _paso("2.4", "WebSocket: user_id y tenant_id colados en el mensaje de suscripcion")
    resultado = _suscripcion(
        token,
        {
            "type": "subscribe",
            "topic": _TOPIC,
            "user_id": _UUID_AJENO,
            "tenant_id": _UUID_AJENO,
        },
    )
    _say(f"    {resultado}")
    if resultado.mensaje is not None:
        raise ValidacionFallida(
            "El canal ACEPTO un mensaje con identidad dentro. Esto es un agujero."
        )
    _say("    el CONTRATO lo rechaza y el canal se cierra: no cuela por el WebSocket.")

    _say()
    _say("  LA IDENTIDAD EFECTIVA SALIO SIEMPRE DE LA SESION VERIFICADA.")
    _say(
        "  Las cuatro vias estan cerradas: query, cabecera, cuerpo y mensaje de canal."
    )


def _activar_switch(automatico: bool) -> None:
    """Paso 3.2: el kill switch se pone. Lo pone el arnes, o lo pone un humano."""
    _paso(
        "3.2", "el OPERADOR activa el kill switch (proceso aparte, credencial aparte)"
    )
    if automatico:
        salida = _operador(
            "kill-switch",
            "activate",
            "--scope",
            "capability",
            "--target-ref",
            _REALTIME_CAP,
            "--reason",
            _MOTIVO_ACTIVAR,
            "--actor",
            _ACTOR,
        )
        _say(f"    activado: kill_switch_id={_kill_switch_id(salida)}")
        _say("    LA API NO SE HA TOCADO: sigue corriendo, con su mismo PID.")
        return

    _say()
    _say("  MODO MANUAL. Ejecuta esto en la terminal del OPERADOR (la unica que puede")
    _say(f"  portar {_OPERATOR_DSN_VAR}):")
    _say()
    _say("    python -m ce_v5.entrypoints.operator_cli kill-switch activate \\")
    _say(f"        --scope capability --target-ref {_REALTIME_CAP} \\")
    _say(f'        --reason "{_MOTIVO_ACTIVAR}" --actor alvaro')
    _say()
    _say("  Apunta el kill_switch_id que imprima: lo necesitaras para soltarlo (3.6).")
    _say("  LA API NO SE TOCA: sigue corriendo, con su mismo PID.")
    input("  Pulsa ENTER cuando lo hayas activado... ")


def _soltar_switch(automatico: bool) -> None:
    """Paso 3.6: el kill switch se suelta."""
    _paso("3.6", "el OPERADOR suelta el kill switch")
    if automatico:
        _soltar_switches(_REALTIME_CAP, _MOTIVO_SOLTAR)
        return

    _say()
    _say("  En la terminal del OPERADOR, con el id que apuntaste en 3.2:")
    _say()
    _say("    python -m ce_v5.entrypoints.operator_cli kill-switch deactivate \\")
    _say(f'        --id <kill_switch_id> --reason "{_MOTIVO_SOLTAR}" --actor alvaro')
    _say()
    _say(
        "  (Si lo perdiste: python -m ce_v5.entrypoints.operator_cli kill-switch list)"
    )
    input("  Pulsa ENTER cuando lo hayas soltado... ")


def _validacion_3(cliente: httpx.Client, token: str, automatico: bool) -> None:
    _titulo("VALIDACION 3 (b): FAIL-CLOSED EN EL BORDE, EN CALIENTE")

    _paso("3.1", "ANTES: /v1/capabilities")
    antes = _capabilities(cliente, token)
    _imprimir_decisiones(antes)
    decision_realtime = _decision(antes, _REALTIME_CAP)
    decision_sensible = _decision(antes, _SENSITIVE_CAP)
    if decision_realtime["decision"].lower() != "allow":
        raise ValidacionFallida(
            f"{_REALTIME_CAP} deberia salir ALLOW con el escenario sembrado. "
            "Corre antes: python tools/seed_p06b_fake.py"
        )
    if decision_sensible["decision"].lower() != "deny":
        raise ValidacionFallida(
            f"{_SENSITIVE_CAP} es SENSIBLE y no tiene entitlement: debia salir DENY. "
            "Que salga ALLOW significa que la API concedio algo por su cuenta."
        )
    _say("    lo NO sensible con regla ALLOW: concedido.")
    _say("    lo SENSIBLE sin entitlement y sin jurisdiccion conocida: DENEGADO.")
    _say("    la API no concede nada por su cuenta.")

    _activar_switch(automatico)

    # El cronometro del corte lo lleva _esperar_decision, que empieza a contar AQUI:
    # justo DESPUES de que el operador (subproceso o humano) haya terminado.
    _paso("3.3", "DESPUES, SIN REINICIAR NADA: /v1/capabilities otra vez")
    despues, transcurrido = _esperar_decision(cliente, token, _REALTIME_CAP, "deny")
    _say()
    _imprimir_decisiones(despues)
    corte = _decision(despues, _REALTIME_CAP)
    if corte["reason_code"] != "denied_by_kill_switch":
        raise ValidacionFallida(
            f"Se esperaba reason_code=denied_by_kill_switch y llego "
            f"{corte['reason_code']}. El DENY llego por otro motivo: no prueba nada."
        )
    if not corte["kill_switch_id"]:
        raise ValidacionFallida(
            "El DENY no trae kill_switch_id: la traza no dice QUE switch corto."
        )
    _say()
    _say(f"    ANTES   : {_REALTIME_CAP} ALLOW ({decision_realtime['reason_code']})")
    _say(
        f"    DESPUES : {_REALTIME_CAP} DENY  ({corte['reason_code']}, "
        f"kill_switch_id={corte['kill_switch_id']})"
    )

    _paso("3.4", "suscripcion realtime NUEVA con el switch activo")
    resultado = _suscripcion(token, {"type": "subscribe", "topic": _TOPIC})
    _say(f"    {resultado}")
    if resultado.mensaje is not None:
        raise ValidacionFallida(
            "El canal SUSCRIBIO con el kill switch activo. El borde no esta gateado."
        )
    _say("    RECHAZADA. El cierre es generico a proposito: no le dice al cliente por")
    _say("    que se le cierra la puerta.")
    _say("    El borde gateado dice que no, y el mismo token que valia hace un segundo")
    _say("    ya no vale. Sin reiniciar la API.")

    _paso("3.5", "cuanto tardo el corte")
    _say(f"    desde que el operador volvio hasta el primer DENY: {transcurrido:.2f} s")
    _say(
        f"    TTL del cache del capability set                 : {_CACHE_TTL_SECONDS} s"
    )
    if transcurrido >= _CACHE_TTL_SECONDS:
        raise ValidacionFallida(
            "El corte tardo lo que dura el TTL: pudo llegar por CADUCIDAD del cache, "
            "no por el evento. La demostracion no prueba lo que dice probar."
        )
    _say("    Tardo SEGUNDOS, no el TTL: el corte llego por EVENTO (outbox -> bus ->")
    _say("    invalidacion del cache), que es el mecanismo principal de ADR-012. Si")
    _say("    hubiera llegado por caducidad, habria tardado hasta un minuto.")
    _say("    (el cronometro arranca cuando el operador YA HABIA VUELTO, asi que el")
    _say("     corte verdadero fue aun mas rapido que este numero.)")

    _soltar_switch(automatico)

    final, vuelta = _esperar_decision(cliente, token, _REALTIME_CAP, "allow")
    _say()
    _imprimir_decisiones(final)
    _say()
    _say(f"    {_REALTIME_CAP} vuelve a ALLOW en {vuelta:.2f} s, EN CALIENTE.")
    _say("    El interruptor se enciende y se apaga sin tocar el proceso.")


def _validaciones(cliente: httpx.Client, automatico: bool) -> None:
    # La limpieza va ANTES de la validacion 1, no dentro de la 3: un switch heredado
    # cierra el borde y la suscripcion realtime de la validacion 1 muere con un 4403.
    _limpieza_previa(automatico)
    token = _validacion_1(cliente)
    yo = cliente.get("/v1/me", headers={"Authorization": f"Bearer {token}"}).json()
    _validacion_2(cliente, token, yo["user_id"], yo["tenant_id"])
    _validacion_3(cliente, token, automatico)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate_p06b_api",
        description="Validacion en caliente de la puerta publica (P06b).",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help=(
            "El kill switch lo mueve un humano en otra terminal (util para verlo paso "
            "a paso). Por defecto lo mueve el arnes, como subproceso del operador."
        ),
    )
    args = parser.parse_args(argv)
    automatico = not args.manual

    _say(
        "Validacion en caliente de P06b (Bloque H). Escenario FALSO, cero datos reales."
    )
    _say(f"API: {_BASE_URL}")
    _say(
        "Modo: AUTOMATICO (el arnes mueve el kill switch por subproceso del operador)."
        if automatico
        else "Modo: MANUAL (el kill switch lo mueve un humano en otra terminal)."
    )

    codigo = 0
    with httpx.Client(base_url=_BASE_URL, timeout=15.0) as cliente:
        try:
            _validaciones(cliente, automatico)
        except ValidacionFallida as fallo:
            _say()
            _say("!!! VALIDACION FALLIDA !!!")
            _say(f"    {fallo}")
            codigo = 1
        except httpx.HTTPError as error:
            _say()
            _say("!!! NO SE PUDO HABLAR CON LA API !!!")
            _say(f"    {type(error).__name__}: {error}")
            _say("    Arranca la API: python -m ce_v5.entrypoints.api")
            codigo = 1
        finally:
            # PASE LO QUE PASE, el switch se suelta. Una demostracion que deja la
            # plataforma cortada rompe el entorno del que venga detras: la siguiente
            # ejecucion arrancaria ya en DENY y no veria el corte, y cualquiera que
            # tocase el sistema lo encontraria apagado sin saber por que.
            if automatico:
                _say()
                _say("--- limpieza final: el switch no se queda puesto")
                try:
                    _soltar_switches(_REALTIME_CAP, _MOTIVO_SOLTAR)
                except ValidacionFallida as fallo:
                    _say(f"    NO SE PUDO SOLTAR EL KILL SWITCH: {fallo}")
                    _say("    SUELTALO A MANO antes de seguir trabajando.")
                    codigo = 1

    if codigo != 0:
        return codigo

    _titulo("LAS TRES VALIDACIONES PASARON")
    _say("1. Login y suscripcion realtime autenticada (token nunca en la URL).")
    _say("2. El cliente no puede imponer identidad por ninguna de las cuatro vias.")
    _say("3. El kill switch corta el borde EN CALIENTE, en segundos y sin reinicio.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
