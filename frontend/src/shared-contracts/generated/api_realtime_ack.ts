// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type Checkpoint = (string | null)
export type Topic = string
export type Type = "ack"

/**
 * Confirmacion del servidor: suscrito, y desde donde.
 */
export interface RealtimeAck {
checkpoint: Checkpoint
topic: Topic
type: Type
}
