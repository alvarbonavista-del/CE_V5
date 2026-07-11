# core/tenancy - Tenant efectivo y su resolucion (ADR-011)

El aislamiento entre tenants es responsabilidad del backend. El tenant
efectivo se DERIVA de la identidad autenticada y de la pertenencia
registrada; el cliente nunca lo impone ni lo influye. Sin pertenencia
valida la operacion FALLA CERRADA (`TenantResolutionError`), nunca degrada
a un tenant por defecto.

## Que hay aqui
- `context.py`: `TenantContext`, el par principal + tenant efectivo. Lo
  construye siempre el backend, jamas el cliente.
- `resolver.py`: `TenantContextResolver`. Recibe un principal ya autenticado
  y resuelve su tenant desde la pertenencia. En v5.0 el tenant coincide 1:1
  con el usuario (una pertenencia unica); ninguna o varias fallan cerrado.
- `ports.py`: el puerto `MembershipReader` que el nucleo necesita de la
  persistencia. La implementacion sobre PostgreSQL vive en
  `ce_v5.infra.db` (DOC_ESTRUCTURA sec.6).
- `errors.py`: `TenancyError` y `TenantResolutionError`.

## Fuera de alcance
La autenticacion real es P06b: aqui se recibe un principal ya autenticado.
El refuerzo en base de datos (RLS, `SET LOCAL`) vive en la migracion 0006 y
en el adapter de infra; el nucleo solo resuelve el contexto.
