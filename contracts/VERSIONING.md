# Versionado y evolucion de contratos (ADR-005)

Reglas de evolucion de la espina dorsal de contratos de Crypto Engine V5.
Documento normativo: subordinado a ADR-005 (no lo reabre, lo aplica).
Cualquier cambio de contrato que no respete estas reglas es un cambio
arquitectonico y se eleva; no es un fix.

## 1. Versionado dual e independiente

Dos versiones evolucionan por separado (ADR-005):

- envelope_version: version de la estructura del propio sobre (envelope).
  Cambia solo si cambia el sobre canonico (ADR-003). Constante en codigo:
  ENVELOPE_VERSION (contracts/source/envelope/envelope.py).
- event_schema_version: version del payload de CADA tipo de evento, por
  tipo. Cambia de forma independiente del sobre y del resto de tipos.

Un cambio en un payload NO obliga a subir envelope_version, y al reves.

## 2. Reglas de cambio (envelope, payloads y entidades persistidas)

1. Nunca renombrar ni retipar un campo. Para "cambiar" un campo se ANADE
   uno nuevo y se DEPRECA el viejo (expand-and-contract / tolerant
   reader). Renombrar o cambiar el tipo de un campo existente esta
   prohibido.
2. Los campos nuevos entran con default: no rompen a productores ni
   consumidores que aun no los conocen.
3. Compatibilidad FULL (backward + forward) por defecto en produccion: un
   consumidor viejo lee datos nuevos y un consumidor nuevo lee datos
   viejos.
4. Los schemas son codigo en git, se revisan en PR y el CI bloquea los
   cambios incompatibles sin bump (check 7.7).
5. Entidades persistidas: llevan schema_version y migradores; se migra al
   cargar, nunca se rompe. (La persistencia es P02b; aqui solo se deja la
   regla.)

## 3. Fuente y artefactos (no se editan a mano)

Flujo unidireccional (DOC_ESTRUCTURA 2.5, ADR-006):

  contracts/source/  (Pydantic v2, UNICA fuente de verdad)
    -> contracts/schemas/  (JSON Schema, artefacto generado)
    -> frontend/src/shared-contracts/generated/  (tipos TS, generados)

Los artefactos (schemas y generated) NUNCA se editan a mano: se regeneran
desde la fuente. Para "corregir" un artefacto se corrige la FUENTE y se
regenera (politica de fixes, DOC_ENTREGABLES sec.6).

## 4. Como se hace cumplir (checks de CI)

- 7.3: regenerar desde la fuente y comparar; si schemas/ o generated/ no
  coinciden con la fuente, el build FALLA (ADR-006).
- 7.4: prohibicion de editar artefactos generados; integrada en 7.3, toda
  edicion manual se detecta al regenerar y comparar.
- 7.7: compatibilidad de evolucion; un cambio incompatible sin subir la
  version correspondiente FALLA (ADR-005).

## 5. Procedimiento para evolucionar un contrato

1. Editar SOLO la fuente en contracts/source.
2. Si el cambio seria incompatible (quitar/renombrar/retipar), NO se hace:
   se anade nuevo + se depreca viejo, y se sube la version que toque
   (envelope_version o el event_schema_version del tipo).
3. Regenerar: python tools/gen_schemas.py y node tools/gen_ts_types.mjs.
4. Commitear fuente + artefactos juntos; el CI (7.3/7.7) valida.
