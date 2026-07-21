// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type CanonicalRuleHash = string
/**
 * Estados del ciclo de evaluacion (INFORME 6 sec 11.4).
 */
export type EvaluationLifecycleState = ("inactive" | "pending" | "firing" | "resolved")
export type RuleId = string
export type TenantId = string

/**
 * rule.firing: la regla entro en estado activo/proyectable (flanco de subida).
 * 
 * Ancla causal: signal.* /alert.*.causation_id = event_id(rule.firing) (CA-P08-01 p.5),
 * en el envelope.
 */
export interface RuleFiringPayload {
canonical_rule_hash: CanonicalRuleHash
previous_state: EvaluationLifecycleState
rule_id: RuleId
tenant_id: TenantId
}
