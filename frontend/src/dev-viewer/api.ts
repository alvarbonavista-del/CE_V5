/// <reference lib="dom" />
// El visor corre en el navegador: usa fetch/URLSearchParams (globales del DOM). El
// tsconfig raiz no incluye la lib "dom" (el resto del frontend aun no la necesita), asi
// que se declara AQUI, localizada al dev-viewer, sin tocar la config compartida ni
// debilitar el type-check del resto. Ver README.

import type { KLineData } from "klinecharts";

import type { RawCandle, Selection } from "./types";

// El endpoint es relativo: el navegador habla con el dev server de Vite y este REENVIA
// /v1 -> la API local (proxy en vite.config.ts). Asi no hay CORS.
const CANDLES_PATH = "/v1/public/market/candles";

/**
 * El resultado de pedir velas, con los TRES estados que un grafico tiene que distinguir
 * (un lienzo en blanco jamas debe ser ambiguo):
 *  - "data": 200 con velas.
 *  - "empty": 200 con lista vacia -> el flujo existe pero no tiene historico.
 *  - "error": 422 (peticion mal formada), otro HTTP, o fallo de red/parseo.
 */
export type CandlesResult =
  | { readonly kind: "data"; readonly candles: readonly RawCandle[] }
  | { readonly kind: "empty" }
  | { readonly kind: "error"; readonly message: string };

/** Pide las `limit` velas mas recientes del flujo seleccionado y clasifica la respuesta. */
export async function fetchCandles(
  selection: Selection,
  limit: number,
): Promise<CandlesResult> {
  const query = new URLSearchParams({
    exchange: selection.exchange,
    symbol: selection.symbol,
    timeframe: selection.timeframe,
    limit: String(limit),
  });

  let response: Response;
  try {
    response = await fetch(`${CANDLES_PATH}?${query.toString()}`);
  } catch (cause) {
    return { kind: "error", message: `fallo de red: ${describe(cause)}` };
  }

  if (response.status === 422) {
    // El borde de la API rechaza symbol no canonico o timeframe fuera del vocabulario.
    return {
      kind: "error",
      message: "422: peticion mal formada (symbol o timeframe no validos)",
    };
  }
  if (!response.ok) {
    return { kind: "error", message: `HTTP ${response.status}` };
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch (cause) {
    return {
      kind: "error",
      message: `respuesta no es JSON: ${describe(cause)}`,
    };
  }
  if (!Array.isArray(body)) {
    return {
      kind: "error",
      message: "respuesta inesperada: no es una lista de velas",
    };
  }
  if (body.length === 0) {
    return { kind: "empty" };
  }
  return { kind: "data", candles: body as RawCandle[] };
}

/**
 * Convierte una vela del cable (precios STRING) en la vela del chart (KLineData, number).
 * Es el UNICO sitio donde el precio pasa de texto a number: el cable lo da como string
 * para no redondearlo, y KLineChart trabaja con number. Se ancla por open_time, que es el
 * event_time del ORIGEN (ADR-007), no un reloj nuestro.
 */
export function toKLineData(candle: RawCandle): KLineData {
  return {
    timestamp: candle.open_time,
    open: Number(candle.open),
    high: Number(candle.high),
    low: Number(candle.low),
    close: Number(candle.close),
    volume: Number(candle.volume),
  };
}

function describe(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}
