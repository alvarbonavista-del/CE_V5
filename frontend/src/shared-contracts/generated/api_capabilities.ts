// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type Advisory = true
export type CapabilityId = string
export type Decision = string
export type KillSwitchId = (string | null)
export type ReasonCode = string
export type Sensitive = boolean
export type Decisions = CapabilityDecisionView[]
export type EvaluatedAt = number
export type PolicyVersion = (string | null)

/**
 * El capability set de CORTESIA (D9). Jamas una autorizacion.
 */
export interface CapabilitiesResponse {
advisory?: Advisory
decisions: Decisions
evaluated_at: EvaluatedAt
policy_version: PolicyVersion
}
/**
 * Una decision, tal como la ve el cliente. No autoriza: informa.
 */
export interface CapabilityDecisionView {
capability_id: CapabilityId
decision: Decision
kill_switch_id?: KillSwitchId
reason_code: ReasonCode
sensitive: Sensitive
}
