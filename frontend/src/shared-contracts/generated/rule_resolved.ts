// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type CanonicalRuleHash = string
/**
 * Estados del ciclo de evaluacion (INFORME 6 sec 11.4).
 */
export type EvaluationLifecycleState = ("inactive" | "pending" | "firing" | "resolved")
/**
 * Por que una regla salio de FIRING a RESOLVED (CA-P08-05).
 * 
 * condition_false = el arbol (sin veto) dejo de cumplirse. veto_true = un veto se
 * activo y bloqueo. data_correction = una correccion de vela cambio el resultado (D5).
 * data_correction se USA en el Bloque 7, pero el enum se cierra aqui: es valor firmado
 * con uso conocido, no un "por si acaso" (regla 5.11 admite el valor con uso pactado).
 */
export type ResolvedReason = ("condition_false" | "veto_true" | "data_correction")
export type RuleId = string
export type TenantId = string

/**
 * rule.resolved: la regla salio del estado activo (flanco de bajada).
 * 
 * NO proyecta cierre especulativo (CA-P08-01 p.8): es el evento de desactivacion que
 * v4 no tenia. resolved_reason (aditivo, CA-P08-05) dice POR QUE se resolvio
 * (condition_false / veto_true / data_correction); el porque es observable, no se
 * infiere del estado.
 */
export interface RuleResolvedPayload {
canonical_rule_hash: CanonicalRuleHash
previous_state: EvaluationLifecycleState
resolved_reason: ResolvedReason
rule_id: RuleId
tenant_id: TenantId
}
