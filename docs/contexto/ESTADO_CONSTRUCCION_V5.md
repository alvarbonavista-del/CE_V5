# ESTADO DE CONSTRUCCION - Crypto Engine V5

Archivo vivo de estado de proceso (sin logica). Lo mantiene Claude Code
en disco; Alvaro lo resube al knowledge cada vez que se cierra una pieza
o un hito (DOC_ENTREGABLES sec.8).

Ultima actualizacion: 2026-07-10 (cierre de pieza P04; hito M2 EN CURSO).

## Hito actual
M2 EN CURSO (sustrato de plataforma), abierto por P04. Piezas de M2: P04
(ENTREGADA), P05, P06, P06b (PENDIENTES).

## Pieza actual
P04 - Raiz Componente, manifest, discovery, lifecycle (ADR-001/008/009/010):
  ENTREGADA.
  Commit de pieza: 866b434
  (866b434ec04dd3e04a9d43a9b3fa2f6f50dfd196). Cierre de contexto en el commit
  "docs(contexto): cierre P04" (regla 5.9).
  Raiz Componente como rol por contratos (Protocol de lifecycle + enganches,
  ADR-001); familia de eventos component.* en contracts/source (payload
  tipado; primer payload concreto del sistema); ComponentManifest tipado con
  validacion estatica (ADR-008); discovery por carpeta que valida el manifest
  ANTES de cargar codigo, con loader inyectado e import dinamico (ADR-009);
  supervisor de lifecycle que conduce ComponentInstances por la maquina de
  ADR-010 y emite component.* por el EventBus con envelope + Clock (emision
  fail-loud: emitir-antes-de-aplicar). Checks 7.5/7.6/7.9 materializados y en
  el workflow. Componente dummy 'sample' que demuestra "copiar carpeta +
  reiniciar" (CE-14) en caliente sobre el bus Redis. Doble revision Central +
  CSA conforme; firmado por Alvaro.
  CI: checks equivalentes al workflow validados en local; Actions pendiente
      por ausencia de remoto.

## Proxima pieza
P05 - Tenancy shared-schema + RLS (ADR-011): tenancy shared-schema + RLS
  fail-closed sobre la persistencia de P02b; toda tabla declara alcance
  (public_market/tenant/user/system); tests de aislamiento cross-tenant.
  Activa el check 7.8. Segunda pieza del hito M2.

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

## Regla de trabajo (REGISTRO_DECISIONES sec.1)
Construccion en micro-pasos: el periferico nunca entrega la pieza entera
de golpe. Un paso, se explica, Alvaro ejecuta y pega salida, siguiente.

## Notas
- Guardarrailes vivos desde el commit 0. Sin deuda, sin codigo muerto,
  sin placeholders.
- Windows local requiere PYTHONUTF8=1 y PYTHONIOENCODING=utf-8.
- Docker Desktop (backend WSL2) requerido para el PostgreSQL local de
  pruebas y el check de integracion DB/bus (ADR-013).
- Checks activos tras P04: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.9,
  integracion DB e integracion del bus (job backend-integration con
  PostgreSQL y Redis 8.8), mas lint/format/type (backend) y biome/tsc/
  depcruise (frontend); todos verdes en local. Inactivo hasta existir su
  objeto: 7.8 (primera tabla tenant/user, P05).
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
