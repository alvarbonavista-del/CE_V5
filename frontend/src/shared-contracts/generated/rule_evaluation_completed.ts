// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type CanonicalRuleHash = string
/**
 * Estados del ciclo de evaluacion (INFORME 6 sec 11.4).
 */
export type EvaluationLifecycleState = ("inactive" | "pending" | "firing" | "resolved")
export type ReasonCode = string
export type Diagnostics = string[]
export type Matched = boolean
export type NodeId = string
export type NotEvaluableReason = (string | null)
export type Observed = (string | null)
/**
 * Resultado de un nodo. NOT_EVALUABLE (dato ausente) NO es FALSE (INFORME 6 9.1).
 * 
 * Conjunto cerrado: true / false / not_evaluable.
 */
export type NodeOutcome = ("true" | "false" | "not_evaluable")
export type NodeResults = NodeResult[]
export type VetoActive = boolean
/**
 * Resultado del bloque veto: CUATRO valores DISTINTOS (CA-P08-05).
 * 
 * NO_VETO (la regla no tiene veto), FALSE, TRUE, NOT_EVALUABLE. PROHIBIDO colapsar a
 * un veto_active:bool: colapsaria las filas 3 y 4 de la tabla de transiciones (V=TRUE
 * bloquea Y RESUELVE; V=NOT_EVALUABLE bloquea pero deja STALE, no resuelve). Es el
 * mismo tipo de hueco que costo P03: un bool que esconde dos casos que la FSM separa.
 */
export type VetoOutcome = ("no_veto" | "false" | "true" | "not_evaluable")
export type RuleId = string
export type TenantId = string

/**
 * rule.evaluation_completed: resultado granular asociado a una transicion.
 */
export interface RuleEvaluationCompletedPayload {
canonical_rule_hash: CanonicalRuleHash
new_state: EvaluationLifecycleState
previous_state: EvaluationLifecycleState
reason_code: ReasonCode
result: EvaluationResult
rule_id: RuleId
tenant_id: TenantId
}
/**
 * Resultado granular de una evaluacion (INFORME 6 sec 8.4; CA-P08-01, CA-P08-05).
 * 
 * rule_outcome = el resultado K3 del arbol de condiciones SIN veto, a nivel de regla
 * (TRUE/FALSE/NOT_EVALUABLE): es el eje R de la tabla de transiciones. matched es su
 * proyeccion booleana de conveniencia (= rule_outcome es TRUE).
 * 
 * veto_outcome = el resultado del veto (NO_VETO/FALSE/TRUE/NOT_EVALUABLE): es el eje V
 * y el campo AUTORITATIVO para la FSM. veto_active es una conveniencia DERIVADA
 * (= veto_outcome in {TRUE, NOT_EVALUABLE}); el runtime NO debe decidir sobre ella,
 * porque colapsa V=TRUE (bloquea y resuelve) con V=NOT_EVALUABLE (bloquea; stale).
 * 
 * matched_suppressed_by_veto (la regla habria disparado pero el veto lo impidio) NO es
 * un campo: es OBSERVABLE de forma derivada (rule_outcome es TRUE y veto_outcome es
 * TRUE). Un validador exige que matched y veto_active sean coherentes con sus ejes: no
 * puede construirse un resultado inconsistente. diagnostics = codigos de diagnostico
 * (ADR-016); el campo tipado SUPERA al diagnostico de texto para el runtime.
 */
export interface EvaluationResult {
diagnostics?: Diagnostics
matched: Matched
node_results: NodeResults
rule_outcome: NodeOutcome
veto_active: VetoActive
veto_outcome: VetoOutcome
}
/**
 * Resultado granular de un nodo, por su node_id estable.
 * 
 * observed = el VALOR CONCRETO usado, renderizado (None si NOT_EVALUABLE). Minimo
 * viable: string; la captura estructurada es mejora progresiva.
 * 
 * not_evaluable_reason = el MOTIVO cuando outcome es NOT_EVALUABLE (dato ausente,
 * historia insuficiente, o hijos indecidibles); None en otro caso. La distincion
 * FALSE vs NO-EVALUABLE (INFORME 6 sec 9.1) exige que el porque quede registrado, no
 * solo el que: sin el, un NOT_EVALUABLE es opaco al historial y al operador (ADR-016).
 * Campo OPCIONAL con default (evolucion aditiva ADR-005): rule.* es pre-consumidor.
 */
export interface NodeResult {
node_id: NodeId
not_evaluable_reason?: NotEvaluableReason
observed?: Observed
outcome: NodeOutcome
}
