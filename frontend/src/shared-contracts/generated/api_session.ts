// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type AccessToken = string
export type ExpiresInSeconds = number
export type TokenType = string
export type UserId = string

/**
 * Lo que se devuelve al entrar o renovar. SIN refresh token: va en cookie.
 */
export interface SessionResponse {
access_token: AccessToken
expires_in_seconds: ExpiresInSeconds
token_type?: TokenType
user_id: UserId
}
