# infra/compose - Entorno local (P02b)

PostgreSQL local para desarrollo y tests. Base de datos de juguete: NUNCA
datos reales (DOC_ENTREGABLES sec.5).

## Arrancar / parar
- Arrancar:  docker compose -f infra/compose/docker-compose.yml up -d
- Estado:    docker compose -f infra/compose/docker-compose.yml ps
- Parar y borrar datos:
             docker compose -f infra/compose/docker-compose.yml down -v

## Conexion (DSN)
Variable de entorno esperada por la app y los tests:
  CE_V5_DATABASE_URL=postgresql://ce_v5:ce_v5@localhost:5432/ce_v5

## Aplicar migraciones
  uv run python -m ce_v5.infra.db.migrations
