// Generador de tipos TypeScript desde JSON Schema (ADR-006).
// Cadena de contratos (DOC_ESTRUCTURA 2.5): contracts/schemas (JSON
// Schema) -> frontend/src/shared-contracts/generated (TS). Solo genera;
// el check de regenerar-y-comparar (7.3/7.4) vive aparte. Salida
// determinista (sin prettier, banner fijo) para comparar byte a byte.
//
// Uso: node tools/gen_ts_types.mjs

import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { compile } from "json-schema-to-typescript";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, "..");
const SCHEMAS = join(REPO_ROOT, "contracts", "schemas");
export const OUT = join(REPO_ROOT, "frontend", "src", "shared-contracts", "generated");

const BANNER = "// Generado desde contracts/schemas. NO editar a mano (ADR-006).";
const OPTIONS = { bannerComment: BANNER, format: false, additionalProperties: false };

export async function buildTypes() {
  const files = readdirSync(SCHEMAS)
    .filter((name) => name.endsWith(".schema.json"))
    .sort();
  const out = [];
  for (const file of files) {
    const schema = JSON.parse(readFileSync(join(SCHEMAS, file), "utf8"));
    const typeName = schema.title ?? basename(file, ".schema.json");
    const content = await compile(schema, typeName, OPTIONS);
    out.push({ name: file.replace(/\.schema\.json$/, ".ts"), content });
  }
  return out;
}

async function main() {
  mkdirSync(OUT, { recursive: true });
  for (const { name, content } of await buildTypes()) {
    writeFileSync(join(OUT, name), content, { encoding: "utf8" });
    console.log(`generado frontend/src/shared-contracts/generated/${name}`);
  }
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
