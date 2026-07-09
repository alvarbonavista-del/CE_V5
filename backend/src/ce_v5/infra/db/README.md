# infra/db - Persistencia base (P02b)

Conexion a PostgreSQL, gestion de transacciones y, en tandas siguientes,
migraciones y tablas de outbox/inbox/audit (ADR-013, ADR-003).

## Que hay aqui
- config.py: resuelve el DSN de conexion desde el entorno
  (variable CE_V5_DATABASE_URL).
- ports.py: contratos Database y Session (Protocol) que abstraen el driver.
- psycopg_adapter.py: adapter con psycopg 3; unico fichero que conoce el
  driver concreto.

## Fuera de alcance en P02b
Sin RLS ni modelo tenant (P05); sin EventBus ni publisher (P03). Aqui solo
se entregan tablas tecnicas, transacciones y la escritura transaccional
atomica demostrada.
