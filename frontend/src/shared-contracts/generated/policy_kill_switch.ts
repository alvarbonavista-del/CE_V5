// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type Actor = string
export type KillSwitchId = string
export type PolicyVersion = string
export type ReasonCode = string
/**
 * Ambitos de kill switch (ADR-012).
 * 
 * Un switch apaga TODO lo que cae dentro de su ambito; un ambito amplio
 * bloquea a los inferiores. La union de bloqueos activos manda.
 */
export type KillSwitchScope = ("global" | "exchange" | "connector" | "market_scope" | "capability" | "tenant" | "user")
export type TargetRef = (string | null)
export type TenantId = (string | null)
export type UserId = (string | null)

/**
 * Payload de policy.kill_switch_activated y _deactivated (ADR-012).
 * 
 * target_ref identifica el objetivo del ambito: el exchange, el connector,
 * el market_scope o la capability. En GLOBAL no hay objetivo. En TENANT y
 * USER el objetivo es el sujeto, no un target_ref.
 */
export interface KillSwitchPayload {
actor: Actor
kill_switch_id: KillSwitchId
policy_version: PolicyVersion
reason_code: ReasonCode
scope: KillSwitchScope
target_ref?: TargetRef
tenant_id?: TenantId
user_id?: UserId
}
