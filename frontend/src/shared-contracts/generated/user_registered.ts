// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type TenantId = string
export type UserId = string

/**
 * Una cuenta nueva existe, con su tenant ya resuelto (alta atomica).
 * 
 * Ni email ni ningun otro dato personal: solo los identificadores.
 */
export interface UserRegisteredPayload {
tenant_id: TenantId
user_id: UserId
}
