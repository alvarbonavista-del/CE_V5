// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type Code = string
export type Message = string
export type Type = "error"

/**
 * Error del canal. Codigo estable; mensaje sin pistas.
 */
export interface RealtimeErrorMessage {
code: Code
message: Message
type: Type
}
