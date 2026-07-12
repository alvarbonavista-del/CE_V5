// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type PolicyVersion = string
/**
 * Motivo por el que el capability set de un sujeto deja de valer.
 */
export type InvalidationReason = ("role_changed" | "plan_changed" | "entitlement_changed" | "override_changed" | "jurisdiction_changed" | "kyc_changed" | "kill_switch_changed" | "policy_version_changed")
export type TenantId = string
export type UserId = (string | null)

/**
 * Payload de policy.subject_invalidated: el capability set de un sujeto
 * deja de ser valido y debe recomputarse (ADR-012: invalidacion por evento).
 * 
 * user_id ausente => se invalida el tenant entero.
 */
export interface SubjectInvalidatedPayload {
policy_version: PolicyVersion
reason: InvalidationReason
tenant_id: TenantId
user_id?: UserId
}
