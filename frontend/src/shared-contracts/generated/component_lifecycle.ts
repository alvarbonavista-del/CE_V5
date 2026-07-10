// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type ComponentId = string
export type ComponentInstanceId = string
export type ComponentVersion = string
export type ErrorCode = (string | null)
/**
 * Salud del componente, EJE APARTE del lifecycle (ADR-010).
 */
export type HealthStatus = ("healthy" | "degraded" | "unhealthy")
/**
 * Ambito de vida de una ComponentInstance (ADR-010).
 */
export type LifecycleScope = ("global" | "tenant" | "user")
/**
 * Estados de la maquina principal de ComponentInstance (ADR-010).
 */
export type LifecycleState = ("registered" | "initializing" | "initialized" | "starting" | "running" | "paused" | "stopping" | "stopped" | "unloaded" | "failed" | "quarantined")
/**
 * Disponibilidad para recibir trabajo, EJE APARTE (ADR-010).
 */
export type ReadinessStatus = ("ready" | "not_ready")
export type Reason = (string | null)
export type TenantId = (string | null)
export type UserId = (string | null)

/**
 * Payload comun de todo evento component.* (ADR-010).
 * 
 * Identifica la instancia y su transicion. previous_state es None solo en
 * el primer evento (component.registered). health_status y
 * readiness_status son EJES APARTE del lifecycle (ADR-010). error_code se
 * espera en FAILED/QUARANTINED.
 */
export interface ComponentLifecyclePayload {
component_id: ComponentId
component_instance_id: ComponentInstanceId
component_version: ComponentVersion
error_code?: ErrorCode
health_status: HealthStatus
lifecycle_scope: LifecycleScope
new_state: LifecycleState
previous_state?: (LifecycleState | null)
readiness_status: ReadinessStatus
reason?: Reason
tenant_id?: TenantId
user_id?: UserId
}
