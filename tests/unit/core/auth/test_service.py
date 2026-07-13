"""Tests del AuthService con adaptadores FALSOS en memoria (P06b). Sin PostgreSQL.

El hasher (Argon2, puro) y el reloj simulado son los REALES; lo unico que se dobla es
lo que hablaria con la base. El reloj se ancla al instante REAL porque PyJWT juzga la
caducidad del access token contra el reloj del sistema, no contra el Clock inyectado.
"""

import time
from uuid import UUID, uuid4

import pytest

from ce_v5.core.auth import (
    AccessTokenService,
    Argon2PasswordHasher,
    AuthConfig,
    AuthService,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    PasswordHasher,
    RefreshTokenReuseError,
    RegisteredUser,
    RotationOutcome,
    RotationResult,
    StoredCredential,
    hash_refresh_token,
)
from ce_v5.core.auth.rate_limit import (
    Attempt,
    RateLimitConfig,
    RateLimitDecision,
)
from ce_v5.core.clock import SimulatedClock

_CONFIG = AuthConfig(jwt_secret="secreto-de-test-de-32-caracteres-o-mas")
_RATE_CONFIG = RateLimitConfig(digest_secret="secreto-de-huellas-de-32-caracteres")
_EMAIL = "ana@ejemplo.test"
_PASSWORD = "contrasena-falsa-de-test"
_IP = "203.0.113.10"  # TEST-NET-3: ficticia.


class _FakeLimiter:
    """Limitador en memoria que REGISTRA lo que se le pide.

    Permite siempre, salvo que se construya con allowed=False.
    """

    def __init__(self, allowed: bool = True) -> None:
        self._allowed = allowed
        self.comprobados: list[Attempt] = []
        self.fallos: list[Attempt] = []
        self.reseteados: list[Attempt] = []

    def check(self, attempt: Attempt) -> RateLimitDecision:
        self.comprobados.append(attempt)
        if self._allowed:
            return RateLimitDecision(allowed=True)
        return RateLimitDecision(
            allowed=False, retry_after_seconds=8, dimension="ip_account"
        )

    def register_failure(self, attempt: Attempt) -> None:
        self.fallos.append(attempt)

    def reset(self, attempt: Attempt) -> None:
        self.reseteados.append(attempt)


class _FakeAuditor:
    """Auditor en memoria: registra que hechos se auditan y cuales no."""

    def __init__(self) -> None:
        self.login_fallidos: list[tuple[str, str]] = []
        self.frenados: list[str] = []
        self.limitador_caido: list[str] = []
        self.csrf: list[str] = []
        self.altas: list[UUID] = []
        self.login_ok: list[UUID] = []
        self.rotados: list[UUID] = []
        self.reusados: list[UUID] = []
        self.salidas: list[tuple[UUID, int]] = []

    def login_failed(
        self, *, account_digest: str, ip_digest: str | None, reason: str
    ) -> None:
        self.login_fallidos.append((account_digest, reason))

    def rate_limited(
        self,
        *,
        action: str,
        account_digest: str | None,
        ip_digest: str | None,
        dimension: str,
    ) -> None:
        self.frenados.append(action)

    def limiter_unavailable(self, *, action: str) -> None:
        self.limitador_caido.append(action)

    def csrf_rejected(self, *, path: str) -> None:
        self.csrf.append(path)

    def registered(self, *, user_id: UUID) -> None:
        self.altas.append(user_id)

    def login_succeeded(self, *, user_id: UUID) -> None:
        self.login_ok.append(user_id)

    def refresh_rotated(self, *, user_id: UUID) -> None:
        self.rotados.append(user_id)

    def refresh_reused(self, *, user_id: UUID) -> None:
        self.reusados.append(user_id)

    def logged_out(self, *, user_id: UUID, sessions_revoked: int) -> None:
        self.salidas.append((user_id, sessions_revoked))


class _FakeCredentialReader:
    """CredentialReader en memoria: un email conocido, o ninguno."""

    def __init__(self, credential: StoredCredential | None) -> None:
        self._credential = credential
        self.emails_consultados: list[str] = []

    def credential_for_email(self, email: str) -> StoredCredential | None:
        self.emails_consultados.append(email)
        if self._credential is None:
            return None
        return self._credential


class _FakeSessionStore:
    """SessionStore en memoria que REGISTRA lo que se le pide."""

    def __init__(self, outcome: RotationResult | None = None) -> None:
        self._outcome = outcome
        self.creadas: list[tuple[UUID, str, int]] = []
        self.rotadas: list[tuple[str, str, int]] = []
        self.revocadas: list[str] = []
        self.revoke_family_devuelve = 2

    def create_session(
        self, user_id: UUID, refresh_token_hash: str, expires_at_ms: int
    ) -> UUID:
        self.creadas.append((user_id, refresh_token_hash, expires_at_ms))
        return uuid4()

    def rotate_session(
        self,
        refresh_token_hash: str,
        new_refresh_token_hash: str,
        expires_at_ms: int,
    ) -> RotationResult:
        self.rotadas.append((refresh_token_hash, new_refresh_token_hash, expires_at_ms))
        assert self._outcome is not None
        return self._outcome

    def revoke_family(self, refresh_token_hash: str) -> int:
        self.revocadas.append(refresh_token_hash)
        return self.revoke_family_devuelve


class _FakeRegistrar:
    """UserRegistrar en memoria: el alta atomica real se prueba en integracion."""

    def __init__(self) -> None:
        self.altas: list[tuple[str, str]] = []

    def register(self, email: str, password_hash: str) -> RegisteredUser:
        self.altas.append((email, password_hash))
        return RegisteredUser(user_id=uuid4(), tenant_id=uuid4())


class _SpyHasher:
    """Espia que envuelve al hasher REAL y cuenta las verificaciones."""

    def __init__(self, inner: PasswordHasher) -> None:
        self._inner = inner
        self.verificaciones = 0

    def hash(self, password: str) -> str:
        return self._inner.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        self.verificaciones += 1
        return self._inner.verify(password_hash, password)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _credential(status: str = "active") -> StoredCredential:
    return StoredCredential(
        user_id=uuid4(),
        password_hash=Argon2PasswordHasher().hash(_PASSWORD),
        status=status,
    )


def _service(
    credentials: _FakeCredentialReader,
    sessions: _FakeSessionStore,
    hasher: PasswordHasher | None = None,
    registrar: _FakeRegistrar | None = None,
    limiter: _FakeLimiter | None = None,
    auditor: _FakeAuditor | None = None,
) -> AuthService:
    clock = SimulatedClock(start_ms=_now_ms())
    return AuthService(
        credentials=credentials,
        registrar=_FakeRegistrar() if registrar is None else registrar,
        sessions=sessions,
        hasher=Argon2PasswordHasher() if hasher is None else hasher,
        tokens=AccessTokenService(_CONFIG, clock),
        clock=clock,
        config=_CONFIG,
        limiter=_FakeLimiter() if limiter is None else limiter,
        rate_config=_RATE_CONFIG,
        auditor=_FakeAuditor() if auditor is None else auditor,
    )


def test_login_correcto_entrega_access_y_refresh() -> None:
    credential = _credential()
    sessions = _FakeSessionStore()
    service = _service(_FakeCredentialReader(credential), sessions)

    issued = service.login(_EMAIL, _PASSWORD, _IP)

    assert issued.user_id == credential.user_id
    principal = AccessTokenService(_CONFIG, SimulatedClock(_now_ms())).verify(
        issued.access_token
    )
    assert principal.user_id == credential.user_id
    # Lo guardado es la HUELLA, no el token entregado.
    guardado = sessions.creadas[0][1]
    assert guardado != issued.refresh_token
    assert guardado == hash_refresh_token(issued.refresh_token)


def test_login_con_email_inexistente_falla() -> None:
    service = _service(_FakeCredentialReader(None), _FakeSessionStore())
    with pytest.raises(InvalidCredentialsError):
        service.login(_EMAIL, _PASSWORD, _IP)


def test_login_con_contrasena_incorrecta_falla() -> None:
    service = _service(_FakeCredentialReader(_credential()), _FakeSessionStore())
    with pytest.raises(InvalidCredentialsError):
        service.login(_EMAIL, "otra-cosa", _IP)


def test_login_de_usuario_no_activo_falla() -> None:
    service = _service(
        _FakeCredentialReader(_credential(status="disabled")), _FakeSessionStore()
    )
    with pytest.raises(InvalidCredentialsError):
        service.login(_EMAIL, _PASSWORD, _IP)


def test_los_tres_fallos_dicen_exactamente_lo_mismo() -> None:
    mensajes: list[str] = []
    for credentials, password in (
        (_FakeCredentialReader(None), _PASSWORD),
        (_FakeCredentialReader(_credential()), "otra-cosa"),
        (_FakeCredentialReader(_credential(status="disabled")), _PASSWORD),
    ):
        service = _service(credentials, _FakeSessionStore())
        with pytest.raises(InvalidCredentialsError) as excinfo:
            service.login(_EMAIL, password, _IP)
        mensajes.append(str(excinfo.value))
    # Un mensaje distinto por caso le diria al atacante que emails existen.
    assert len(set(mensajes)) == 1


def test_email_inexistente_verifica_contra_el_senuelo() -> None:
    spy = _SpyHasher(Argon2PasswordHasher())
    service = _service(_FakeCredentialReader(None), _FakeSessionStore(), hasher=spy)
    verificaciones_antes = spy.verificaciones

    with pytest.raises(InvalidCredentialsError):
        service.login(_EMAIL, _PASSWORD, _IP)

    # Se verifico algo (el senuelo): si no, la respuesta llegaria antes cuando el
    # email no existe y el tiempo delataria quien tiene cuenta.
    assert spy.verificaciones == verificaciones_antes + 1


def test_refresh_rotado_entrega_tokens_nuevos() -> None:
    user_id = uuid4()
    sessions = _FakeSessionStore(
        RotationResult(
            outcome=RotationOutcome.ROTATED, user_id=user_id, session_id=uuid4()
        )
    )
    service = _service(_FakeCredentialReader(None), sessions)

    issued = service.refresh("refresh-viejo", _IP)

    assert issued.user_id == user_id
    assert issued.refresh_token != "refresh-viejo"
    principal = AccessTokenService(_CONFIG, SimulatedClock(_now_ms())).verify(
        issued.access_token
    )
    assert principal.user_id == user_id
    # A la ventanilla viaja la HUELLA del token viejo, nunca el token.
    huella_enviada, huella_nueva, _ = sessions.rotadas[0]
    assert huella_enviada == hash_refresh_token("refresh-viejo")
    assert huella_nueva == hash_refresh_token(issued.refresh_token)


def test_refresh_reusado_revoca_la_familia() -> None:
    sessions = _FakeSessionStore(
        RotationResult(outcome=RotationOutcome.REUSE_DETECTED, user_id=uuid4())
    )
    service = _service(_FakeCredentialReader(None), sessions)
    with pytest.raises(RefreshTokenReuseError):
        service.refresh("refresh-gastado", _IP)


def test_refresh_invalido_falla() -> None:
    sessions = _FakeSessionStore(RotationResult(outcome=RotationOutcome.INVALID))
    service = _service(_FakeCredentialReader(None), sessions)
    with pytest.raises(InvalidRefreshTokenError):
        service.refresh("refresh-inventado", _IP)


def test_refresh_caducado_falla() -> None:
    sessions = _FakeSessionStore(
        RotationResult(outcome=RotationOutcome.EXPIRED, user_id=uuid4())
    )
    service = _service(_FakeCredentialReader(None), sessions)
    with pytest.raises(InvalidRefreshTokenError):
        service.refresh("refresh-caducado", _IP)


def test_register_hashea_la_contrasena_y_abre_sesion() -> None:
    registrar = _FakeRegistrar()
    sessions = _FakeSessionStore()
    service = _service(_FakeCredentialReader(None), sessions, registrar=registrar)

    issued = service.register("  Ana@Ejemplo.TEST ", _PASSWORD, _IP)

    email, password_hash = registrar.altas[0]
    assert email == "ana@ejemplo.test"  # normalizado por el nucleo.
    # A la base baja el HASH, jamas la contrasena en claro (CA-07 p.6).
    assert password_hash != _PASSWORD
    assert password_hash.startswith("$argon2id$")
    assert issued.access_token
    assert sessions.creadas[0][0] == issued.user_id


def test_un_login_correcto_resetea_el_limitador() -> None:
    limiter = _FakeLimiter()
    service = _service(
        _FakeCredentialReader(_credential()), _FakeSessionStore(), limiter=limiter
    )

    service.login(_EMAIL, _PASSWORD, _IP)

    assert limiter.comprobados[0].action == "login"
    assert limiter.fallos == []
    assert len(limiter.reseteados) == 1


def test_un_login_fallido_suma_al_limitador() -> None:
    limiter = _FakeLimiter()
    service = _service(
        _FakeCredentialReader(_credential()), _FakeSessionStore(), limiter=limiter
    )

    with pytest.raises(InvalidCredentialsError):
        service.login(_EMAIL, "otra-cosa", _IP)

    assert len(limiter.fallos) == 1
    assert limiter.reseteados == []


def test_al_limitador_no_llegan_ni_el_email_ni_la_ip() -> None:
    limiter = _FakeLimiter()
    service = _service(
        _FakeCredentialReader(_credential()), _FakeSessionStore(), limiter=limiter
    )

    service.login(_EMAIL, _PASSWORD, _IP)

    attempt = limiter.comprobados[0]
    assert attempt.account_digest is not None
    assert _EMAIL not in attempt.account_digest
    assert attempt.ip_digest is not None
    assert _IP not in attempt.ip_digest


def test_un_login_frenado_falla_igual_que_una_clave_equivocada() -> None:
    # Dictamen CSA a: decir "demasiados intentos" CONFIRMARIA que la cuenta existe.
    frenado = _service(
        _FakeCredentialReader(_credential()),
        _FakeSessionStore(),
        limiter=_FakeLimiter(allowed=False),
    )
    con_clave_mala = _service(_FakeCredentialReader(_credential()), _FakeSessionStore())

    with pytest.raises(InvalidCredentialsError) as frenado_exc:
        frenado.login(_EMAIL, _PASSWORD, _IP)
    with pytest.raises(InvalidCredentialsError) as clave_exc:
        con_clave_mala.login(_EMAIL, "otra-cosa", _IP)

    assert str(frenado_exc.value) == str(clave_exc.value)


def test_el_registro_cuenta_aunque_tenga_exito() -> None:
    # No existe un "registro incorrecto": el abuso ES el registro masivo, asi que un
    # alta con exito tambien suma al contador.
    limiter = _FakeLimiter()
    service = _service(
        _FakeCredentialReader(None), _FakeSessionStore(), limiter=limiter
    )

    service.register(_EMAIL, _PASSWORD, _IP)

    assert limiter.comprobados[0].action == "register"
    assert len(limiter.fallos) == 1


def test_el_refresh_se_limita_solo_por_ip() -> None:
    limiter = _FakeLimiter()
    sessions = _FakeSessionStore(
        RotationResult(outcome=RotationOutcome.ROTATED, user_id=uuid4())
    )
    service = _service(_FakeCredentialReader(None), sessions, limiter=limiter)

    service.refresh("refresh-viejo", _IP)

    attempt = limiter.comprobados[0]
    assert attempt.action == "refresh"
    # No hay cuenta conocida antes de validar la cookie: no se inventa.
    assert attempt.account_digest is None


def test_un_refresh_frenado_no_rota() -> None:
    service = _service(
        _FakeCredentialReader(None),
        _FakeSessionStore(),
        limiter=_FakeLimiter(allowed=False),
    )
    with pytest.raises(InvalidRefreshTokenError):
        service.refresh("refresh-viejo", _IP)


def test_logout_revoca_la_familia_con_la_huella() -> None:
    sessions = _FakeSessionStore()
    service = _service(_FakeCredentialReader(None), sessions)

    revocadas = service.logout("refresh-en-claro")

    assert revocadas == sessions.revoke_family_devuelve
    assert sessions.revocadas == [hash_refresh_token("refresh-en-claro")]
    assert "refresh-en-claro" not in sessions.revocadas


# --- Auditoria partida en dos (dictamen CSA N) --------------------------------------


def test_un_login_fallido_no_tiene_dueno_y_solo_va_al_log() -> None:
    auditor = _FakeAuditor()
    service = _service(
        _FakeCredentialReader(_credential()), _FakeSessionStore(), auditor=auditor
    )

    with pytest.raises(InvalidCredentialsError):
        service.login(_EMAIL, "otra-cosa", _IP)

    # Se registra el hecho PRE-identidad, con la HUELLA y el motivo tecnico...
    assert len(auditor.login_fallidos) == 1
    huella, motivo = auditor.login_fallidos[0]
    assert _EMAIL not in huella
    assert motivo == "bad_password"
    # ...y NO se escribe nada en la auditoria por sujeto: no hay dueno al que atarlo.
    assert auditor.login_ok == []


def test_un_login_correcto_se_audita_por_sujeto() -> None:
    auditor = _FakeAuditor()
    credential = _credential()
    service = _service(
        _FakeCredentialReader(credential), _FakeSessionStore(), auditor=auditor
    )

    service.login(_EMAIL, _PASSWORD, _IP)

    assert auditor.login_ok == [credential.user_id]
    assert auditor.login_fallidos == []


def test_un_login_frenado_se_registra_con_su_dimension() -> None:
    auditor = _FakeAuditor()
    service = _service(
        _FakeCredentialReader(_credential()),
        _FakeSessionStore(),
        limiter=_FakeLimiter(allowed=False),
        auditor=auditor,
    )

    with pytest.raises(InvalidCredentialsError):
        service.login(_EMAIL, _PASSWORD, _IP)

    assert auditor.frenados == ["login"]


def test_el_reuso_de_refresh_se_audita_por_sujeto() -> None:
    auditor = _FakeAuditor()
    user_id = uuid4()
    sessions = _FakeSessionStore(
        RotationResult(outcome=RotationOutcome.REUSE_DETECTED, user_id=user_id)
    )
    service = _service(_FakeCredentialReader(None), sessions, auditor=auditor)

    with pytest.raises(RefreshTokenReuseError):
        service.refresh("refresh-gastado", _IP)

    assert auditor.reusados == [user_id]


def test_el_logout_sin_principal_no_inventa_dueno() -> None:
    auditor = _FakeAuditor()
    service = _service(
        _FakeCredentialReader(None), _FakeSessionStore(), auditor=auditor
    )

    service.logout("refresh-en-claro")

    assert auditor.salidas == []


def test_el_logout_con_principal_se_audita() -> None:
    auditor = _FakeAuditor()
    sessions = _FakeSessionStore()
    service = _service(_FakeCredentialReader(None), sessions, auditor=auditor)
    user_id = uuid4()

    service.logout("refresh-en-claro", user_id)

    assert auditor.salidas == [(user_id, sessions.revoke_family_devuelve)]


def test_el_registro_se_audita_por_sujeto() -> None:
    # Una cuenta que nace y opera sin dejar rastro seria un agujero de auditoria. El
    # alta es atomica: hay usuario y tenant desde el primer instante.
    auditor = _FakeAuditor()
    service = _service(
        _FakeCredentialReader(None), _FakeSessionStore(), auditor=auditor
    )

    issued = service.register(_EMAIL, _PASSWORD, _IP)

    assert auditor.altas == [issued.user_id]
