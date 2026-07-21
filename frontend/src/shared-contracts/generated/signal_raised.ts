// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type CanonicalRuleHash = string
export type Exchange = string
export type RuleId = string
export type SignalId = string
export type Symbol = string
export type TenantId = string

/**
 * signal.raised: senal de trading proyectada desde una regla en FIRING.
 */
export interface SignalRaisedPayload {
canonical_rule_hash: CanonicalRuleHash
exchange: Exchange
rule_id: RuleId
signal_id: SignalId
symbol: Symbol
tenant_id: TenantId
}
