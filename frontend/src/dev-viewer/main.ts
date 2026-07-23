/// <reference lib="dom" />
// El visor vive en el navegador: toca el DOM (document, setInterval, elementos HTML). El
// tsconfig raiz NO incluye la lib "dom" a proposito (el resto del frontend aun no la
// pide). Se declara AQUI, localizada al dev-viewer, sin tocar la config compartida ni
// debilitar el type-check del resto. Ver README (gotcha de tipos DOM).

import type { Chart, DataLoader, KLineData, Period } from "klinecharts";
import { init } from "klinecharts";

import { fetchCandles, toKLineData } from "./api";
import { type Selection, TIMEFRAMES, type Timeframe } from "./types";

// Cuantas velas se piden de historico (coincide con el default de la API) y cada cuanto
// se re-piden las ultimas en vivo. POLL_LIMIT=2: la vela en formacion + la recien cerrada.
const HISTORY_LIMIT = 500;
const POLL_MS = 1500;
const POLL_LIMIT = 2;

// Precision de display FIJA para la herramienta (BTC-USDT por defecto). Un simbolo muy
// distinto se veria con precision equivocada; para un visor desechable es aceptable.
const PRICE_PRECISION = 2;
const VOLUME_PRECISION = 4;

// timeframe canonico -> Period de KLineChart v10 ({type, span}).
const PERIODS: Record<Timeframe, Period> = {
  "1m": { type: "minute", span: 1 },
  "5m": { type: "minute", span: 5 },
  "15m": { type: "minute", span: 15 },
  "1h": { type: "hour", span: 1 },
  "4h": { type: "hour", span: 4 },
  "1d": { type: "day", span: 1 },
};

type StatusKind = "cargando" | "empty" | "error" | "normal";
const STATUS_LABEL: Record<StatusKind, string> = {
  cargando: "Cargando",
  empty: "Sin datos",
  error: "Error",
  normal: "OK",
};

// -- Referencias del DOM (fallan fuerte si el HTML no las trae) -----------------

function requireInput(id: string): HTMLInputElement {
  const el = document.getElementById(id);
  if (!(el instanceof HTMLInputElement)) {
    throw new Error(`dev-viewer: falta el input #${id}`);
  }
  return el;
}

function requireSelect(id: string): HTMLSelectElement {
  const el = document.getElementById(id);
  if (!(el instanceof HTMLSelectElement)) {
    throw new Error(`dev-viewer: falta el select #${id}`);
  }
  return el;
}

function requireButton(id: string): HTMLButtonElement {
  const el = document.getElementById(id);
  if (!(el instanceof HTMLButtonElement)) {
    throw new Error(`dev-viewer: falta el boton #${id}`);
  }
  return el;
}

function requireElement(id: string): HTMLElement {
  const el = document.getElementById(id);
  if (el === null) {
    throw new Error(`dev-viewer: falta el elemento #${id}`);
  }
  return el;
}

const exchangeInput = requireInput("exchange");
const symbolInput = requireInput("symbol");
const timeframeSelect = requireSelect("timeframe");
const loadButton = requireButton("load");
const statusElement = requireElement("status");
const chartContainer = requireElement("chart");

// -- Estado ---------------------------------------------------------------------

const created = init(chartContainer);
if (created === null) {
  throw new Error("dev-viewer: KLineChart no pudo inicializar #chart");
}
const chart: Chart = created;

let current: Selection = readSelection();
let pollTimer: number | null = null;

// -- Utilidades -----------------------------------------------------------------

function isTimeframe(value: string): value is Timeframe {
  return (TIMEFRAMES as readonly string[]).includes(value);
}

function readSelection(): Selection {
  const timeframe = timeframeSelect.value;
  return {
    exchange: exchangeInput.value.trim(),
    symbol: symbolInput.value.trim(),
    timeframe: isTimeframe(timeframe) ? timeframe : "1m",
  };
}

function setStatus(kind: StatusKind, detail: string): void {
  statusElement.dataset.state = kind;
  statusElement.textContent = detail
    ? `${STATUS_LABEL[kind]} — ${detail}`
    : STATUS_LABEL[kind];
}

function stopPolling(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

// -- Sondeo en vivo -------------------------------------------------------------

async function pollLatest(callback: (bar: KLineData) => void): Promise<void> {
  const result = await fetchCandles(current, POLL_LIMIT);
  if (result.kind === "data") {
    for (const candle of result.candles) {
      callback(toKLineData(candle));
    }
    setStatus("normal", "en vivo");
  } else if (result.kind === "error") {
    setStatus("error", `sondeo: ${result.message}`);
  }
  // "empty" en el sondeo: no se cambia el estado (el historico ya se pinto).
}

// -- Data loader de KLineChart v10 ----------------------------------------------

const dataLoader: DataLoader = {
  getBars: async (params) => {
    if (params.type !== "init") {
      // El visor NO pagina hacia atras (herramienta minima): solo la ventana reciente.
      // Cualquier peticion que no sea la inicial se responde vacia y sin "mas".
      params.callback([], false);
      return;
    }
    setStatus("cargando", "pidiendo historico");
    const result = await fetchCandles(current, HISTORY_LIMIT);
    if (result.kind === "data") {
      params.callback(result.candles.map(toKLineData), false);
      setStatus("normal", `${result.candles.length} velas`);
    } else if (result.kind === "empty") {
      params.callback([], false);
      setStatus("empty", "el flujo no tiene historico");
    } else {
      params.callback([], false);
      setStatus("error", result.message);
    }
  },
  subscribeBar: (params) => {
    // VIVO POR SONDEO: cada POLL_MS se re-piden las ultimas velas y se entregan por el
    // camino de ACTUALIZACION de KLineChart, que fusiona por timestamp (open_time): mismo
    // timestamp SOBREESCRIBE (absorbe la correccion de la ultima vela), timestamp mayor =
    // vela NUEVA.
    //
    // LIMITACION ACEPTADA: una correccion de una vela NO-ultima (timestamp menor que el
    // ultimo pintado) KLineChart la IGNORA por este camino. Para una herramienta
    // desechable es aceptable; el escape es recargar el historico (pulsar Cargar).
    stopPolling();
    pollTimer = window.setInterval(() => {
      void pollLatest(params.callback);
    }, POLL_MS);
  },
  unsubscribeBar: () => {
    stopPolling();
  },
};

// -- Aplicar la seleccion de la barra -------------------------------------------

function apply(): void {
  current = readSelection();
  if (current.symbol === "" || current.exchange === "") {
    setStatus("error", "exchange y symbol no pueden ir vacios");
    return;
  }
  chart.setSymbol({
    ticker: current.symbol,
    pricePrecision: PRICE_PRECISION,
    volumePrecision: VOLUME_PRECISION,
  });
  chart.setPeriod(PERIODS[current.timeframe]);
  // (Re)instalar el data loader dispara getBars("init"), que lee `current` -- incluido el
  // exchange, que el chart no conoce. Asi un cambio de CUALQUIER campo de la barra recarga.
  chart.setDataLoader(dataLoader);
}

loadButton.addEventListener("click", apply);
timeframeSelect.addEventListener("change", apply);

// Carga inicial con los defaults del HTML (binance / BTC-USDT / 1m).
apply();
