# ESTADO DE CONSTRUCCION - Crypto Engine V5

Archivo vivo de estado de proceso (sin logica). Lo mantiene Claude Code
en disco; Alvaro lo resube al knowledge cada vez que se cierra una pieza
o un hito (DOC_ENTREGABLES sec.8).

Ultima actualizacion: 2026-07-11 (cierre de pieza P05; hito M2 EN CURSO).

## Hito actual
M2 EN CURSO (sustrato de plataforma), abierto por P04. Piezas de M2: P04
(ENTREGADA), P05 (ENTREGADA), P06 y P06b (PENDIENTES). 2 de 4.

## Pieza actual
P05 - Tenancy shared-schema + RLS (ADR-011): ENTREGADA.
  Commit de pieza: 795deb3
  (795deb300b8b559b2d8314be32ab6a91f71b3d9f). Cierre de contexto en el commit
  "docs(contexto): cierre P05" (regla 5.9).
  Hash del commit de cierre de contexto: c9ee0950f20f241c24514bd3928e09197fee9473.
  (Un commit no puede contener su propio hash: por eso el hash del cierre se
  registra en el commit inmediato posterior. Regla 5.9 cumplida: cero cola en
  el arbol.)
  Modelo tenant + user_tenant_membership (pertenencia en capa aparte, costura
  de organizaciones abierta sin soportarlas); TenantContextResolver que
  resuelve el tenant en el backend desde la identidad autenticada y falla
  cerrado sin pertenencia valida; SET LOCAL transaccional del contexto; rol de
  aplicacion sin BYPASSRLS ni SUPERUSER y rol de migraciones fuera de runtime;
  RLS ENABLE + FORCE con policies atadas al contexto; isolation_scope declarado
  en las seis tablas (las cuatro de sistema allowlistadas); check 7.8
  materializado (tools/check_tenancy.py) y en el workflow; tests de aislamiento
  en CI; fuga cross-tenant demostrada como bloqueada en lectura, borrado y
  escritura. Doble revision Central + CSA conforme; firmado por Alvaro.
  CI: checks equivalentes al workflow validados en local; Actions pendiente
      por ausencia de remoto.

## Proxima pieza
P06 - PolicyEvaluator central + kill switch (ADR-012): resolucion de
  capacidades por jurisdiccion/plan/rol, fail-closed, kill switch jerarquico,
  enforcement en API. Tercera pieza del hito M2.

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
