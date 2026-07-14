# ESTADO DE CONSTRUCCION - Crypto Engine V5

Archivo vivo de estado de proceso (sin logica). Lo mantiene Claude Code
en disco; Alvaro lo resube al knowledge cada vez que se cierra una pieza
o un hito (DOC_ENTREGABLES sec.8).

Ultima actualizacion: 2026-07-14 (cierre de pieza P06b y del hito M2).

## Hito actual
M2 CERRADO (sustrato de plataforma). Piezas de M2: P04 (ENTREGADA), P05
(ENTREGADA), P06 (ENTREGADA), P06b (ENTREGADA). 4 de 4.
Proximo hito: M3 (datos, reglas y notificacion backend): P07, P08, P09a.

## Pieza actual
P06b - API/Auth/Realtime Gateway (ADR-002/006/011/012/013/019): ENTREGADA.
  CIERRA el hito M2.
  Commit de pieza: 6864c2a
  (6864c2af23dbaca1b04f41a0cfff3c0323247223).
  Commit final (PASO 0 del cierre, fuga de tenants huerfanos): 52b26db
  (52b26dba7e291611bfa6c050a6cba657fad477b9).
  ACTIONS VERDE 3/3 (backend, backend-integration, frontend) sobre el commit
  FINAL. 598 tests en verde con CERO SKIPS.
  Doble revision Central + CSA conforme; firmado por Alvaro.
  Resumen: puerta publica HTTP/WS; auth PROPIA (Argon2id, JWT corto que NO lleva
  el tenant dentro, refresh rotatorio con deteccion de reuso, jamas accesible al
  JS); la identidad sale SOLO de la sesion verificada y el tenant lo resuelve el
  BACKEND; canon de identidad con VENTANILLAS SECURITY DEFINER (el rol de
  aplicacion NO tiene privilegios de tabla sobre app_user/user_credential/
  user_session) y FK de P05 PAGADA; capabilities INFORMATIVAS; enforcement
  fail-closed ESTRICTO en el borde realtime con el PolicyGate de P06; linea base
  de seguridad completa (rate limiting, CSRF, CORS, cabeceras, limites de cuerpo,
  logs sin secretos, guardias de arranque); publica user.registered por outbox y
  consume policy.* por cursor privado; NO evalua reglas ni ejecuta ordenes (test
  de frontera con lista cerrada de rutas).
  Cierre de contexto en el commit "docs(contexto): cierre P06b y M2" (regla 5.9);
  su hash se registra en el commit inmediato posterior.
  Hash del commit de cierre de contexto: ee8647e0c213c38cb4dddb01a4b955e1b08577fe.
  (Un commit no puede contener su propio hash: por eso el hash del cierre se
  registra en el commit inmediato posterior. Regla 5.9 cumplida: cero cola en el
  arbol.)

## Proxima pieza
P07 - Ingesta de market data (hibrida), ADR-014. ABRE el hito M3.

## Piezas cerradas
- P00 - Esqueleto de repositorio + CI base: ENTREGADA (hito M0 CERRADO).
  Commits: d3f7ad6 -> 15f936d.
- P01 - Contratos base y envelope: ENTREGADA. Commit 17bb584.
- P02 - Modelo temporal y Clock: ENTREGADA. Commit 271d677.
- P02b - Persistencia base + migraciones + outbox: ENTREGADA.
  Commit ed3e788.
- P03 - Sustrato EventBus + adapter Redis: ENTREGADA. Commit cb25b81.
  Con P03 se cierra el hito M1 (4 de 4).
- P04 - Raiz Componente, manifest, discovery, lifecycle: ENTREGADA.
  Commit 866b434. Abre el hito M2.
- P05 - Tenancy shared-schema + RLS: ENTREGADA. Commit 795deb3.
- P06 - PolicyEvaluator central + kill switch: ENTREGADA. Commit 06cb51f.
- P06b - API/Auth/Realtime Gateway: ENTREGADA. Commit de pieza 6864c2a; commit
  final 52b26db. Con P06b se cierra el hito M2 (4 de 4).
Van 9 piezas cerradas de 19.

## Regla de trabajo (REGISTRO_DECISIONES sec.1)
Construccion en micro-pasos: el periferico nunca entrega la pieza entera
de golpe. Un paso, se explica, Alvaro ejecuta y pega salida, siguiente.

## Notas
- Guardarrailes vivos desde el commit 0. Sin deuda, sin codigo muerto,
  sin placeholders.
- Windows local requiere PYTHONUTF8=1 y PYTHONIOENCODING=utf-8.
- Docker Desktop (backend WSL2) requerido para el PostgreSQL local de
  pruebas y el check de integracion DB/bus (ADR-013).
- Checks activos tras P05: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8 (activado
  en P05), 7.9, integracion DB e integracion del bus (job backend-integration
  con PostgreSQL y Redis 8.8), mas lint/format/type (backend) y biome/tsc/
  depcruise (frontend); todos verdes en local. Ya no queda ningun check
  inactivo.
- Checks activos tras P06: 7.1-7.9 + check audit (tools/check_audit.py) + check
  de registro event_type->payload (tools/check_event_payload_registry.py) +
  integracion DB/bus. Todos verdes en local.
- Checks activos tras P06b: 7.1-7.9 + audit + IDENTITY (NUEVO, CA-07:
  tools/check_identity_access.py; exige que el rol de aplicacion NO tenga
  privilegios de tabla sobre las tablas de secretos y que el acceso vaya por
  ventanillas SECURITY DEFINER) + registro event_type->payload + integracion
  DB/bus/API. TODOS EN VERDE en local Y en Actions.
- 7.5/7.6/7.9 (P04): tools/check_manifests, tools/check_orphans,
  tools/check_component_docs; enganchados al job backend del workflow.
- Componentes: viven en backend/src/ce_v5/components/<nombre>/ (manifest.json
  + __init__ entrypoint + logica + tests + README); testpaths incluye esa
  carpeta. Componente de referencia: sample.
- Contracts: la fuente Pydantic se importa como paquete 'source'
  (source.envelope / source.families / source.time); raiz de importacion
  en contracts/. El vocabulario de lifecycle vive en
  source.families.component (P04 D1).
- Persistencia (P02b): variable CE_V5_DATABASE_URL con el DSN de
  PostgreSQL; migraciones via "python -m ce_v5.infra.db.migrations";
  entorno local en infra/compose/docker-compose.yml.
- Bus (P03): variable CE_V5_REDIS_URL con la URL de Redis; entorno local en
  infra/compose/docker-compose.yml (PostgreSQL + Redis). El contrato "source"
  se instala en runtime (pyproject: wheel packages incluye contracts/source).
- Tenancy (P05, ADR-011): CE_V5_DATABASE_URL = rol de APLICACION (ce_v5_app;
  sin SUPERUSER ni BYPASSRLS; sometido a RLS). CE_V5_MIGRATIONS_DATABASE_URL =
  rol de MIGRACIONES (dueno de las tablas; NO corre en runtime).
  CE_V5_APP_DB_PASSWORD = credencial del rol de aplicacion, que provisiona
  "python -m ce_v5.infra.db.provision" (se ejecuta con el rol de migraciones).
  Migraciones: "python -m ce_v5.infra.db.migrations" corre con el rol de
  migraciones. Check 7.8: "python tools/check_tenancy.py". Validacion en
  caliente de P05: "python tools/validate_p05_tenancy.py".
- Politica (P06, ADR-012/ADR-021): existe un TERCER DSN,
  CE_V5_OPERATOR_DATABASE_URL (rol ce_v5_operator: kill switch, publicacion de
  policy_version y su auditoria). JAMAS debe estar presente en un proceso de
  runtime: DbConfig.from_env lo detecta y ABORTA el arranque
  (OperatorDsnInRuntimeError). Solo la herramienta de operador
  ("python -m ce_v5.entrypoints.operator_cli") lo porta. Tambien
  CE_V5_OPERATOR_DB_PASSWORD para provisionar el rol. Utilidades de P06:
  tools/seed_p06_fake.py (escenario FALSO de demo),
  entrypoints/hot_validation_policy.py (validacion en caliente),
  tools/show_p06_audit.py (volcado de auditorias).
- Remoto: github.com/alvarbonavista-del/CE_V5 (PRIVADO). Desde T-01 existe
  copia del repositorio fuera del disco de Alvaro. Empujar es obligatorio al
  cerrar cada pieza.
- CI real: GitHub Actions ejecuta ci.yml en cada push. Tres jobs (backend,
  backend-integration con PostgreSQL y Redis, frontend). VERDE desde 64330c7.
  ACTIONS VERDE 3/3 sobre el commit final de P06b (52b26db). La formula vieja
  ("Actions pendiente por ausencia de remoto") quedo DEROGADA en T-01 (regla
  5.13).
- API y auth (P06b, ADR-002/019): la API es un PROCESO PROPIO y se arranca con
  "python -m ce_v5.entrypoints.api". Variables de entorno nuevas:
    CE_V5_JWT_SECRET ............. FIRMA los tokens de acceso. Minimo 32 chars.
                                   Si falta o es corto, la aplicacion NO ARRANCA.
    CE_V5_RATE_LIMIT_SECRET ...... calcula las HUELLAS de email e IP usadas como
                                   claves en Redis (el almacen nunca guarda emails
                                   ni IPs en claro). Minimo 32 chars.
    CE_V5_ENV .................... entorno de ejecucion (development/production).
    CE_V5_TRUSTED_PROXY_COUNT .... cuantos proxies PROPIOS hay delante. 0 = se
                                   IGNORA X-Forwarded-For (valor SEGURO).
    CE_V5_CORS_ALLOWED_ORIGINS ... origenes admitidos, separados por coma. VACIO
                                   por defecto; un "*" esta PROHIBIDO y la
                                   aplicacion NO ARRANCA con el.
    CE_V5_COOKIE_SECURE .......... cookies solo por HTTPS. En produccion, false
                                   impide el arranque.
    CE_V5_MAX_BODY_BYTES ......... tamano maximo de cuerpo, rechazado ANTES de
                                   leerlo.
    CE_V5_API_HOST / CE_V5_API_PORT  escucha de la API.
- Validacion en caliente de P06b: UN SOLO COMANDO,
  "python tools/run_p06b_hot_validation.py" (levanta lo que necesita y ejecuta el
  arnes completo).
- AVISO IMPORTANTE (regla 5.18): la suite solo corre ENTERA si
  CE_V5_OPERATOR_DATABASE_URL esta en el entorno. SIN esa variable se SALTAN 21
  tests de integracion en silencio. Un test que se salta en silencio es un test que
  no existe: el barrido de cierre debe reportar SIEMPRE el numero de skips, y CERO
  es el valor por defecto.
