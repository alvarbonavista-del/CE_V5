// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type Email = string
export type Password = string

/**
 * Alta de una cuenta nueva.
 */
export interface RegisterRequest {
email: Email
password: Password
}
