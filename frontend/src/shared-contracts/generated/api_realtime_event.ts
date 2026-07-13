// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type Checkpoint = string
export type Topic = string
export type Type = "event"

/**
 * Un evento del bus. El envelope se entrega TAL CUAL (ADR-013).
 * 
 * El cliente consume el envelope canonico y no inventa campos: es el mismo
 * contrato que viaja por el bus, no una version recortada que habria que mantener en
 * dos sitios.
 */
export interface RealtimeEvent {
checkpoint: Checkpoint
envelope: Envelope
topic: Topic
type: Type
}
export interface Envelope {
[k: string]: unknown
}
