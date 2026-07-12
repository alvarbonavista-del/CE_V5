"""Validacion en caliente CRITICA de P06: el kill switch corta SIN reinicio (B9).

NO es un test: es una DEMOSTRACION VIVA. Un unico proceso monta la cadena real de
politica y, en un bucle, le pregunta al gate por dos capacidades cada segundo.
Mientras corre, el operador (en otra terminal, con su rol) activa un kill switch;
el evento policy.* llega por el bus, invalida el cache y la MISMA iteracion del
MISMO proceso pasa de ALLOW a DENY. El contador de iteracion demuestra que el
proceso no se reinicio: el corte es en caliente (ADR-012).

Corre con el ROL DE APLICACION via DbConfig.from_env, y por eso DEMUESTRA la
guardia CA-03: si alguien exportara CE_V5_OPERATOR_DATABASE_URL en esta ventana,
from_env lanzaria OperatorDsnInRuntimeError y el proceso NO arrancaria. Un proceso
de runtime jamas porta la credencial de operador.

Cadena real, sin atajos: PostgresPolicyStore -> PolicyEvaluator ->
CapabilitySetCache + CachedPolicyEvaluator -> PolicyGate con
PostgresSensitiveActionAudit. Entradas del sujeto con los proveedores estaticos.

Uso: python -m ce_v5.entrypoints.hot_validation_policy <tenant_id> <user_id>
Requiere CE_V5_DATABASE_URL (app) y CE_V5_REDIS_URL. Ctrl+C para parar.
"""

from __future__ import annotations

import argparse
import json
import time

from ce_v5.core.clock import SystemClock
from ce_v5.core.policy import (
    CachedPolicyEvaluator,
    CapabilitySetCache,
    EvidenceSource,
    JurisdictionEvidence,
    KycStatus,
    PolicyCacheInvalidator,
    PolicyDenied,
    PolicyEvaluator,
    PolicyGate,
    PolicyInputs,
    StaticKycProvider,
    StaticVpnDetector,
    TrustHierarchy,
    resolve_jurisdiction,
)
from ce_v5.infra.bus_redis import RedisBusConfig, RedisEventBus, create_client
from ce_v5.infra.db.config import DbConfig
from ce_v5.infra.db.outbox_publisher import OutboxPublisher
from ce_v5.infra.db.policy_store import PostgresPolicyStore
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.infra.db.sensitive_audit import PostgresSensitiveActionAudit
from source.families.policy import (
    KillSwitchPayload,
    PolicyEventType,
    PolicyVersionPublishedPayload,
    SubjectInvalidatedPayload,
)

_TOPIC = "policy"
_GROUP = "hot-policy-demo"
_CONSUMER = "demo"
_CAPABILITIES = ("execute_order", "view_dashboard")
# IP de documentacion (TEST-NET-3): claramente ficticia, jamas una IP real.
_DEMO_IP = "203.0.113.10"

# TTL LARGO A PROPOSITO (60 s). MOTIVO: si el TTL fuese corto, un observador
# podria creer que el cambio se propago por CADUCIDAD del cache, no por el evento.
# Con el TTL largo, si una capability cambia en ~1 segundo es porque el EVENTO la
# invalido (invalidacion por evento, mecanismo PRINCIPAL de ADR-012). La demo debe
# probar lo que dice probar.
_MAX_STALENESS_MS = 60_000


def _say(message: str) -> None:
    print(message, flush=True)


def _subject_inputs(tenant_id: str, user_id: str) -> PolicyInputs:
    """Entradas del sujeto con los proveedores estaticos (fail-closed por omision).

    Jurisdiccion 'AA' por KYC, KYC verificado, IP limpia (no VPN), plan 'plan_x'.
    Todo INVENTADO: sirve para la demo, no describe a nadie real.
    """
    kyc = StaticKycProvider(
        statuses={(tenant_id, user_id): KycStatus.VERIFIED},
        jurisdictions={(tenant_id, user_id): "AA"},
    )
    vpn = StaticVpnDetector(vpn_ips=frozenset(), clean_ips=frozenset({_DEMO_IP}))
    evidence = JurisdictionEvidence(
        EvidenceSource.KYC, kyc.jurisdiction_for_subject(tenant_id, user_id)
    )
    jurisdiction = resolve_jurisdiction([evidence], TrustHierarchy.default())
    return PolicyInputs(
        subject_tenant_id=tenant_id,
        subject_user_id=user_id,
        jurisdiction=jurisdiction,
        kyc_status=kyc.status_for_subject(tenant_id, user_id),
        vpn_detected=vpn.is_vpn(_DEMO_IP),
        plan="plan_x",
        role=None,
    )


def _handle_event(
    invalidator: PolicyCacheInvalidator, event_type: str, envelope: dict[str, object]
) -> None:
    payload = envelope.get("payload", {})
    if event_type in (
        PolicyEventType.KILL_SWITCH_ACTIVATED.value,
        PolicyEventType.KILL_SWITCH_DEACTIVATED.value,
    ):
        invalidator.on_kill_switch_changed(KillSwitchPayload.model_validate(payload))
    elif event_type == PolicyEventType.VERSION_PUBLISHED.value:
        invalidator.on_version_published(
            PolicyVersionPublishedPayload.model_validate(payload)
        )
    elif event_type == PolicyEventType.SUBJECT_INVALIDATED.value:
        invalidator.on_subject_invalidated(
            SubjectInvalidatedPayload.model_validate(payload)
        )
    else:
        return
    _say(f"  [invalidacion] cache invalidado por {event_type}")


def _ask_gate(gate: PolicyGate, inputs: PolicyInputs, capability_id: str) -> None:
    try:
        decision = gate.require(inputs, capability_id)
        _say(f"  [gate] {capability_id:<15} ALLOW  ({decision.reason_code.value})")
    except PolicyDenied as denied:
        _say(
            f"  [gate] {capability_id:<15} DENY   ({denied.decision.reason_code.value})"
        )


def _iteration(
    publisher: OutboxPublisher,
    bus: RedisEventBus,
    invalidator: PolicyCacheInvalidator,
    gate: PolicyGate,
    inputs: PolicyInputs,
    number: int,
) -> None:
    _say(f"--- iteracion {number} (mismo proceso, sin reinicio) ---")
    # 1) Drena la outbox: los eventos que el OPERADOR escribio en su transaccion
    #    salen al bus (DB -> bus, ADR-013).
    drained = publisher.drain_once(batch_size=100)
    if drained:
        _say(f"  [outbox] {drained} evento(s) publicado(s) al bus")
    # 2) Consume el topic 'policy' y aplica la invalidacion por evento.
    for received in bus.poll(_TOPIC, _GROUP, _CONSUMER, max_messages=50, block_ms=0):
        message = received.message
        _say(f"  [evento] {message.event_id} {message.event_type}")
        envelope = json.loads(message.envelope)
        if isinstance(envelope, dict):
            _handle_event(invalidator, message.event_type, envelope)
        bus.ack(received.delivery)
    # 3) Pregunta autoritativa al gate por cada capability.
    for capability_id in _CAPABILITIES:
        _ask_gate(gate, inputs, capability_id)


def _run(tenant_id: str, user_id: str) -> None:
    clock = SystemClock()
    # Cadena real, sin atajos.
    database = PsycopgDatabase(DbConfig.from_env())
    bus_config = RedisBusConfig.from_env()
    client = create_client(bus_config)
    bus = RedisEventBus(client, bus_config)

    store = PostgresPolicyStore(database)
    evaluator = PolicyEvaluator(store, clock)
    cache = CapabilitySetCache(clock, max_staleness_ms=_MAX_STALENESS_MS)
    cached = CachedPolicyEvaluator(evaluator, cache)
    audit = PostgresSensitiveActionAudit(database)
    gate = PolicyGate(cached, audit)
    # El invalidator comparte EL MISMO cache que el CachedPolicyEvaluator: por eso
    # un evento tira la entrada que el gate reusaria.
    invalidator = PolicyCacheInvalidator(cache)
    publisher = OutboxPublisher(db=database, bus=bus)
    inputs = _subject_inputs(tenant_id, user_id)

    bus.ensure_group(_TOPIC, _GROUP)
    _say("=== Validacion en caliente P06: kill switch que corta SIN reinicio ===")
    _say("Rol de APLICACION (DbConfig.from_env): la guardia CA-03 ya actuo.")
    _say(f"Sujeto: tenant_id={tenant_id} user_id={user_id}")
    _say(f"TTL del cache = {_MAX_STALENESS_MS} ms (largo a proposito): un cambio en")
    _say("~1 s prueba la invalidacion por EVENTO, no por caducidad. Ctrl+C para parar.")
    _say("")

    number = 0
    try:
        while True:
            number += 1
            _iteration(publisher, bus, invalidator, gate, inputs, number)
            time.sleep(1)
    except KeyboardInterrupt:
        _say("\nParado por el operador (Ctrl+C). El proceso nunca se reinicio.")
    finally:
        client.close()
        database.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validacion en caliente de politica (P06-B9)."
    )
    parser.add_argument("tenant_id", help="tenant_id que imprimio seed_p06_fake.py")
    parser.add_argument("user_id", help="user_id que imprimio seed_p06_fake.py")
    args = parser.parse_args()
    _run(args.tenant_id, args.user_id)


if __name__ == "__main__":
    main()
