# ESTADO DE CONSTRUCCION - Crypto Engine V5

Archivo vivo de estado de proceso (sin logica). Lo mantiene Claude Code
en disco; Alvaro lo resube al knowledge cada vez que se cierra una pieza
o un hito (DOC_ENTREGABLES sec.8).

Ultima actualizacion: 2026-07-10 (cierre de pieza P03 y hito M1).

## Hito actual
Ninguno en curso. M1 CERRADO (2026-07-10). Proximo hito: M2 (sustrato de
plataforma).

## Pieza actual
P03 - Sustrato EventBus (abstraccion + adapter Redis) (ADR-013): ENTREGADA.
  Commit de pieza: cb25b81
  (cb25b81e2948977dfd574d5c3aff137b8a11eed5). Cierre de contexto en el commit
  "docs(contexto): cierre P03 y M1" (regla 5.9).
  Abstraccion propia EventBus en core/bus (puerto + DTOs, sin broker ni
  contratos); adapter Redis Streams en infra/bus_redis (at-least-once, consumer
  groups, ordering por stream_key con particionado basico, DLQ observable,
  replay por offset con error si el offset fue purgado); OutboxPublisher (drena
  la outbox de P02b, valida el envelope contra el contrato antes de publicar,
  ADR-006) e InboxConsumer (dedup por inbox de P02b, ACK tras persistir el
  efecto) en infra/db; equivalente local en docker-compose (Redis 8.8);
  composition root de validacion en caliente. Reinicio de consumidor SIN perder
  ni duplicar demostrado en caliente (20 eventos, dedup 1). Doble revision
  Central + CSA conforme; firmado por Alvaro.
  CI: checks equivalentes al workflow validados en local; Actions pendiente por
      ausencia de remoto.

## Proxima pieza
P04 - Raiz Componente, manifest, discovery, lifecycle (ADR-001/008/009/010):
  sustrato de Componentes: raiz neutral, manifest tipado, discovery por carpeta
  que valida el manifest ANTES de cargar codigo, lifecycle observable. Abre el
  hito M2. Activa los checks 7.5 y 7.6.

## Piezas cerradas
- P00 - Esqueleto de repositorio + CI base: ENTREGADA (hito M0 CERRADO).
  Commits: d3f7ad6 -> 15f936d.
- P01 - Contratos base y envelope: ENTREGADA. Commit 17bb584.
- P02 - Modelo temporal y Clock: ENTREGADA. Commit 271d677.
- P02b - Persistencia base + migraciones + outbox: ENTREGADA.
  Commit ed3e788.
- P03 - Sustrato EventBus + adapter Redis: ENTREGADA. Commit cb25b81.
  Con P03 se cierra el hito M1 (4 de 4).

## Regla de trabajo (REGISTRO_DECISIONES sec.1)
Construccion en micro-pasos: el periferico nunca entrega la pieza entera
de golpe. Un paso, se explica, Alvaro ejecuta y pega salida, siguiente.

## Notas
- Guardarrailes vivos desde el commit 0. Sin deuda, sin codigo muerto,
  sin placeholders.
- Windows local requiere PYTHONUTF8=1 y PYTHONIOENCODING=utf-8.
- Docker Desktop (backend WSL2) requerido para el PostgreSQL local de
  pruebas y el check de integracion DB (ADR-013).
- Checks activos tras M1: 7.1, 7.2, 7.3, 7.4, 7.7, integracion DB (job
  backend-integration con PostgreSQL) e integracion del bus (mismo job, ahora
  tambien con Redis 8.8), mas lint/format/type (backend) y biome/tsc/depcruise
  (frontend); todos verdes en local. Inactivos hasta existir su objeto: 7.5/7.6
  (P04), 7.8 (primera tabla tenant/user, P05).
- Contracts: la fuente Pydantic se importa como paquete 'source'
  (source.envelope / source.families / source.time); raiz de importacion
  en contracts/ (revision de D3, REGISTRO_DECISIONES sec.7).
- Persistencia (P02b): variable CE_V5_DATABASE_URL con el DSN de
  PostgreSQL; migraciones via "python -m ce_v5.infra.db.migrations";
  entorno local en infra/compose/docker-compose.yml.
- Bus (P03): variable CE_V5_REDIS_URL con la URL de Redis; entorno local en
  infra/compose/docker-compose.yml (PostgreSQL + Redis). El contrato "source"
  se instala en runtime (pyproject: wheel packages incluye contracts/source).
