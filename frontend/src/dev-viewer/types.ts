// Modelo LOCAL de la respuesta del endpoint publico de velas. ESCRITO A MANO A PROPOSITO.
//
// NO hay tipo generado para esto, y no es un olvido: los tipos de shared-contracts/
// generated se generan de los schemas de los EVENTOS del producto (contracts/source). El
// endpoint /v1/public/market/candles NO es un contrato de producto: es una VENTANA de
// LECTURA al historico, cuyo modelo de respuesta (MarketCandleRead) vive en el propio
// endpoint de la API, no en contracts. Por eso su forma se escribe aqui, a mano, y se
// mantiene sincronizada por inspeccion -- igual que el visor entero es una herramienta
// desechable, no el chart del producto.
//
// LOS PRECIOS VIAJAN COMO STRING (tal como salen al cable): el JSON solo tiene coma
// flotante binaria y serializar un Decimal como number redondearia el precio. El visor
// los parsea a number solo en el ULTIMO paso, al construir la vela del chart (api.ts).

/** Una vela tal como la devuelve GET /v1/public/market/candles (oldest -> newest). */
export interface RawCandle {
  /** open_time en ms: el instante que fija el ORIGEN del hecho (event_time, ADR-007). */
  open_time: number;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
}

/** El vocabulario CERRADO de timeframes que sirve la API (contrato Timeframe, ADR-005). */
export type Timeframe = "1m" | "5m" | "15m" | "1h" | "4h" | "1d";

export const TIMEFRAMES: readonly Timeframe[] = [
  "1m",
  "5m",
  "15m",
  "1h",
  "4h",
  "1d",
];

/** Lo que el usuario elige en la barra: que flujo pintar. */
export interface Selection {
  exchange: string;
  symbol: string;
  timeframe: Timeframe;
}
