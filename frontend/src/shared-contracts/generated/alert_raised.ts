// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type AlertId = string
export type CanonicalRuleHash = string
export type Exchange = string
export type NotificationPolicyRef = (string | null)
export type RuleId = string
export type Symbol = string
export type TenantId = string

/**
 * alert.raised: aviso proyectado desde una regla en FIRING.
 */
export interface AlertRaisedPayload {
alert_id: AlertId
canonical_rule_hash: CanonicalRuleHash
exchange: Exchange
notification_policy_ref?: NotificationPolicyRef
rule_id: RuleId
symbol: Symbol
tenant_id: TenantId
}
