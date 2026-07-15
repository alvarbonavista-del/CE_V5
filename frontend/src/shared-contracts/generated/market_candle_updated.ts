// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type Close = (number | string)
/**
 * Instante en UTC epoch milliseconds (int64). Formato canonico de tiempo en cable (ADR-007).
 */
export type CloseTime = number
export type CorrectionRevision = (number | null)
export type CorrectsIdempotencyKey = (string | null)
export type Exchange = string
export type High = (number | string)
export type Low = (number | string)
/**
 * Tipo de mercado. Solo SPOT en v5.0 (derivados: fuera de alcance).
 */
export type MarketType = "spot"
/**
 * Estado de madurez de un dato temporal (ADR-007).
 * 
 * Se modela en el schema de las familias que lo necesitan (market.*,
 * datasource.*), NO como campo universal del envelope.
 */
export type MaturityState = ("provisional" | "closed" | "correction" | "reemission")
export type Open = (number | string)
/**
 * Instante en UTC epoch milliseconds (int64). Formato canonico de tiempo en cable (ADR-007).
 */
export type OpenTime = number
export type Symbol = string
/**
 * Granularidad de vela. Conjunto CERRADO y ampliable (ADR-005).
 * 
 * Los seis son DIVISORES EXACTOS del dia. Gracias a eso vale una
 * invariante universal: el inicio de una vela SIEMPRE cae en una
 * frontera exacta de su intervalo contada desde epoch. Un timeframe
 * semanal o mensual romperia esa invariante (su frontera no es un
 * divisor del dia) y exigiria una regla de alineacion distinta: entra
 * cuando se necesite, no antes.
 */
export type Timeframe = ("1m" | "5m" | "15m" | "1h" | "4h" | "1d")
export type Volume = (number | string)

/**
 * market.candle_updated: vela PROVISIONAL, en formacion (ADR-007).
 * 
 * Vista viva: NO es historico canonico y puede perderse sin dano (la
 * autoridad es candle_closed). Las reglas y senales NUNCA se evaluan
 * sobre ella (invariante firmado en el dictamen P07-A).
 */
export interface CandleUpdatedPayload {
close: Close
close_time: CloseTime
correction_revision?: CorrectionRevision
corrects_idempotency_key?: CorrectsIdempotencyKey
exchange: Exchange
high: High
low: Low
market_type: MarketType
maturity_state: MaturityState
open: Open
open_time: OpenTime
symbol: Symbol
timeframe: Timeframe
volume: Volume
}
