// Generado desde contracts/schemas. NO editar a mano (ADR-006).

/**
 * Instante en UTC epoch milliseconds (int64). Formato canonico de tiempo en cable (ADR-007).
 */
export type EventTime = number
export type Exchange = string
export type FromSequence = number
/**
 * Tipo de mercado. Solo SPOT en v5.0 (derivados: fuera de alcance).
 */
export type MarketType = "spot"
export type Reason = string
export type Symbol = string
export type ToSequence = (number | null)

/**
 * market.orderbook_resynced: el libro perdio continuidad y se REINICIO (P07c).
 * 
 * Su PROPIO hecho publicado, no una correccion (no hay candle_corrected para el
 * libro): un resync dice que entre from_sequence (lo ultimo bueno) y to_sequence
 * (donde reanudo) hubo un hueco, y que el estado se reconstruyo desde una foto nueva.
 * to_sequence es None cuando el extremo es DESCONOCIDO (el motor no supo acotar donde
 * reanudo): fail-safe, un hueco abierto por ese lado.
 */
export interface OrderbookResyncedPayload {
event_time: EventTime
exchange: Exchange
from_sequence: FromSequence
market_type: MarketType
reason: Reason
symbol: Symbol
to_sequence?: ToSequence
}
