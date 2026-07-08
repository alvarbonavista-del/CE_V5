// Type-check del frontend (check de tipos TS).
// Politica de madurez (DOC_ESTRUCTURA 7.0): el check esta vivo desde el
// commit 0 y se ACTIVA cuando existe su objeto (el primer .ts/.tsx real).
// - Sin fuentes TS todavia: pasa informando (no hay nada que comprobar).
// - Con fuentes: corre tsc --noEmit en modo estricto y propaga su
//   resultado. No rebaja nada: en cuanto hay una fuente, tsc manda.
import { readdirSync, statSync, existsSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";

const SRC = "frontend/src";

function hasTsSources(dir) {
  if (!existsSync(dir)) return false;
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (hasTsSources(full)) return true;
    } else if (/\.tsx?$/.test(entry)) {
      return true;
    }
  }
  return false;
}

if (!hasTsSources(SRC)) {
  console.log("OK type-check frontend: sin fuentes TS todavia (se activa con P12a).");
  process.exit(0);
}

const requireCjs = createRequire(import.meta.url);
const tscBin = requireCjs.resolve("typescript/bin/tsc");
const res = spawnSync(process.execPath, [tscBin, "-p", "tsconfig.json", "--noEmit"], {
  stdio: "inherit",
});
process.exit(res.status ?? 1);
