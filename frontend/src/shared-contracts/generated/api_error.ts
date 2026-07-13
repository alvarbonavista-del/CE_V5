// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type Code = string
export type Message = string

/**
 * Error devuelto por la API.
 */
export interface ApiError {
code: Code
message: Message
}
