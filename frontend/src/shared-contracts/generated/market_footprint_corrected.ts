// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type BarBuyVolume = (number | string)
export type BarDelta = (number | string)
export type BarSellVolume = (number | string)
export type BuyVolume = (number | string)
export type Delta = (number | string)
export type Price = (number | string)
export type SellVolume = (number | string)
export type Cells = FootprintCell[]
/**
 * Instante en UTC epoch milliseconds (int64). Formato canonico de tiempo en cable (ADR-007).
 */
export type CloseTime = number
export type CorrectionRevision = number
export type CorrectsIdempotencyKey = (string | null)
export type Exchange = string
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
export type TradeCount = number

/**
 * market.footprint_corrected: correccion append-only por trade tardio (ADR-007).
 * 
 * No muta el original (append-only): es un hecho NUEVO que referencia por
 * corrects_idempotency_key el footprint corregido y numera su revision.
 * correction_revision es OBLIGATORIO (>=1), como en CandleCorrectedPayload: estrecha
 * el int|None de la base a int requerido para que dos correcciones de la misma barra
 * no colisionen en la idempotency_key.
 */
export interface FootprintCorrectedPayload {
bar_buy_volume: BarBuyVolume
bar_delta: BarDelta
bar_sell_volume: BarSellVolume
cells: Cells
close_time: CloseTime
correction_revision: CorrectionRevision
corrects_idempotency_key?: CorrectsIdempotencyKey
exchange: Exchange
market_type: MarketType
maturity_state: MaturityState
open_time: OpenTime
symbol: Symbol
timeframe: Timeframe
trade_count: TradeCount
}
/**
 * Una celda del footprint: un nivel de precio dentro de una barra.
 * 
 * Volumen agresor comprador y vendedor a ese precio, y su delta (buy - sell). El
 * delta se lleva EXPLICITO y se valida contra buy-sell: un consumidor no tiene que
 * recalcularlo ni puede recibir uno incoherente.
 */
export interface FootprintCell {
buy_volume: BuyVolume
delta: Delta
price: Price
sell_volume: SellVolume
}
