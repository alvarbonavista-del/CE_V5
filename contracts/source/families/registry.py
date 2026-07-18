"""Registro canonico event_type -> payload (CA-06).

POR QUE EXISTE: el OutboxPublisher (P03) validaba el envelope drenado contra
Envelope[EventPayload] BASE, y EventPayload tiene extra="forbid" con CERO campos.
Eso solo aceptaba payloads VACIOS: cualquier evento con contenido real habria
sido rechazado, asi que la garantia de ADR-006 era ILUSORIA. Este registro mapea
cada event_type CONCRETO a su clase de payload y a su event_schema_version, para
validar contra la clase REAL.

REGLA DE GOBIERNO: todo event_type nuevo entra en UNO de los dos mapas: en
EVENT_PAYLOAD_REGISTRY si su payload ya existe, o en DEFERRED_EVENT_TYPES si su
payload y su productor los define una pieza futura. El check
tools/check_event_payload_registry.py lo hace cumplir: ningun event_type
declarado en contracts/source/families/* puede faltar en ambos mapas ni aparecer
en los dos.

Registro EXPLICITO por event_type, NO union discriminada central: una union
importaria TODOS los payloads de TODAS las familias y arriesgaria el ciclo
envelope<->families con el que P02 ya tropezo. Por eso este modulo NO se
reexporta desde source.families.__init__: solo lo importa quien lo necesita
(el publisher y el check).
"""

from __future__ import annotations

from dataclasses import dataclass

from source.envelope import EventPayload
from source.families.alert import AlertEventType, AlertRaisedPayload
from source.families.component import ComponentEventType, ComponentLifecyclePayload
from source.families.market import (
    CandleClosedPayload,
    CandleCorrectedPayload,
    CandleUpdatedPayload,
    MarketCandleEventType,
)
from source.families.policy import (
    KillSwitchPayload,
    PolicyEventType,
    PolicyVersionPublishedPayload,
    SubjectInvalidatedPayload,
)
from source.families.rule import (
    RuleEvaluationCompletedPayload,
    RuleEventType,
    RuleFiringPayload,
    RuleResolvedPayload,
)
from source.families.signal import SignalEventType, SignalRaisedPayload
from source.families.user import UserEventType, UserRegisteredPayload


class EventPayloadRegistryError(RuntimeError):
    """Error al resolver el payload de un event_type (CA-06)."""


class UnknownEventTypePayloadError(EventPayloadRegistryError):
    """El event_type no esta en el registro ni en los diferidos (CA-06)."""


class DeferredEventTypeError(EventPayloadRegistryError):
    """El event_type esta declarado, pero su payload/productor son de otra pieza.

    Su taxonomia existe, su payload aun no: hoy NADIE puede emitirlo, y por eso
    tampoco puede publicarse.
    """


# event_type CONCRETO -> (clase de payload concreta, event_schema_version).
EVENT_PAYLOAD_REGISTRY: dict[str, tuple[type[EventPayload], int]] = {
    ComponentEventType.REGISTERED.value: (ComponentLifecyclePayload, 1),
    ComponentEventType.INITIALIZING.value: (ComponentLifecyclePayload, 1),
    ComponentEventType.INITIALIZED.value: (ComponentLifecyclePayload, 1),
    ComponentEventType.STARTING.value: (ComponentLifecyclePayload, 1),
    ComponentEventType.RUNNING.value: (ComponentLifecyclePayload, 1),
    ComponentEventType.PAUSED.value: (ComponentLifecyclePayload, 1),
    ComponentEventType.STOPPING.value: (ComponentLifecyclePayload, 1),
    ComponentEventType.STOPPED.value: (ComponentLifecyclePayload, 1),
    ComponentEventType.UNLOADED.value: (ComponentLifecyclePayload, 1),
    ComponentEventType.FAILED.value: (ComponentLifecyclePayload, 1),
    ComponentEventType.QUARANTINED.value: (ComponentLifecyclePayload, 1),
    PolicyEventType.KILL_SWITCH_ACTIVATED.value: (KillSwitchPayload, 1),
    PolicyEventType.KILL_SWITCH_DEACTIVATED.value: (KillSwitchPayload, 1),
    PolicyEventType.VERSION_PUBLISHED.value: (PolicyVersionPublishedPayload, 1),
    PolicyEventType.SUBJECT_INVALIDATED.value: (SubjectInvalidatedPayload, 1),
    # NO va a DEFERRED_EVENT_TYPES: tiene payload y tiene PRODUCTOR REAL desde hoy (el
    # alta de la API, P06b).
    UserEventType.REGISTERED.value: (UserRegisteredPayload, 1),
    # market.* (CA-06, pagado en P07): payload OHLCV real y productor real (el
    # ingestor de mercado). Cada tipo apunta a su subclase concreta, que FIJA su
    # maturity_state: una vela cerrada marcada como provisional la rechaza el
    # contrato, no el codigo.
    MarketCandleEventType.CANDLE_UPDATED.value: (CandleUpdatedPayload, 1),
    MarketCandleEventType.CANDLE_CLOSED.value: (CandleClosedPayload, 1),
    MarketCandleEventType.CANDLE_CORRECTED.value: (CandleCorrectedPayload, 1),
    # rule.* (P08): ciclo de evaluacion neutral; solo por transicion (CA-P08-01).
    RuleEventType.EVALUATION_COMPLETED.value: (RuleEvaluationCompletedPayload, 1),
    RuleEventType.FIRING.value: (RuleFiringPayload, 1),
    RuleEventType.RESOLVED.value: (RuleResolvedPayload, 1),
    # signal.*/alert.* (P08): proyecciones derivadas de rule.firing (causation_id).
    SignalEventType.RAISED.value: (SignalRaisedPayload, 1),
    AlertEventType.RAISED.value: (AlertRaisedPayload, 1),
}

# Estado unico y constante de un tipo diferido: diferido HASTA que cierre su
# pieza duena. No hay otros estados; el check exige exactamente este valor.
DEFERRED_STATUS = "deferred_until_piece"


@dataclass(frozen=True, slots=True)
class DeferredEventType:
    """Entrada ESTRUCTURADA de un event_type diferido (CA-06, exigencia del CSA).

    Un tipo diferido no puede ser una cadena suelta que se aparca y se olvida:
    lleva SIETE campos obligatorios y no vacios que dicen QUE falta, QUIEN lo
    pagara y CUANDO deja de estar diferido. El check
    tools/check_event_payload_registry.py hace cumplir cada campo, que status sea
    exactamente DEFERRED_STATUS, que owner_piece sea una pieza del roadmap AUN NO
    cerrada, y que nadie use ya el tipo (un diferido en uso es una mentira).
    """

    event_type: str  # el tipo concreto.
    family: str  # la familia a la que pertenece.
    motivo: str  # por que se declara la taxonomia hoy.
    owner_piece: str  # la PIEZA DUENA concreta que lo pagara.
    dependency_reason: str  # QUE parte del payload exige esa pieza posterior.
    exit_rule: str  # que pasa al cerrar la pieza duena (se registra o se elimina).
    status: str = DEFERRED_STATUS  # constante: diferido hasta cerrar la pieza.


# event_type declarado cuya taxonomia existe pero cuyo PAYLOAD y PRODUCTOR los
# define una pieza futura: hoy NADIE puede emitirlos. Cada entrada es honesta.
# VACIO desde P07: los tres market.* eran los unicos diferidos y ya tienen
# payload real y productor real (la ingesta de mercado). No queda en CE v5 ni un
# solo event_type declarado sin payload. El mapa se conserva (no se borra) porque
# es el mecanismo de gobierno de CA-06 para las piezas que vengan.
DEFERRED_EVENT_TYPES: dict[str, DeferredEventType] = {}


def _resolve(event_type: str) -> tuple[type[EventPayload], int]:
    entry = EVENT_PAYLOAD_REGISTRY.get(event_type)
    if entry is not None:
        return entry
    responsible = DEFERRED_EVENT_TYPES.get(event_type)
    if responsible is not None:
        raise DeferredEventTypeError(
            f"event_type {event_type!r} esta diferido a la pieza "
            f"{responsible.owner_piece}: su payload y su productor aun no existen; "
            "nadie puede emitirlo."
        )
    raise UnknownEventTypePayloadError(
        f"event_type {event_type!r} no esta en el registro de payloads "
        "(contracts/source/families/registry.py); todo event_type nuevo se "
        "registra alli (CA-06)."
    )


def payload_class_for(event_type: str) -> type[EventPayload]:
    """Clase de payload concreta de un event_type (CA-06).

    Lanza DeferredEventTypeError si el tipo es de una pieza futura y
    UnknownEventTypePayloadError si no esta registrado. JAMAS devuelve
    EventPayload base, dict ni Any.
    """
    return _resolve(event_type)[0]


def expected_event_schema_version(event_type: str) -> int:
    """event_schema_version esperada para un event_type registrado (CA-06).

    Misma semantica de fallo que payload_class_for (diferido/desconocido lanzan).
    """
    return _resolve(event_type)[1]
