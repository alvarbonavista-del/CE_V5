"""Adaptador de las VENTANILLAS de identidad (P06b, CA-07 opcion A).

El rol de aplicacion NO tiene ningun privilegio de tabla sobre app_user,
user_credential ni user_session: solo puede EJECUTAR las funciones SECURITY DEFINER
de la migracion 0010. Este modulo es el UNICO punto del backend que las llama. Si
manana alguien intentase leer esas tablas directamente, PostgreSQL se lo negaria; no
es una convencion, es el motor.

La contrasena en claro JAMAS llega aqui: se recibe el hash Argon2id ya calculado en
el nucleo (CA-07 p.6, frontera dura: cero logica de negocio en la base).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg

from ce_v5.core.auth.email import normalize_email
from ce_v5.core.auth.ports import (
    RegisteredUser,
    RotationOutcome,
    RotationResult,
    StoredCredential,
)
from ce_v5.core.auth.service import EmailAlreadyRegisteredError
from ce_v5.core.clock.protocol import Clock
from ce_v5.infra.db.outbox import OutboxEvent, enqueue_event
from ce_v5.infra.db.ports import Database, Session
from ce_v5.infra.db.tenancy import (
    assert_app_role_cannot_bypass_rls,
    create_tenant_for_user_in_session,
)
from source.envelope import Envelope, Scope
from source.families.user import UserEventType, UserRegisteredPayload

_USER_EVENT_SCHEMA_VERSION = 1

_REGISTER_USER = "SELECT auth_register_user(%s, %s)"
_CREDENTIAL_FOR_EMAIL = (
    "SELECT out_user_id, out_password_hash, out_status "
    "FROM auth_credential_for_email(%s)"
)
_CREATE_SESSION = "SELECT auth_create_session(%s, %s, %s)"
_ROTATE_SESSION = (
    "SELECT out_outcome, out_user_id, out_session_id "
    "FROM auth_rotate_session(%s, %s, %s)"
)
_REVOKE_FAMILY = "SELECT auth_revoke_session_family(%s)"


class IdentityError(RuntimeError):
    """Error al operar contra las ventanillas de identidad (P06b)."""


def register_user_in_session(session: Session, email: str, password_hash: str) -> UUID:
    """Da de alta usuario + credencial DENTRO de la transaccion en curso.

    Existe la variante en sesion para que el alta pueda ser ATOMICA junto con el
    tenant, la pertenencia y la fila de outbox: un usuario sin tenant no podria
    resolver contexto y quedaria inutilizable.
    """
    row = session.fetchone(_REGISTER_USER, (normalize_email(email), password_hash))
    if row is None or row[0] is None:
        raise IdentityError(
            "La ventanilla auth_register_user no devolvio un identificador de usuario."
        )
    return UUID(str(row[0]))


def register_user(db: Database, email: str, password_hash: str) -> UUID:
    """Da de alta usuario + credencial en su propia transaccion."""
    with db.transaction() as session:
        return register_user_in_session(session, email, password_hash)


class PostgresUserRegistrar:
    """Alta atomica en UNA transaccion: usuario, credencial, tenant, pertenencia y
    EVENTO.

    Si el alta se partiera en dos transacciones, un fallo intermedio dejaria un usuario
    sin tenant: un usuario que no puede entrar a ninguna parte.
    """

    def __init__(self, db: Database, clock: Clock, source: str = "api") -> None:
        self._db = db
        self._clock = clock
        self._source = source

    def register(self, email: str, password_hash: str) -> RegisteredUser:
        try:
            with self._db.transaction() as session:
                assert_app_role_cannot_bypass_rls(session)
                user_id = register_user_in_session(session, email, password_hash)
                tenant_id = create_tenant_for_user_in_session(session, user_id)
                # MISMA TRANSACCION que el alta (patron outbox, P02b/ADR-013): o existen
                # la cuenta, el tenant, la pertenencia Y el evento, o no existe nada. Si
                # el evento se publicara DESPUES, en otra transaccion, un corte en medio
                # dejaria un usuario del que el resto del sistema nunca se entero: un
                # fantasma.
                self._enqueue_registered(session, user_id, tenant_id)
        except psycopg.errors.UniqueViolation as exc:
            raise EmailAlreadyRegisteredError(
                "Ya existe una cuenta con ese email."
            ) from exc
        return RegisteredUser(user_id=user_id, tenant_id=tenant_id)

    def _enqueue_registered(
        self, session: Session, user_id: UUID, tenant_id: UUID
    ) -> None:
        """Encola user.registered. SIN datos personales: ni email ni nada parecido."""
        now_ms = self._clock.now_ms()
        # idempotency_key derivada del user_id: si el alta se reintentara, la outbox lo
        # deduplica por su UNIQUE en vez de emitir el hecho dos veces.
        idempotency_key = f"{UserEventType.REGISTERED.value}:{user_id}"
        envelope = Envelope[UserRegisteredPayload](
            event_type=UserEventType.REGISTERED.value,
            event_schema_version=_USER_EVENT_SCHEMA_VERSION,
            source=self._source,
            idempotency_key=idempotency_key,
            # Ordering POR USUARIO: los hechos de una cuenta se ordenan entre si.
            stream_key=str(user_id),
            scope=Scope.USER,
            tenant_id=str(tenant_id),
            user_id=str(user_id),
            event_time=now_ms,
            processing_time=now_ms,
            correlation_id=uuid4().hex,
            payload=UserRegisteredPayload(
                user_id=str(user_id), tenant_id=str(tenant_id)
            ),
        )
        enqueue_event(
            session,
            OutboxEvent(
                event_id=envelope.event_id,
                idempotency_key=idempotency_key,
                stream_key=str(user_id),
                event_type=envelope.event_type,
                envelope=envelope.model_dump(mode="json"),
            ),
        )


def _to_utc(epoch_ms: int) -> datetime:
    """La base guarda timestamptz; el nucleo trabaja en epoch ms (ADR-007)."""
    return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)


class PostgresCredentialReader:
    """Lee la credencial de UN email por la ventanilla. Nunca enumera usuarios."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def credential_for_email(self, email: str) -> StoredCredential | None:
        with self._db.transaction() as session:
            row = session.fetchone(_CREDENTIAL_FOR_EMAIL, (normalize_email(email),))
        if row is None:
            return None
        return StoredCredential(
            user_id=UUID(str(row[0])),
            password_hash=str(row[1]),
            status=str(row[2]),
        )


class PostgresSessionStore:
    """Sesiones de refresh por la ventanilla. Guarda huellas, nunca tokens."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create_session(
        self, user_id: UUID, refresh_token_hash: str, expires_at_ms: int
    ) -> UUID:
        with self._db.transaction() as session:
            row = session.fetchone(
                _CREATE_SESSION,
                (str(user_id), refresh_token_hash, _to_utc(expires_at_ms)),
            )
        if row is None or row[0] is None:
            raise IdentityError("La ventanilla auth_create_session no devolvio sesion.")
        return UUID(str(row[0]))

    def rotate_session(
        self,
        refresh_token_hash: str,
        new_refresh_token_hash: str,
        expires_at_ms: int,
    ) -> RotationResult:
        with self._db.transaction() as session:
            row = session.fetchone(
                _ROTATE_SESSION,
                (
                    refresh_token_hash,
                    new_refresh_token_hash,
                    _to_utc(expires_at_ms),
                ),
            )
        if row is None or row[0] is None:
            raise IdentityError("La ventanilla auth_rotate_session no devolvio nada.")
        outcome = RotationOutcome(str(row[0]))
        user_id = None if row[1] is None else UUID(str(row[1]))
        session_id = None if row[2] is None else UUID(str(row[2]))
        return RotationResult(outcome=outcome, user_id=user_id, session_id=session_id)

    def revoke_family(self, refresh_token_hash: str) -> int:
        with self._db.transaction() as session:
            row = session.fetchone(_REVOKE_FAMILY, (refresh_token_hash,))
        if row is None or row[0] is None:
            raise IdentityError("La ventanilla auth_revoke_session_family fallo.")
        return int(str(row[0]))
