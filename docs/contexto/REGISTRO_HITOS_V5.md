# REGISTRO DE HITOS - Crypto Engine V5

Archivo vivo (sin logica). Mantenido por Claude Code; Alvaro lo resube
al knowledge al cerrar cada pieza o hito (DOC_ENTREGABLES sec.8).

Ultima actualizacion: 2026-07-12 (T-01).

| Hito | Definicion breve (DOC_ROADMAP sec.4) | Piezas | Estado |
|------|--------------------------------------|--------|--------|
| M0 | Repo creado + CI de guardarrailes en verde (base estructural) | P00 | CERRADO |
| M1 | Un evento viaja de punta a punta con envelope, idempotencia y Clock sobre el bus externo, con outbox transaccional; reinicio sin perdida | P01, P02, P02b, P03 | CERRADO |
| M2 | Un Componente se descubre por carpeta, aislado por tenant/RLS, con capacidades por el gate fail-closed; API/auth/realtime en pie; kill switch en caliente | P04, P05, P06, P06b | EN CURSO |
| M3 | Una Rule dispara sobre datos reales y proyecta signal.*/alert.*; el router backend entrega por un canal no-PWA/mock (sin overlay, sin ejecucion) | P07, P08, P09a | PENDIENTE |
| M4 | PWA instalable con dashboard, chart y overlays de signal.* en movil real; push PWA; geo-blocking corta ejecucion, no visualizacion | P12a, P12b, P13, P09b | PENDIENTE |
| M5 | Ejecucion gateada: bloqueo UE/EEA/UK, orden manual BYOC, autotrade BYOC, reconciliacion | P10a, P10b, P11 | PENDIENTE |

## Detalle M0 (cerrado 2026-07-08)
- P00 ENTREGADA. Commits d3f7ad6 -> 15f936d.
- Guardarrailes bloqueantes de Pieza 0 en verde 11/11 (validacion en
  caliente local). CI: checks equivalentes al workflow validados en
  local; Actions pendiente por ausencia de remoto.
- Doble revision Central + CSA conforme; firmado por Alvaro.

## Detalle M1 (cerrado 2026-07-10)
- P01 - Contratos base y envelope: ENTREGADA (1 de 4). Commit 17bb584.
  Envelope + familias como fuente Pydantic v2; cadena source -> JSON
  Schema -> TS reproducible; checks 7.3/7.4/7.7 verdes en local. Doble
  revision Central + CSA conforme; firmado por Alvaro. CI: checks
  equivalentes al workflow validados en local; Actions pendiente por
  ausencia de remoto.
- P02 - Modelo temporal y Clock: ENTREGADA (2 de 4). Commit 271d677.
  Envelope retipado a EpochMillis (UTC epoch ms int64) via CA-01; modelo
  temporal (EpochMillis, enums de madurez/politicas, watermark basico);
  maturity_state y tipos de vela por familia; Clock inyectable (real +
  SimulatedClock). Checks verdes en local. Doble revision Central + CSA
  conforme; firmado por Alvaro. CI: checks equivalentes al workflow
  validados en local; Actions pendiente por ausencia de remoto.
- P02b - Persistencia base + migraciones + outbox transaccional (ADR-013):
  ENTREGADA. Persistencia sobre PostgreSQL (conexion, transacciones,
  migraciones append-only con checksum), tablas tecnicas outbox/inbox/
  audit_log con la identidad de evento de ADR-003, y primitiva de escritura
  transaccional atomica (negocio + outbox). Atomicidad DB-outbox demostrada
  en caliente; equivalente local en docker-compose. Sin RLS/tenancy (P05),
  sin EventBus (P03). Checks equivalentes al workflow verdes en local;
  doble revision Central + CSA conforme; firmado por Alvaro. Commit ed3e788.
- P03 - Sustrato EventBus (abstraccion + adapter Redis) (ADR-013): ENTREGADA
  (4 de 4). Commit cb25b81. Abstraccion propia en core/bus; adapter Redis
  Streams (at-least-once, consumer groups, ordering por stream_key, DLQ
  observable, replay por offset); OutboxPublisher (valida el contrato antes de
  publicar) e InboxConsumer (idempotencia via inbox, ACK tras persistir el
  efecto); equivalente local en docker-compose. Reinicio de consumidor SIN
  perder ni duplicar demostrado en caliente. Checks equivalentes al workflow
  verdes en local; doble revision Central + CSA conforme; firmado por Alvaro.

Cierre de hito M1 (2026-07-10): CERRADO. La espina dorsal tecnica queda
demostrada de punta a punta (un evento viaja con envelope, idempotencia y
Clock sobre el bus externo, con outbox transaccional; reinicio sin perdida).
Doble revision Central + CSA conforme; firmado por Alvaro. Proximo hito: M2
(sustrato de plataforma): P04, P05, P06, P06b.

## Detalle M2 (en curso desde 2026-07-10)
- P04 - Raiz Componente, manifest, discovery, lifecycle (ADR-001/008/009/010):
  ENTREGADA (1 de 4 de M2). Commit 866b434. Raiz Componente como rol por
  contratos; familia de eventos component.* en contracts/source; manifest
  tipado con validacion estatica; discovery por carpeta que valida el
  manifest ANTES de cargar codigo (loader inyectado, import dinamico);
  supervisor de lifecycle observable que emite component.* por el bus con
  envelope + Clock (emision fail-loud). "Copiar carpeta + reiniciar" (CE-14)
  demostrado en caliente sobre el bus Redis con el componente sample. Checks
  7.5/7.6/7.9 activados y en el workflow. Checks equivalentes al workflow
  verdes en local; doble revision Central + CSA conforme; firmado por Alvaro.
- P05 - Tenancy shared-schema + RLS (ADR-011): ENTREGADA (2 de 4 de M2).
  Commit 795deb3. Tenancy shared-schema con RLS fail-closed sobre la
  persistencia de P02b; tenant como abstraccion y user_tenant_membership como
  capa aparte; TenantContextResolver en el backend (el cliente nunca impone el
  tenant) que falla cerrado sin pertenencia valida; SET LOCAL transaccional;
  rol de aplicacion sin BYPASSRLS ni SUPERUSER y rol de migraciones fuera de
  runtime; toda tabla declara isolation_scope (las de sistema de P02b
  allowlistadas); defensa en profundidad con filtrado por tenant en la capa de
  aplicacion. Fuga cross-tenant demostrada como BLOQUEADA en lectura, borrado y
  escritura; sin pertenencia, falla cerrado. Check 7.8 activado
  (tools/check_tenancy.py) y demostrado que MUERDE (tabla tenant sin RLS ->
  FAIL; tabla sin tenant_id fuera de la allowlist -> FAIL). Tests de aislamiento
  en CI en cada build. Checks equivalentes al workflow verdes en local; doble
  revision Central + CSA conforme; firmado por Alvaro.
- P06 - PolicyEvaluator central + kill switch (ADR-012, ADR-021): ENTREGADA
  (3 de 4 de M2). Commit 06cb51f. Familia policy.* creada por ADR-021 (CA-02),
  con la frontera dura policy.* = CAUSA / component.* = CONSECUENCIA unidas por
  causation_id. Gate fail-closed: DENY > ALLOW en sensibles, entitlement
  explicito obligatorio, VPN/jurisdiccion desconocidas -> DENY, y "si no se
  puede auditar, no se permite". Kill switch jerarquico con transaccion atomica
  (estado + auditoria + outbox) y propagacion por evento. Rol de DB
  ce_v5_operator estrecho, con guardia de arranque que impide que un proceso de
  runtime porte su credencial. Checks nuevos: "audit" y registro
  event_type->payload, ambos demostrados MORDIENDO. VALIDACION EN CALIENTE
  CRITICA SUPERADA: una capability ALLOW pasa a DENY en ~1 segundo, en el MISMO
  proceso y sin reinicio, con TTL de cache de 60 s que descarta la caducidad
  como causa; y vuelve a ALLOW al soltar el switch. Dos defectos historicos
  corregidos (P03 y P05, ver REGISTRO_DECISIONES sec.13). Checks equivalentes al
  workflow verdes en local; doble revision Central + CSA conforme; firmado por
  Alvaro.
- P06b: PENDIENTE. Cierra M2.

## Nota T-01 (2026-07-12)
Desde T-01 el proyecto tiene remoto privado y GitHub Actions ejecutandose de
verdad. Actions VERDE en el commit 64330c7. La formula "Actions pendiente por
ausencia de remoto" que aparece en los cierres de P00 a P06 era CIERTA cuando
se escribio y se conserva sin tocar; queda DEROGADA hacia delante (regla
5.13): a partir de aqui, una pieza no se cierra sin Actions en verde.
