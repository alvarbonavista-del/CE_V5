import process from "node:process";
import { defineConfig } from "vite";

// SOLO servidor de DESARROLLO (nada de build de produccion: eso es M4/P13).
//
// El dev server sirve el visor y REENVIA /v1 -> la API local. Es un PROXY a proposito:
// asi el navegador solo habla con Vite (mismo origen) y Vite reenvia a la API, evitando
// CORS sin montar CORS en la API para una herramienta desechable.
//
// El destino por defecto es la API local, cuyo host/puerto los fija el backend con
// CE_V5_API_HOST/CE_V5_API_PORT (default 127.0.0.1:8000, ver api/__main__.py). Para
// apuntar a otra API local se exporta CE_V5_DEV_VIEWER_API=http://host:puerto antes de
// arrancar Vite.
const API_TARGET = process.env.CE_V5_DEV_VIEWER_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  // El index.html del visor vive en src/dev-viewer; ese es el root que Vite sirve.
  root: "src/dev-viewer",
  server: {
    proxy: {
      "/v1": { target: API_TARGET, changeOrigin: true },
    },
  },
});
