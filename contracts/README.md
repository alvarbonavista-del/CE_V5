# contracts/ - Espina dorsal de contratos (ADR-003/004/005/006)

Proposito: definir UNA sola vez los contratos de eventos de Crypto Engine
V5 (el sobre canonico y la taxonomia de familias) como fuente Pydantic v2,
y generar desde ahi los artefactos que consumen backend y frontend. No
contiene logica que produzca ni consuma eventos.

## Zonas (flujo de una sola direccion)

- source/   Fuente de verdad en Pydantic v2. Lo unico que se edita a mano.
  - envelope/   Sobre canonico unico (ADR-003).
  - families/   Taxonomia base de familias dominio.accion (ADR-004).
  - time/       Reservada para el modelo temporal (P02, ADR-007).
- schemas/  JSON Schema generado (artefacto). NO editar a mano.

El tercer destino, los tipos TypeScript, se genera fuera de esta carpeta,
en frontend/src/shared-contracts/generated/ (artefacto). NO editar a mano.

## Regenerar los artefactos

  python tools/gen_schemas.py     # source Pydantic -> contracts/schemas
  node tools/gen_ts_types.mjs     # schemas -> frontend generated

Los artefactos se commitean junto a la fuente. El CI regenera y compara:
si divergen, falla (checks 7.3/7.4). La compatibilidad de evolucion la
valida el check 7.7.

## Reglas de evolucion

Ver VERSIONING.md (ADR-005): versionado dual, nunca renombrar/retipar,
anadir + deprecar, expand-and-contract, campos nuevos con default,
compatibilidad FULL.
