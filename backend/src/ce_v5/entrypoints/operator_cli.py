"""Herramienta de operador (CA-04): kill switch y publicacion de version.

Porta la credencial de OPERADOR, asi que NO es un proceso de runtime: usa
OperatorDbConfig.from_env, JAMAS DbConfig.from_env (cuya guardia abortaria si
viera el DSN de operador). Cada subcomando ejecuta una primitiva atomica de
operator_admin (cambio + operator_audit + outbox en una sola transaccion) e
imprime lo que hizo, en texto plano para pegar en un informe.

Uso: python -m ce_v5.entrypoints.operator_cli <grupo> <accion> [opciones]
Requiere CE_V5_OPERATOR_DATABASE_URL.
"""

from __future__ import annotations

import argparse
import sys
from uuid import UUID, uuid4

from ce_v5.core.clock import SystemClock
from ce_v5.infra.db.config import DbConfig, OperatorDbConfig
from ce_v5.infra.db.operator_admin import (
    KillSwitchRow,
    OperatorActionResult,
    OperatorAdmin,
)
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from source.families.policy import KillSwitchScope

_SCOPES = [scope.value for scope in KillSwitchScope]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="operator_cli")
    groups = parser.add_subparsers(dest="group", required=True)

    kill_switch = groups.add_parser("kill-switch")
    actions = kill_switch.add_subparsers(dest="action", required=True)

    activate = actions.add_parser("activate")
    activate.add_argument("--scope", required=True, choices=_SCOPES)
    activate.add_argument("--target-ref", default=None)
    activate.add_argument("--tenant-id", default=None)
    activate.add_argument("--user-id", default=None)
    activate.add_argument("--reason", required=True)
    activate.add_argument("--actor", required=True)

    deactivate = actions.add_parser("deactivate")
    deactivate.add_argument("--id", required=True)
    deactivate.add_argument("--reason", required=True)
    deactivate.add_argument("--actor", required=True)

    actions.add_parser("list")

    policy = groups.add_parser("policy")
    policy_actions = policy.add_subparsers(dest="action", required=True)
    publish = policy_actions.add_parser("publish")
    publish.add_argument("--version", required=True)
    publish.add_argument("--reason", required=True)
    publish.add_argument("--actor", required=True)

    return parser


def _print_result(result: OperatorActionResult) -> None:
    print(f"action: {result.action}")
    print(f"event_id: {result.event_id}")
    print(f"correlation_id: {result.correlation_id}")
    if result.kill_switch_id is not None:
        print(f"kill_switch_id: {result.kill_switch_id}")
    if result.policy_version is not None:
        print(f"policy_version: {result.policy_version}")
        print(f"previous_current: {result.previous_current}")
        print(f"new_current: {result.new_current}")


def _print_kill_switches(rows: list[KillSwitchRow]) -> None:
    if not rows:
        print("(sin kill switches)")
        return
    for row in rows:
        estado = "activo" if row.active else "inactivo"
        print(
            f"{row.kill_switch_id}  {row.scope}  target_ref={row.target_ref}  "
            f"tenant_id={row.tenant_id}  user_id={row.user_id}  {estado}  "
            f"reason={row.reason_code}  actor={row.actor}"
        )


def _dispatch(admin: OperatorAdmin, args: argparse.Namespace) -> None:
    correlation_id = uuid4().hex
    if args.group == "kill-switch" and args.action == "activate":
        _print_result(
            admin.activate_kill_switch(
                scope=KillSwitchScope(args.scope),
                target_ref=args.target_ref,
                tenant_id=args.tenant_id,
                user_id=args.user_id,
                reason_code=args.reason,
                actor=args.actor,
                correlation_id=correlation_id,
            )
        )
    elif args.group == "kill-switch" and args.action == "deactivate":
        _print_result(
            admin.deactivate_kill_switch(
                UUID(args.id),
                reason_code=args.reason,
                actor=args.actor,
                correlation_id=correlation_id,
            )
        )
    elif args.group == "kill-switch" and args.action == "list":
        _print_kill_switches(admin.list_kill_switches())
    else:  # policy publish
        _print_result(
            admin.publish_policy_version(
                args.version,
                actor=args.actor,
                reason_code=args.reason,
                correlation_id=correlation_id,
            )
        )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    database = PsycopgDatabase(DbConfig(dsn=OperatorDbConfig.from_env().dsn))
    try:
        _dispatch(OperatorAdmin(database, SystemClock()), args)
    finally:
        database.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
