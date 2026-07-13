"""Canal realtime autenticado (P06b, dictamen CSA K; ADR-019).

EL TOKEN NO VIAJA EN LA URL: llega en el PRIMER MENSAJE. Una URL queda escrita en los
logs del servidor, en el historial del navegador y en el Referer que se manda a
terceros; un token ahi es un token publicado.

EL CLIENTE NO IMPONE NADA: la identidad sale del token verificado y el TENANT lo
resuelve el backend desde la pertenencia (ADR-011). El contrato no tiene donde poner un
user_id ni un tenant_id, y prohibe campos extra.

ENTREGA FAIL-CLOSED: solo salen por el socket los envelopes que se pueden ATRIBUIR CON
CERTEZA al sujeto (scope tenant de SU tenant, o scope user de SU usuario). Los de scope
system son de plataforma, no de un usuario, y NO se entregan. Lo que no se puede
atribuir, no se envia.

TODOS LOS CIERRES POR FALLO DE AUTENTICACION USAN EL MISMO CODIGO Y EL MISMO MOTIVO: si
un token caducado se distinguiera de uno falso, el canal seria un oraculo.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ce_v5.core.auth.rate_limit import ACTION_REALTIME, Attempt, digest
from ce_v5.core.auth.tokens import AuthenticatedPrincipal, InvalidAccessTokenError
from ce_v5.core.bus import Offset, UnknownOffsetError
from ce_v5.entrypoints.api.client_ip import client_ip
from ce_v5.entrypoints.api.composition import ApiContext
from ce_v5.entrypoints.api.observability import log_event
from ce_v5.entrypoints.api.policy_enforcement import (
    CapabilityDenied,
    require_capability,
)
from source.api import RealtimeAck, RealtimeAuth, RealtimeEvent, RealtimeSubscribe

router = APIRouter(prefix="/v1")

# La UNICA capability que P06b gatea: las cinco sensibles del catalogo son de piezas
# posteriores (la ejecucion es M5).
SUBSCRIBE_REALTIME = "subscribe_realtime"

AUTH_TIMEOUT_SECONDS = 5
HEARTBEAT_SECONDS = 20
IDLE_TIMEOUT_SECONDS = 60
# Un WebSocket sin limite de mensaje es una via de agotamiento de memoria.
MAX_MESSAGE_BYTES = 4096
MAX_CONNECTIONS_PER_USER = 5
MAX_CONNECTIONS_PER_IP = 20
_REPLAY_BATCH = 100

# Codigos de cierre. TODO fallo de autenticacion usa 4401 y el MISMO motivo: distinguir
# "token caducado" de "token falso" convertiria el canal en un oraculo.
CLOSE_AUTH_FAILED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_TOO_MANY = 4429
CLOSE_MESSAGE_TOO_BIG = 4413
CLOSE_PROTOCOL = 4400
_AUTH_FAILED_REASON = "auth_failed"

# LIMITE POR PROCESO. Con varias replicas detras de un balanceador, cada proceso
# contaria las suyas y el limite real seria N veces mayor. Un contador COMPARTIDO
# (Redis) es lo que hace falta entonces, y es tarea de la pieza de DESPLIEGUE/ESCALADO:
# no se construye hoy "por si acaso" algo que no se puede probar sin varias replicas.
_conexiones_por_usuario: dict[str, int] = defaultdict(int)
_conexiones_por_ip: dict[str, int] = defaultdict(int)


def _cabe(raw: str) -> bool:
    return len(raw.encode("utf-8")) <= MAX_MESSAGE_BYTES


def _para_el_sujeto(envelope: dict[str, object], tenant_id: str, user_id: UUID) -> bool:
    """True solo si el envelope se puede ATRIBUIR CON CERTEZA a este sujeto."""
    scope = envelope.get("scope")
    if scope == "tenant":
        return envelope.get("tenant_id") == tenant_id
    if scope == "user":
        return envelope.get("tenant_id") == tenant_id and envelope.get(
            "user_id"
        ) == str(user_id)
    # system y public_market NO son de un usuario: no se entregan (fail-closed).
    return False


async def _recibir(websocket: WebSocket, timeout: float) -> str | None:
    """Un mensaje del cliente, o None si no llego a tiempo."""
    try:
        return await asyncio.wait_for(websocket.receive_text(), timeout)
    except TimeoutError:
        return None


async def _autenticar(
    websocket: WebSocket, context: ApiContext
) -> AuthenticatedPrincipal | None:
    """Primer mensaje: la sesion. Devuelve None si hay que cerrar (ya cerrado).

    El exp viene DENTRO del principal: el unico modulo que toca JWT es core/auth/tokens.
    """
    raw = await _recibir(websocket, AUTH_TIMEOUT_SECONDS)
    if raw is None or not _cabe(raw):
        await websocket.close(CLOSE_AUTH_FAILED, _AUTH_FAILED_REASON)
        return None
    try:
        mensaje = RealtimeAuth.model_validate_json(raw)
        return context.tokens.verify(mensaje.access_token)
    except (ValidationError, InvalidAccessTokenError, ValueError):
        # MISMO cierre para todo: token ausente, malformado, falso o caducado.
        await websocket.close(CLOSE_AUTH_FAILED, _AUTH_FAILED_REASON)
        return None


async def _suscribir(
    websocket: WebSocket,
    context: ApiContext,
    principal: AuthenticatedPrincipal,
    tenant_id: str,
    ip: str | None,
) -> tuple[str, Offset | None] | None:
    """Segundo mensaje: la suscripcion, gateada por politica."""
    raw = await _recibir(websocket, IDLE_TIMEOUT_SECONDS)
    if raw is None or not _cabe(raw):
        await websocket.close(CLOSE_MESSAGE_TOO_BIG, "message_too_big")
        return None
    try:
        mensaje = RealtimeSubscribe.model_validate_json(raw)
    except ValidationError:
        # Un user_id o un tenant_id colados en el mensaje mueren AQUI: el contrato
        # prohibe campos extra, asi que el cliente no puede imponer identidad.
        await websocket.close(CLOSE_PROTOCOL, "protocol_error")
        return None

    try:
        # EL BORDE GATEADO de P06b: solo un ALLOW explicito deja suscribir.
        require_capability(
            context, principal.user_id, tenant_id, ip, SUBSCRIBE_REALTIME
        )
    except CapabilityDenied as denied:
        log_event(
            "realtime.subscribe_denied",
            capability_id=denied.capability_id,
            reason=denied.reason_code,
        )
        await websocket.close(CLOSE_FORBIDDEN, "forbidden")
        return None

    checkpoint = None if mensaje.checkpoint is None else Offset(mensaje.checkpoint)
    return mensaje.topic, checkpoint


def _cursor_inicial(
    context: ApiContext, topic: str, checkpoint: Offset | None
) -> Offset | None:
    """Desde donde se reanuda.

    Con checkpoint: desde ahi (replay es EXCLUSIVO, asi que no se repite el ultimo).
    Sin checkpoint: desde el FINAL REAL del topic (latest_offset, CA-12), o desde el
    principio si esta vacio, que es lo mismo: no hay historia que saltarse.

    Antes esto era un APANO: se leian los primeros 100 mensajes del historico y se
    tomaba el ultimo de ESA VENTANA como "el final". En cuanto el topic pasaba de 100
    mensajes, el cursor se quedaba en el mensaje 100 y el cliente recibia eventos
    ANTIGUOS COMO SI FUERAN NUEVOS. La primitiva del puerto lo resuelve en O(1).
    """
    if checkpoint is not None:
        return checkpoint
    return context.bus.latest_offset(topic)


@router.websocket("/realtime")
async def realtime(websocket: WebSocket) -> None:
    """Canal autenticado. El token va en el primer MENSAJE, jamas en la URL."""
    context: ApiContext = websocket.app.state.context
    # client_ip solo mira request.client y las cabeceras, que un WebSocket tambien
    # tiene: es el UNICO sitio que decide la IP, y no se duplica esa logica aqui.
    ip = client_ip(cast(Request, cast(object, websocket)), context.api_config)

    await websocket.accept()

    # Limite del handshake (dimension IP: aqui todavia no hay cuenta).
    intento = Attempt(
        action=ACTION_REALTIME,
        ip_digest=None if ip is None else digest(ip, context.rate_config.digest_secret),
        account_digest=None,
    )
    if not context.limiter.check(intento).allowed:
        await websocket.close(CLOSE_AUTH_FAILED, _AUTH_FAILED_REASON)
        return

    principal = await _autenticar(websocket, context)
    if principal is None:
        return

    # El TENANT lo resuelve el BACKEND desde la pertenencia. El cliente no lo manda.
    with context.scoped_db.transaction(principal.user_id) as scoped:
        tenant_id = str(scoped.context.tenant_id)

    clave_usuario = str(principal.user_id)
    clave_ip = ip or "desconocida"
    if (
        _conexiones_por_usuario[clave_usuario] >= MAX_CONNECTIONS_PER_USER
        or _conexiones_por_ip[clave_ip] >= MAX_CONNECTIONS_PER_IP
    ):
        await websocket.close(CLOSE_TOO_MANY, "too_many_connections")
        return
    _conexiones_por_usuario[clave_usuario] += 1
    _conexiones_por_ip[clave_ip] += 1

    try:
        suscripcion = await _suscribir(websocket, context, principal, tenant_id, ip)
        if suscripcion is None:
            return
        topic, checkpoint = suscripcion
        cursor = _cursor_inicial(context, topic, checkpoint)
        await websocket.send_text(
            RealtimeAck(
                type="ack",
                topic=topic,
                checkpoint=None if cursor is None else cursor.value,
            ).model_dump_json()
        )
        await _bombear(websocket, context, principal, tenant_id, topic, cursor)
    except WebSocketDisconnect:
        pass
    finally:
        _conexiones_por_usuario[clave_usuario] -= 1
        _conexiones_por_ip[clave_ip] -= 1


async def _bombear(
    websocket: WebSocket,
    context: ApiContext,
    principal: AuthenticatedPrincipal,
    tenant_id: str,
    topic: str,
    cursor: Offset | None,
) -> None:
    """Entrega eventos, vigila el latido y revalida la sesion."""
    ultimo_latido = time.monotonic()
    ultima_senal = time.monotonic()

    while True:
        # 1. Eventos nuevos desde el cursor.
        try:
            recibidos = context.bus.replay(
                topic, start=cursor, max_messages=_REPLAY_BATCH
            )
        except UnknownOffsetError:
            # El checkpoint ya no existe en el historial: no se avanza en silencio.
            await websocket.close(CLOSE_PROTOCOL, "unknown_checkpoint")
            return
        for recibido in recibidos:
            cursor = recibido.delivery.offset
            envelope = json.loads(recibido.message.envelope)
            if not isinstance(envelope, dict):
                continue
            # Fail-closed: lo que no se atribuye con certeza al sujeto, no sale.
            if not _para_el_sujeto(envelope, tenant_id, principal.user_id):
                continue
            await websocket.send_text(
                RealtimeEvent(
                    type="event",
                    topic=topic,
                    checkpoint=cursor.value,
                    envelope=envelope,
                ).model_dump_json()
            )

        # 2. Mensajes del cliente (pong o lo que sea): renuevan la senal de vida.
        raw = await _recibir(websocket, 0.05)
        if raw is not None:
            if not _cabe(raw):
                await websocket.close(CLOSE_MESSAGE_TOO_BIG, "message_too_big")
                return
            ultima_senal = time.monotonic()

        ahora = time.monotonic()

        # 3. La sesion no vive para siempre solo porque el socket siga abierto. El exp
        # viene del token YA VERIFICADO (core/auth/tokens): aqui no se decodifica nada.
        if time.time() >= principal.expires_at_seconds:
            await websocket.close(CLOSE_AUTH_FAILED, _AUTH_FAILED_REASON)
            return

        # 4. Latido: una conexion zombi consume memoria para siempre.
        if ahora - ultimo_latido >= HEARTBEAT_SECONDS:
            await websocket.send_text('{"type":"ping"}')
            ultimo_latido = ahora
        if ahora - ultima_senal >= IDLE_TIMEOUT_SECONDS:
            await websocket.close(CLOSE_PROTOCOL, "idle_timeout")
            return
