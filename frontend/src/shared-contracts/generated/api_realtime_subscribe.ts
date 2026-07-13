// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type Checkpoint = (string | null)
export type Topic = string
export type Type = "subscribe"

/**
 * Suscripcion a un topic, opcionalmente desde un checkpoint.
 */
export interface RealtimeSubscribe {
checkpoint?: Checkpoint
topic: Topic
type: Type
}
