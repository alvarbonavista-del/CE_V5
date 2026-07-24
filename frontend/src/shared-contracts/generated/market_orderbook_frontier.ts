// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type Price = (number | string)
export type Size = (number | string)
export type Asks = OrderbookLevel[]
export type Bids = OrderbookLevel[]
export type CadenceMs = number
/**
 * Instante en UTC epoch milliseconds (int64). Formato canonico de tiempo en cable (ADR-007).
 */
export type CloseTime = number
export type DepthK = number
export type Exchange = string
export type FormulaVersion = number
export type IsComplete = boolean
/**
 * Las dos variantes de un snapshot en UNA tabla (dictamen de Central).
 * 
 * FRONTIER se publica (as-of close_time, uno por barra); SAMPLE se persiste sin
 * publicar (muestra intra-ventana a cadencia). La misma forma de payload; el kind y el
 * sitio (outbox o no) los distingue.
 */
export type MarketOrderbookSnapshotKind = ("frontier" | "sample")
/**
 * Tipo de mercado. Solo SPOT en v5.0 (derivados: fuera de alcance).
 */
export type MarketType = "spot"
/**
 * Instante en UTC epoch milliseconds (int64). Formato canonico de tiempo en cable (ADR-007).
 */
export type OpenTime = number
export type SampleTime = (number | null)
export type Sequence = number
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

/**
 * Snapshot top-K del libro L2 (P07c). Cubre las dos variantes: frontier y sample.
 * 
 * El payload es UNO; el kind decide si se publica (frontier, por outbox) o solo se
 * persiste (sample). Por eso NO se registra por si mismo dos veces: el registro
 * (CA-06) mapea market.orderbook_frontier -> esta clase; el sample no es event_type.
 * 
 * is_complete es ORTOGONAL al kind: una muestra o un frontier pueden estar completos o
 * no segun hubiera un hueco/resync en su ventana (cond.3). DEFAULT False (fail-safe):
 * lo que no declara su completitud cuenta como incompleto.
 */
export interface OrderbookSnapshotPayload {
asks: Asks
bids: Bids
cadence_ms: CadenceMs
close_time: CloseTime
depth_k: DepthK
exchange: Exchange
formula_version: FormulaVersion
is_complete?: IsComplete
kind: MarketOrderbookSnapshotKind
market_type: MarketType
open_time: OpenTime
sample_time?: SampleTime
sequence: Sequence
symbol: Symbol
timeframe: Timeframe
}
/**
 * Un nivel del top-K persistido: precio y tamano agregado a ese precio.
 * 
 * Ya VALIDADO (ADR-006): precio y tamano finitos y positivos. Un nivel de tamano 0 no
 * es un nivel del libro (en el motor un tamano 0 BORRA el nivel; lo que se persiste
 * son niveles vivos). Decimal, nunca float: el libro es la base del precio de
 * ejecucion.
 */
export interface OrderbookLevel {
price: Price
size: Size
}
