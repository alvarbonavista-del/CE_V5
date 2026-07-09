# ESTADO DE CONSTRUCCION - Crypto Engine V5

Archivo vivo de estado de proceso (sin logica). Lo mantiene Claude Code
en disco; Alvaro lo resube al knowledge cada vez que se cierra una pieza
o un hito (DOC_ENTREGABLES sec.8).

Ultima actualizacion: 2026-07-09 (cierre de pieza P02b).

## Hito actual
M1 - Un evento viaja de punta a punta con envelope, idempotencia y Clock
     sobre el bus externo, con outbox transaccional; reinicio sin perdida:
     EN CURSO (3 de 4). Piezas: P01, P02, P02b ENTREGADAS; P03 PENDIENTE.

## Pieza actual
P02b - Persistencia base + migraciones + outbox transaccional (ADR-013):
  ENTREGADA. Commit de pieza: ed3e788
  (ed3e78833ce6789d9e435876dea8ae2c094421d4). Cierre de contexto en el
  commit "docs(contexto): cierre P02b" (regla 5.9).
  Conexion a PostgreSQL 18.4 (driver psycopg 3.3.4) y gestion de
  transacciones via puerto Session; runner de migraciones propio
  forward-only y append-only con checksum (tabla schema_migrations);
  tablas tecnicas outbox, inbox y audit_log (identidad de evento ADR-003);
  primitiva de escritura transaccional atomica (negocio + outbox);
  equivalente local en docker-compose (PostgreSQL 18.4). Atomicidad
  DB-outbox demostrada en caliente (commit deja negocio+outbox; rollback
  no deja ninguno). Sin RLS ni tenancy (P05); sin EventBus (P03).
  Doble revision Central + CSA conforme; firmado por Alvaro.
  CI: checks equivalentes al workflow validados en local; Actions
      pendiente por ausencia de remoto.

## Proxima pieza
P03 - Sustrato EventBus (abstraccion + adapter Redis) (ADR-013):
  bus externo con at-least-once, DLQ, consumer groups, idempotencia real
  y outbox/inbox transaccional SOBRE la persistencia de P02b; replay por
  offset. Cierra el hito M1.

## Piezas cerradas
- P00 - Esqueleto de repositorio + CI base: ENTREGADA (hito M0 CERRADO).
  Commits: d3f7ad6 -> 15f936d.
- P01 - Contratos base y envelope: ENTREGADA. Commit 17bb584.
- P02 - Modelo temporal y Clock: ENTREGADA. Commit 271d677.
- P02b - Persistencia base + migraciones + outbox: ENTREGADA.
  Commit ed3e788.

## Regla de trabajo (REGISTRO_DECISIONES sec.1)
Construccion en micro-pasos: el periferico nunca entrega la pieza entera
de golpe. Un paso, se explica, Alvaro ejecuta y pega salida, siguiente.

## Notas
- Guardarrailes vivos desde el commit 0. Sin deuda, sin codigo muerto,
  sin placeholders.
- Windows local requiere PYTHONUTF8=1 y PYTHONIOENCODING=utf-8.
- Docker Desktop (backend WSL2) requerido para el PostgreSQL local de
  pruebas y el check de integracion DB (ADR-013).
- Checks activos tras P02b: 7.1, 7.2, 7.3, 7.4, 7.7, integracion DB (job
  backend-integration; tests/integration contra PostgreSQL) (+ lint/
  format/type y biome/tsc/depcruise). Inactivos hasta existir su objeto:
  7.5/7.6 (P04), 7.8 (primera tabla tenant/user), 7.9 (primer Componente).
- Contracts: la fuente Pydantic se importa como paquete 'source'
  (source.envelope / source.families / source.time); raiz de importacion
  en contracts/ (revision de D3, REGISTRO_DECISIONES sec.7).
- Persistencia (P02b): variable CE_V5_DATABASE_URL con el DSN de
  PostgreSQL; migraciones via "python -m ce_v5.infra.db.migrations";
  entorno local en infra/compose/docker-compose.yml.
