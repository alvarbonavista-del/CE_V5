// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type AccessToken = string
export type Type = "auth"

/**
 * Primer mensaje: la sesion. El token JAMAS en la URL.
 */
export interface RealtimeAuth {
access_token: AccessToken
type: Type
}
