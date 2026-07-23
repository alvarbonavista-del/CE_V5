# dev-viewer — visor de velas desechable (T-05)

**Qué es.** Una herramienta de desarrollo para MIRAR con los ojos que la ingesta produce
velas correctas: pinta el historico canónico que sirve `GET /v1/public/market/candles` y
lo refresca en vivo. Es **semilla de P13**, **NO el chart del producto**.

**Qué NO es (y no debe llegar a ser).** No tiene lógica de dominio, ni PWA, ni pulido, ni
build de producción, ni dibujo de indicadores. No importa `ui-core`, `app-core`,
`device-web` ni `device-ports`: solo `klinecharts` y sus propios módulos. Si esto empieza
a crecer hacia el producto, es que se está construyendo P13 en el sitio equivocado —
párese y hágase P13 como toca.

## Cómo se arranca

Tres piezas, en este orden:

1. **La API** (con historial de velas ya ingerido para el flujo que se quiera ver):
   ```
   python -m ce_v5.entrypoints.api
   ```
   Escucha en `127.0.0.1:8000` por defecto (`CE_V5_API_HOST` / `CE_V5_API_PORT`).

2. **El dev server de Vite** (desde `frontend/`):
   ```
   pnpm --filter @ce-v5/frontend dev
   ```
   Vite sirve el visor y **reenvía `/v1` → la API** (proxy en `frontend/vite.config.ts`),
   así el navegador solo habla con Vite y no hay CORS. Si la API está en otro sitio:
   ```
   CE_V5_DEV_VIEWER_API=http://host:puerto pnpm --filter @ce-v5/frontend dev
   ```

3. **El navegador** en la URL que imprime Vite (por defecto `http://localhost:5173`).
   La barra superior elige `exchange` / `symbol` / `timeframe` (defaults
   `binance` / `BTC-USDT` / `1m`) y "Cargar" recarga. La zona de estado dice siempre en
   qué está: **Cargando**, **Sin datos**, **Error** (con el mensaje) o **OK** — un lienzo
   en blanco nunca es ambiguo.

**Qué necesita para verse algo:** la API en marcha **y** velas ingeridas de ese flujo (si
no hay historial, el estado dice "Sin datos", que es distinto de un error).

## Cómo funciona (breve)

- `api.ts` pide las velas y distingue los tres estados (datos / sin datos / error). Los
  precios llegan como **string** (el cable no los redondea) y se parsean a `number` solo
  al construir la vela del chart.
- `main.ts` usa la API de datos de **KLineChart 10.0.0** (`setDataLoader`): `getBars` carga
  el histórico; `subscribeBar` refresca **por sondeo** cada ~1.5 s re-pidiendo las últimas
  velas y entregándolas por el camino de actualización (KLineChart fusiona por
  `open_time`). Una corrección de una vela **no-última** no la capta el streaming; el
  escape es recargar.
- `types.ts` modela a mano la respuesta del endpoint (es una ventana de lectura local a la
  API, no un contrato de producto: por eso no hay tipo generado).

## Hueco para DataSources (indicadores futuros)

`datasources.ts` fija la **forma** para enchufar indicadores (RSI en P08b; pivotphase,
divergencias y footprint en P08c) sin rehacer el visor: un registro de descriptores, cada
uno con nombre, dimensión (`overlay` sobre el precio / `panel` aparte), sus trazos y de
dónde saca los valores (Vía A: pegado a la vela; Vía B: `Map` por timestamp de un stream
`datasource.*`). Hoy está **vacío** a propósito.

⚠️ **Al registrar el primer indicador real (P08b), CRÍTICO 1 de I-01:** el `calc` de
KLineChart devuelve un **array alineado por posición**, no un objeto por timestamp (la doc
oficial miente). Exige una comprobación empírica de 5 minutos de ese contrato antes de
fiarse. Está documentado en `datasources.ts`.

## Nota de tipos DOM

El visor toca el DOM (`document`, `fetch`, `setInterval`). El `tsconfig.json` raíz no
incluye la lib `dom` (el resto del frontend aún no la necesita). En vez de debilitar la
config compartida, cada módulo del visor que toca el DOM declara
`/// <reference lib="dom" />` en su cabecera: localizado al dev-viewer y sin afectar al
type-check del resto.
