// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type Actor = string
export type PolicyVersion = string
export type PreviousPolicyVersion = (string | null)
export type Reason = (string | null)

/**
 * Payload de policy.version_published: entra en vigor una policy_version.
 */
export interface PolicyVersionPublishedPayload {
actor: Actor
policy_version: PolicyVersion
previous_policy_version?: PreviousPolicyVersion
reason?: Reason
}
