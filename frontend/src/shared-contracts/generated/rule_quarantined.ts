// Generado desde contracts/schemas. NO editar a mano (ADR-006).

/**
 * Por que una regla quedo en CUARENTENA (operacional, robustez; CA-P08-04 D3).
 * 
 * ENUM UNICO (CA-P08-06 p.3): el MISMO enum sirve a la columna
 * rule_lifecycle_state.quarantine_reason y al payload del evento rule.quarantined; el
 * runtime (platform.rules.runtime) lo importa de aqui, no lo redefine. Sin strings
 * libres: una razon nueva se versiona por ADR-005 (anadir valor es compatible). Vive
 * en contracts porque un evento del bus lo referencia; StaleReason, sin evento, se
 * queda como enum operacional en el runtime.
 */
export type QuarantineReason = ("plan_not_recomputable" | "repeated_exceptions")
export type RuleId = string
export type TechnicalDetail = (string | null)
export type TenantId = string

/**
 * rule.quarantined: la regla paso a CUARENTENA (OPERACIONAL; CA-P08-06, D3).
 * 
 * NO es una transicion de evaluacion: no pasa por el validador de flanco de CA-P08-01
 * (sin previous_state), NO proyecta signal.* /alert.* ni sustituye firing/resolved.
 * Se emite SOLO en la transicion operacional is_quarantined false->true (el runtime lo
 * decide; no en bucle si ya estaba quarantined) y en la MISMA transaccion que la
 * escritura del estado (atomicidad, CA-P08-02).
 * 
 * Familia rule.* (NUNCA component.*): una Regla es DATO evaluado tenant-scoped, no un
 * Componente con manifest/discovery/lifecycle; component.quarantined (ADR-010) es para
 * instancias de Componente (CA-P08-06 p.6).
 * 
 * quarantine_reason usa el ENUM UNICO compartido con rule_lifecycle_state.quarantine_
 * reason (p.3). technical_detail es OPCIONAL, acotado por schema y sin secretos (p.4).
 * tenant_id viaja en el payload ademas del envelope; su coherencia con el tenant
 * autoritativo del envelope la exige el productor (p.2), server-authoritative.
 */
export interface RuleQuarantinedPayload {
quarantine_reason: QuarantineReason
rule_id: RuleId
technical_detail?: TechnicalDetail
tenant_id: TenantId
}
