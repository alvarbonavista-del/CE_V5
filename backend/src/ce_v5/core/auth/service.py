"""Casos de uso de autenticacion: entrar, renovar y salir (P06b, ADR-019).

ROTACION CON DETECCION DE REUSO: cada refresh que se usa MUERE y nace otro. Si aparece
un refresh YA GASTADO, solo hay dos explicaciones y las dos son malas: o han robado el
token del usuario, o han robado el que ya se consumio. En ambos casos hay un ladron con
un token valido. Por eso se revoca la FAMILIA ENTERA de sesiones: el ladron y el usuario
legitimo quedan fuera, y el usuario vuelve a entrar con su contrasena. Es una molestia
para uno y el final del ataque para el otro.

CONTRA LA ENUMERACION DE USUARIOS: si el email no existe, se verifica igualmente contra
un hash SENUELO. Sin eso, la respuesta llegaria mucho antes cuando el email no esta
registrado, y un atacante podria descubrir QUIEN tiene cuenta midiendo el tiempo.
Responder "no" es correcto; responder "no" mas rapido es una filtracion.

La contrasena en claro no sale de este modulo: a la base solo va el hash (CA-07 p.6).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import NoReturn
from uuid import UUID

from ce_v5.core.auth.audit import AuthAuditor
from ce_v5.core.auth.config import AuthConfig
from ce_v5.core.auth.email import normalize_email
from ce_v5.core.auth.ports import (
    ACTIVE_STATUS,
    CredentialReader,
    PasswordHasher,
    RotationOutcome,
    SessionStore,
    UserRegistrar,
)
from ce_v5.core.auth.rate_limit import (
    ACTION_LOGIN,
    ACTION_REFRESH,
    ACTION_REGISTER,
    Attempt,
    AuthRateLimiter,
    RateLimitConfig,
    digest,
)
from ce_v5.core.auth.sessions import hash_refresh_token, new_refresh_token
from ce_v5.core.auth.tokens import AccessTokenService
from ce_v5.core.clock.protocol import Clock


class AuthError(RuntimeError):
    """Error de autenticacion."""


class InvalidCredentialsError(AuthError):
    """Email o contrasena incorrectos, o cuenta no activa.

    Un unico error para los tres casos A PROPOSITO: distinguirlos le diria a un
    atacante que emails existen.
    """


class InvalidRefreshTokenError(AuthError):
    """El refresh token no existe, esta revocado o ha caducado."""


class RefreshTokenReuseError(AuthError):
    """Se ha reusado un refresh token ya gastado: la familia queda revocada."""


class EmailAlreadyRegisteredError(AuthError):
    """Ya existe una cuenta con ese email."""


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """Lo que se entrega tras entrar o renovar."""

    user_id: UUID
    access_token: str
    refresh_token: str
    """En CLARO, y solo para meterlo en una cookie httpOnly: JAMAS al JavaScript."""


class AuthService:
    """Entrar, renovar y salir."""

    def __init__(
        self,
        credentials: CredentialReader,
        registrar: UserRegistrar,
        sessions: SessionStore,
        hasher: PasswordHasher,
        tokens: AccessTokenService,
        clock: Clock,
        config: AuthConfig,
        limiter: AuthRateLimiter,
        rate_config: RateLimitConfig,
        auditor: AuthAuditor,
    ) -> None:
        self._credentials = credentials
        self._registrar = registrar
        self._sessions = sessions
        self._hasher = hasher
        self._tokens = tokens
        self._clock = clock
        self._config = config
        self._limiter = limiter
        self._rate_config = rate_config
        self._auditor = auditor
        # Hash SENUELO: se calcula una vez y solo sirve para gastar el mismo tiempo
        # cuando el email no existe (ver docstring del modulo).
        self._decoy_hash = hasher.hash(secrets.token_urlsafe(16))

    def _attempt(
        self, action: str, client_ip: str | None, email: str | None = None
    ) -> Attempt:
        """El intento, YA anonimizado: al limitador no llegan ni el email ni la IP."""
        secret = self._rate_config.digest_secret
        return Attempt(
            action=action,
            ip_digest=None if client_ip is None else digest(client_ip, secret),
            account_digest=None if email is None else digest(email, secret),
        )

    def _refresh_expiry_ms(self) -> int:
        return self._clock.now_ms() + self._config.refresh_ttl_seconds * 1000

    def _issue(self, user_id: UUID) -> IssuedSession:
        refresh = new_refresh_token()
        self._sessions.create_session(user_id, refresh.hash, self._refresh_expiry_ms())
        return IssuedSession(
            user_id=user_id,
            access_token=self._tokens.issue(user_id),
            refresh_token=refresh.raw,
        )

    def register(
        self, email: str, password: str, client_ip: str | None
    ) -> IssuedSession:
        """Da de alta la cuenta y abre sesion. Atomico: usuario, tenant y pertenencia.

        La contrasena se hashea AQUI: a la base solo baja el hash (CA-07 p.6).

        AQUI CUENTA CADA INTENTO, no solo los fallidos: no existe un "registro
        incorrecto" que frenar. El abuso ES el registro masivo (crear mil cuentas), asi
        que un alta con EXITO tambien suma al contador.
        """
        normalized = normalize_email(email)
        attempt = self._attempt(ACTION_REGISTER, client_ip, normalized)
        decision = self._limiter.check(attempt)
        if not decision.allowed:
            self._auditor.rate_limited(
                action=ACTION_REGISTER,
                account_digest=attempt.account_digest,
                ip_digest=attempt.ip_digest,
                dimension=decision.dimension,
            )
            # Respuesta GENERICA (dictamen CSA a): decir "demasiados registros" no
            # confirma nada de nadie, pero mantener un unico error para toda la puerta
            # evita que la forma de la respuesta sirva para sondear el sistema.
            raise InvalidCredentialsError("Credenciales invalidas o no disponibles.")
        # Se cuenta el intento ANTES de crear nada: no existe un "registro incorrecto"
        # que frenar (el abuso ES el registro masivo), y asi un fallo del limitador
        # ocurre ANTES de cualquier efecto. Con el conteo DESPUES, un Redis caido tras
        # crear la cuenta convertiria un alta con exito en un 401: la cuenta existiria y
        # el usuario creeria que no.
        self._limiter.register_failure(attempt)
        registered = self._registrar.register(normalized, self._hasher.hash(password))
        issued = self._issue(registered.user_id)
        # El orden importa: primero la cuenta (transaccion atomica), luego la sesion, y
        # SOLO despues la traza. Si la auditoria fallara, el auditor lo registra en su
        # log y el alta NO se rompe: negar una cuenta ya creada porque falla una
        # escritura de traza dejaria al usuario con una cuenta que cree que no tiene.
        self._auditor.registered(user_id=issued.user_id)
        return issued

    def login(self, email: str, password: str, client_ip: str | None) -> IssuedSession:
        """Autentica y abre sesion.

        Falla igual, y TARDA igual, cuando el email no existe: si tardara menos, el
        tiempo de respuesta delataria que emails estan registrados.
        """
        normalized = normalize_email(email)
        attempt = self._attempt(ACTION_LOGIN, client_ip, normalized)
        decision = self._limiter.check(attempt)
        if not decision.allowed:
            self._auditor.rate_limited(
                action=ACTION_LOGIN,
                account_digest=attempt.account_digest,
                ip_digest=attempt.ip_digest,
                dimension=decision.dimension,
            )
            # MISMO error que una credencial invalida, a proposito (dictamen CSA a):
            # decir "demasiados intentos para esta cuenta" CONFIRMARIA que la cuenta
            # existe. La respuesta publica debe ser indistinguible entre usuario
            # inexistente, clave equivocada, cuenta frenada y limitador caido.
            raise InvalidCredentialsError("Email o contrasena incorrectos.")

        credential = self._credentials.credential_for_email(normalized)
        if credential is None:
            self._hasher.verify(self._decoy_hash, password)
            self._fallo_de_login(attempt, "unknown_account")
        if not self._hasher.verify(credential.password_hash, password):
            self._fallo_de_login(attempt, "bad_password")
        if credential.status != ACTIVE_STATUS:
            self._fallo_de_login(attempt, "inactive_account")

        self._limiter.reset(attempt)
        issued = self._issue(credential.user_id)
        self._auditor.login_succeeded(user_id=issued.user_id)
        return issued

    def _fallo_de_login(self, attempt: Attempt, reason: str) -> NoReturn:
        """Cuenta el fallo, lo registra con su motivo TECNICO y lanza el error generico.

        El motivo tecnico se queda en el LOG (hecho PRE-identidad: no hay dueno al que
        atarlo). Al cliente le llega siempre lo mismo, o la forma de la respuesta le
        diria quien tiene cuenta.
        """
        self._limiter.register_failure(attempt)
        if attempt.account_digest is not None:
            self._auditor.login_failed(
                account_digest=attempt.account_digest,
                ip_digest=attempt.ip_digest,
                reason=reason,
            )
        raise InvalidCredentialsError("Email o contrasena incorrectos.")

    def refresh(self, raw_refresh_token: str, client_ip: str | None) -> IssuedSession:
        """Rota el refresh token: el viejo muere y nace otro.

        Solo se limita por IP: el refresh viaja en una cookie y no sabemos de quien es
        hasta validarla; y no vamos a validarla ANTES de aplicar el limite, porque eso
        seria justo el trabajo que el limitador existe para evitar.
        """
        attempt = self._attempt(ACTION_REFRESH, client_ip)
        decision = self._limiter.check(attempt)
        if not decision.allowed:
            self._auditor.rate_limited(
                action=ACTION_REFRESH,
                account_digest=None,
                ip_digest=attempt.ip_digest,
                dimension=decision.dimension,
            )
            raise InvalidRefreshTokenError("Refresh token no valido.")

        new_refresh = new_refresh_token()
        result = self._sessions.rotate_session(
            hash_refresh_token(raw_refresh_token),
            new_refresh.hash,
            self._refresh_expiry_ms(),
        )
        if result.outcome is RotationOutcome.REUSE_DETECTED:
            self._limiter.register_failure(attempt)
            if result.user_id is not None:
                # El reuso SI tiene dueno (la ventanilla lo identifico): va a la
                # auditoria por sujeto.
                self._auditor.refresh_reused(user_id=result.user_id)
            raise RefreshTokenReuseError(
                "Refresh token reusado: la familia de sesiones queda revocada."
            )
        if result.outcome is not RotationOutcome.ROTATED or result.user_id is None:
            self._limiter.register_failure(attempt)
            raise InvalidRefreshTokenError("Refresh token no valido.")
        self._auditor.refresh_rotated(user_id=result.user_id)
        return IssuedSession(
            user_id=result.user_id,
            access_token=self._tokens.issue(result.user_id),
            refresh_token=new_refresh.raw,
        )

    def logout(self, raw_refresh_token: str, user_id: UUID | None = None) -> int:
        """Cierra la sesion revocando su familia. Devuelve cuantas sesiones revoco.

        user_id es OPCIONAL: el logout se autentica por COOKIE, y la cookie no dice de
        quien es hasta revocarla. Si el llamador conoce al principal, la traza tiene
        dueno y va a la auditoria por sujeto; si no, no se inventa un dueno.
        """
        revocadas = self._sessions.revoke_family(hash_refresh_token(raw_refresh_token))
        if user_id is not None:
            self._auditor.logged_out(user_id=user_id, sessions_revoked=revocadas)
        return revocadas
