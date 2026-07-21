// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type Close = (number | string)
/**
 * Instante en UTC epoch milliseconds (int64). Formato canonico de tiempo en cable (ADR-007).
 */
export type CloseTime = number
export type CorrectionRevision = number
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
 * market.candle_corrected: correccion de una vela ya cerrada (ADR-007).
 * 
 * No muta el original (append-only): es un hecho NUEVO que referencia por
 * corrects_idempotency_key la vela corregida (regla heredada de
 * MaturityAwarePayload) y numera su revision.
 * 
 * correction_revision es OBLIGATORIO (>=1) en este tipo (CA-P08-09): estrecha el
 * int|None de CandlePayload a un int requerido. Sin el, dos correcciones de la misma
 * vela colisionarian en la idempotency_key y la outbox (indice UNIQUE, P02b) se
 * tragaria la segunda EN SILENCIO. La obligatoriedad la impone ahora el TIPO del campo
 * -- no un validador aparte -- de modo que el schema generado lo refleja y ningun
 * consumidor la recibe como null. Correccion pre-consumidor (None nunca fue un evento
 * valido: ningun productor lo emitio ni ningun consumidor lo acepto).
 */
export interface CandleCorrectedPayload {
close: Close
close_time: CloseTime
correction_revision: CorrectionRevision
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
