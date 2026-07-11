# infra/db - Persistencia base (P02b)

Conexion a PostgreSQL, gestion de transacciones y, en tandas siguientes,
migraciones y tablas de outbox/inbox/audit (ADR-013, ADR-003).

## Que hay aqui
- config.py: resuelve los DSN de conexion desde el entorno. DOS roles/DSN
  distintos (ADR-011): rol de APLICACION (CE_V5_DATABASE_URL, se conecta en
  runtime, sin BYPASSRLS ni SUPERUSER) y rol de MIGRACIONES
  (CE_V5_MIGRATIONS_DATABASE_URL, dueno de las tablas, nunca corre en runtime).
- ports.py: contratos Database y Session (Protocol) que abstraen el driver.
- psycopg_adapter.py: adapter con psycopg 3; unico fichero que conoce el
  driver concreto.
- provision.py: da LOGIN al rol de aplicacion (ce_v5_app) con la contrasena
  del entorno; se ejecuta con el rol de migraciones y reafirma que el rol de
  aplicacion no puede saltarse el RLS (ADR-011).
- tenancy.py: sesion transaccional con contexto de tenant (SET LOCAL) y RLS
  del ADR-011. Resuelve el tenant en el backend desde la identidad
  autenticada, verifica que el rol conectado no puede saltarse el RLS, y da
  de alta el tenant 1:1 por usuario. Defensa en profundidad: sus repositorios
  filtran por tenant_id ademas de estar protegidos por RLS.

## Fuera de alcance en P02b
Sin RLS ni modelo tenant (P05); sin EventBus ni publisher (P03). Aqui solo
se entregan tablas tecnicas, transacciones y la escritura transaccional
atomica demostrada.
