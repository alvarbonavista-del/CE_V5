"""Check 7.3/7.4 (schemas): los JSON Schema generados coinciden con la
fuente Pydantic. Regenera en memoria desde contracts/source y compara con
los ficheros en contracts/schemas. Si la fuente cambio sin regenerar, o
si un artefacto se edito a mano, FALLA. No escribe nada.

Amplia el check base de P00 (que solo vigilaba que no hubiera ficheros no
generados) a regenerar-y-comparar (ADR-006), como estaba previsto.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = REPO_ROOT / "contracts" / "schemas"

sys.path.insert(0, str(REPO_ROOT / "tools"))

from gen_schemas import build_schemas, serialize  # noqa: E402


def _problems() -> list[str]:
    problems: list[str] = []
    expected = {name: serialize(s) for name, s in build_schemas().items()}
    for name, text in expected.items():
        path = SCHEMAS / name
        if not path.is_file():
            problems.append(f"falta el schema generado: {name}")
            continue
        if path.read_text(encoding="utf-8") != text:
            problems.append(f"desincronizado con la fuente: {name}")
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        if path.name not in expected:
            problems.append(f"schema no generado por la fuente: {path.name}")
    return problems


def main() -> int:
    problems = _problems()
    if problems:
        print("FAIL check 7.3/7.4 (schemas): regenerar desde la fuente.")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("OK check 7.3/7.4 (schemas): JSON Schema en sincronia con la fuente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
