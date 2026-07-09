// Check 7.3/7.4 (TS): los tipos generados coinciden con la fuente.
// Regenera en memoria desde contracts/schemas y compara con los .ts en
// disco. Divergencia (fuente cambiada sin regenerar) o edicion manual de
// un artefacto -> FALLA. No escribe nada.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { OUT, buildTypes } from "./gen_ts_types.mjs";

const expected = await buildTypes();
const problems = [];
for (const { name, content } of expected) {
  let actual = null;
  try {
    actual = readFileSync(join(OUT, name), "utf8");
  } catch {
    problems.push(`falta el fichero generado: ${name}`);
    continue;
  }
  if (actual !== content) {
    problems.push(`desincronizado con la fuente: ${name}`);
  }
}
if (problems.length > 0) {
  console.error("FAIL check 7.3/7.4 (TS): regenerar desde la fuente.");
  for (const p of problems) {
    console.error(`  - ${p}`);
  }
  process.exit(1);
}
console.log("OK check 7.3/7.4 (TS): tipos generados en sincronia con la fuente.");
