// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type TenantId = string
export type UserId = string

/**
 * Quien eres y en que tenant operas, SEGUN EL BACKEND.
 * 
 * El tenant no lo mando el cliente: lo resolvio el backend desde la pertenencia.
 */
export interface MeResponse {
tenant_id: TenantId
user_id: UserId
}
