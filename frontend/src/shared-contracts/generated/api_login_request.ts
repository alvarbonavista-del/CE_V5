// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type Email = string
export type Password = string

/**
 * Entrada al sistema con email y contrasena.
 */
export interface LoginRequest {
email: Email
password: Password
}
