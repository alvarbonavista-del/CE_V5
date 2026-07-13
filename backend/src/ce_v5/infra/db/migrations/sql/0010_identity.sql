-- Migracion 0010: canon de identidad de plataforma (P06b, CA-07 opcion A).
-- Sucesora de 0009. Append-only: no se edita ninguna migracion aplicada.
--
-- POR QUE ESTAS TABLAS SON isolation_scope=system Y NO 'user':
-- El canon de identidad PRECEDE al tenant. El tenant se DERIVA de la pertenencia
-- user->tenant (ADR-011). Darles tenant_id invertiria la causalidad: haria falta un
-- tenant para autenticar al usuario, cuando el tenant se obtiene DESPUES de
-- autenticar y resolver pertenencia. El login busca por email cuando todavia NO hay
-- ni usuario ni tenant en la transaccion; una policy atada a app_current_tenant_id()
-- devolveria CERO FILAS y el login seria imposible.
--
-- COMO SE COMPENSA ESE 'system' (CA-07, opcion A): el rol de aplicacion NO recibe
-- NINGUN privilegio de tabla sobre ellas. Solo puede EJECUTAR cinco ventanillas
-- SECURITY DEFINER estrechas. Un bug o una inyeccion SQL en la API no puede volcar
-- la tabla de usuarios ni la de hashes: lo impide el MOTOR, no la buena conducta del
-- codigo. Mismo patron que la 0008 con el operador (CA-04).
--
-- FRONTERA DURA (CA-07 p.6): estas funciones son primitivas de CONTROL DE ACCESO,
-- de la misma categoria que las policies RLS (que ya son SQL). NO son una via para
-- mover logica de negocio al motor. La verificacion Argon2id ocurre en PYTHON: la
-- contrasena en claro JAMAS viaja a la base.

CREATE TABLE app_user (
    user_id     uuid PRIMARY KEY,
    email       text NOT NULL UNIQUE,
    status      text NOT NULL DEFAULT 'active',
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT app_user_status_valido CHECK (status IN ('active', 'disabled')),
    CONSTRAINT app_user_email_normalizado CHECK (email = lower(email))
);
COMMENT ON TABLE app_user IS
    'Canon de identidad de plataforma (P06b). isolation_scope=system: PRECEDE al tenant, que se deriva de la pertenencia (ADR-011).';

CREATE TABLE user_credential (
    user_id       uuid PRIMARY KEY REFERENCES app_user (user_id) ON DELETE CASCADE,
    password_hash text NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE user_credential IS
    'Hash Argon2id de la credencial (P06b). isolation_scope=system. La verificacion ocurre en Python; la clave en claro jamas llega a la base.';

CREATE TABLE user_session (
    session_id         uuid PRIMARY KEY,
    user_id            uuid NOT NULL REFERENCES app_user (user_id) ON DELETE CASCADE,
    family_id          uuid NOT NULL,
    refresh_token_hash text NOT NULL UNIQUE,
    issued_at          timestamptz NOT NULL DEFAULT now(),
    expires_at         timestamptz NOT NULL,
    revoked_at         timestamptz,
    rotated_to         uuid REFERENCES user_session (session_id) ON DELETE SET NULL
);
CREATE INDEX user_session_family_idx ON user_session (family_id);
CREATE INDEX user_session_user_idx ON user_session (user_id);
COMMENT ON TABLE user_session IS
    'Sesiones de refresh rotatorio (P06b, ADR-019). isolation_scope=system. Guarda el HASH del refresh token, nunca el token.';

-- Deuda de P05 PAGADA (REGISTRO_DECISIONES sec.12): la pertenencia ya no puede
-- apuntar a un usuario inexistente. La 0005 dejo user_id sin FK porque las cuentas
-- reales eran de P06b; ya existen.
ALTER TABLE user_tenant_membership
    ADD CONSTRAINT user_tenant_membership_user_fk
    FOREIGN KEY (user_id) REFERENCES app_user (user_id) ON DELETE CASCADE;

-- CERO privilegios directos (CA-07 p.3). REVOKE explicito y auditable, aunque nada
-- se haya concedido: queda ESCRITO, como en la 0008.
REVOKE ALL ON app_user, user_credential, user_session FROM PUBLIC;
REVOKE ALL ON app_user, user_credential, user_session FROM ce_v5_app;
REVOKE ALL ON app_user, user_credential, user_session FROM ce_v5_operator;

-- RLS ENABLE + FORCE (CA-07 p.2). No se elimina por ser system.
ALTER TABLE app_user ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_user FORCE ROW LEVEL SECURITY;
ALTER TABLE user_credential ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_credential FORCE ROW LEVEL SECURITY;
ALTER TABLE user_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_session FORCE ROW LEVEL SECURITY;

-- FORCE RLS + SECURITY DEFINER, RESUELTO EXPLICITAMENTE (CA-07 p.5):
-- la funcion corre con los privilegios de su PROPIETARIO, y FORCE somete tambien al
-- propietario. Por tanto el propietario necesita policies, y se le dan EXACTAMENTE
-- las operaciones que las cinco ventanillas necesitan, ni una mas: no hay policy de
-- UPDATE ni de DELETE sobre app_user ni sobre user_credential (nadie los edita ni los
-- borra en v5.0), y user_session solo admite UPDATE (revocar/rotar), nunca DELETE.
-- ce_v5_app NO aparece en ninguna policy: no lee tablas, solo ejecuta ventanillas.
-- PROHIBIDO usar BYPASSRLS como escape: el guardia de arranque de P05 (D6) sigue
-- negandose a operar si el rol de conexion pudiera saltarse el RLS.
DO $$
BEGIN
    EXECUTE format(
        'CREATE POLICY app_user_owner_read ON app_user FOR SELECT TO %I USING (true)',
        current_user);
    EXECUTE format(
        'CREATE POLICY app_user_owner_insert ON app_user FOR INSERT TO %I WITH CHECK (true)',
        current_user);
    EXECUTE format(
        'CREATE POLICY user_credential_owner_read ON user_credential FOR SELECT TO %I USING (true)',
        current_user);
    EXECUTE format(
        'CREATE POLICY user_credential_owner_insert ON user_credential FOR INSERT TO %I WITH CHECK (true)',
        current_user);
    EXECUTE format(
        'CREATE POLICY user_session_owner_read ON user_session FOR SELECT TO %I USING (true)',
        current_user);
    EXECUTE format(
        'CREATE POLICY user_session_owner_insert ON user_session FOR INSERT TO %I WITH CHECK (true)',
        current_user);
    EXECUTE format(
        'CREATE POLICY user_session_owner_update ON user_session FOR UPDATE TO %I USING (true) WITH CHECK (true)',
        current_user);
END
$$;

-- CONVENCION DE NOMBRES (CA-09 p.3), obligatoria y verificada por el check:
--   parametros p_, variables v_, columnas de salida out_, y toda columna cualificada
--   con su tabla en WHERE/SET. PostgreSQL convierte los nombres de salida en variables
--   de la funcion: si una se llamase igual que una columna (session_id, user_id...), la
--   sentencia seria AMBIGUA y reventaria en ejecucion. Lo destapo el test de INTEGRACION
--   contra PostgreSQL real; ningun test con mocks puede validar semantica de PL/pgSQL.
--   Con prefijos distintos, la colision es estructuralmente imposible.

-- LAS CINCO VENTANILLAS (CA-07 p.4).
-- Requisitos que cumplen TODAS y que el check nuevo verifica en cada build:
--   search_path FIJADO (el secuestro de search_path es EL exploit clasico de
--   SECURITY DEFINER); parametros TIPADOS; CERO SQL dinamico; sin comodines ni
--   patrones; retorno MINIMO; EXECUTE revocado a PUBLIC y concedido solo a ce_v5_app;
--   NINGUNA permite ENUMERAR usuarios.

CREATE FUNCTION auth_register_user(p_email text, p_password_hash text)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_user_id uuid := gen_random_uuid();
BEGIN
    INSERT INTO app_user (user_id, email) VALUES (v_user_id, p_email);
    INSERT INTO user_credential (user_id, password_hash) VALUES (v_user_id, p_password_hash);
    RETURN v_user_id;
END
$$;
COMMENT ON FUNCTION auth_register_user(text, text) IS
    'Ventanilla: alta de usuario + credencial. Recibe el HASH ya calculado en Python; la clave en claro no llega a la base. Falla si el email ya existe.';

CREATE FUNCTION auth_credential_for_email(p_email text)
RETURNS TABLE (out_user_id uuid, out_password_hash text, out_status text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT u.user_id, c.password_hash, u.status
    FROM app_user u
    JOIN user_credential c ON c.user_id = u.user_id
    WHERE u.email = p_email
    LIMIT 1
$$;
COMMENT ON FUNCTION auth_credential_for_email(text) IS
    'Ventanilla: UNA fila para UN email exacto (sin comodines, sin patrones). No enumera usuarios. La verificacion Argon2id ocurre en Python.';

CREATE FUNCTION auth_create_session(
    p_user_id uuid, p_refresh_token_hash text, p_expires_at timestamptz)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_session_id uuid := gen_random_uuid();
BEGIN
    INSERT INTO user_session (
        session_id, user_id, family_id, refresh_token_hash, expires_at)
    VALUES (v_session_id, p_user_id, v_session_id, p_refresh_token_hash, p_expires_at);
    RETURN v_session_id;
END
$$;
COMMENT ON FUNCTION auth_create_session(uuid, text, timestamptz) IS
    'Ventanilla: abre sesion guardando el HASH del refresh token. La primera sesion funda su familia (family_id = session_id).';

CREATE FUNCTION auth_rotate_session(
    p_refresh_token_hash text, p_new_refresh_token_hash text, p_expires_at timestamptz)
RETURNS TABLE (out_outcome text, out_user_id uuid, out_session_id uuid)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row user_session%ROWTYPE;
    v_new_id uuid;
BEGIN
    SELECT * INTO v_row FROM user_session
        WHERE user_session.refresh_token_hash = p_refresh_token_hash
        FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'invalid'::text, NULL::uuid, NULL::uuid;
        RETURN;
    END IF;
    IF v_row.revoked_at IS NOT NULL OR v_row.rotated_to IS NOT NULL THEN
        UPDATE user_session SET revoked_at = now()
            WHERE user_session.family_id = v_row.family_id
              AND user_session.revoked_at IS NULL;
        RETURN QUERY SELECT 'reuse_detected'::text, v_row.user_id, NULL::uuid;
        RETURN;
    END IF;
    IF v_row.expires_at <= now() THEN
        UPDATE user_session SET revoked_at = now()
            WHERE user_session.session_id = v_row.session_id;
        RETURN QUERY SELECT 'expired'::text, v_row.user_id, NULL::uuid;
        RETURN;
    END IF;
    v_new_id := gen_random_uuid();
    INSERT INTO user_session (
        session_id, user_id, family_id, refresh_token_hash, expires_at)
    VALUES (
        v_new_id, v_row.user_id, v_row.family_id, p_new_refresh_token_hash,
        p_expires_at);
    UPDATE user_session SET revoked_at = now(), rotated_to = v_new_id
        WHERE user_session.session_id = v_row.session_id;
    RETURN QUERY SELECT 'rotated'::text, v_row.user_id, v_new_id;
END
$$;
COMMENT ON FUNCTION auth_rotate_session(text, text, timestamptz) IS
    'Ventanilla: rotacion de refresh token. Un token ya gastado o revocado significa ROBO: se revoca la FAMILIA entera (reuse_detected). Atomica: buscar, invalidar y emitir ocurren en la misma transaccion.';

CREATE FUNCTION auth_revoke_session_family(p_refresh_token_hash text)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_family uuid;
    v_count integer;
BEGIN
    SELECT user_session.family_id INTO v_family FROM user_session
        WHERE user_session.refresh_token_hash = p_refresh_token_hash;
    IF NOT FOUND THEN
        RETURN 0;
    END IF;
    UPDATE user_session SET revoked_at = now()
        WHERE user_session.family_id = v_family
          AND user_session.revoked_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END
$$;
COMMENT ON FUNCTION auth_revoke_session_family(text) IS
    'Ventanilla: logout. Revoca la familia entera de sesiones a la que pertenece el token.';

REVOKE EXECUTE ON FUNCTION auth_register_user(text, text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION auth_credential_for_email(text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION auth_create_session(uuid, text, timestamptz) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION auth_rotate_session(text, text, timestamptz) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION auth_revoke_session_family(text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION auth_register_user(text, text) TO ce_v5_app;
GRANT EXECUTE ON FUNCTION auth_credential_for_email(text) TO ce_v5_app;
GRANT EXECUTE ON FUNCTION auth_create_session(uuid, text, timestamptz) TO ce_v5_app;
GRANT EXECUTE ON FUNCTION auth_rotate_session(text, text, timestamptz) TO ce_v5_app;
GRANT EXECUTE ON FUNCTION auth_revoke_session_family(text) TO ce_v5_app;
