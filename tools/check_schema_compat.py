"""Check 7.7: compatibilidad de evolucion de schemas (ADR-005).

Compara cada JSON Schema actual con su version anterior en git (HEAD) y
aplica las reglas de evolucion FULL: prohibido quitar un campo, retiparlo,
volverlo requerido, o reducir un enum. Un cambio incompatible FALLA. Un
schema nuevo (sin version previa) es compatible por definicion: todo es
aditivo.

La logica de compatibilidad es una funcion pura (check_compatibility),
testeable sin git; el wrapper de CLI aporta la version previa desde git.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = REPO_ROOT / "contracts" / "schemas"


def _props(schema: dict[str, object]) -> dict[str, dict[str, object]]:
    props = schema.get("properties", {})
    return props if isinstance(props, dict) else {}


def _required(schema: dict[str, object]) -> set[str]:
    req = schema.get("required", [])
    return set(req) if isinstance(req, list) else set()


def _type_sig(prop: dict[str, object]) -> str:
    keys = ("type", "format", "$ref", "anyOf", "enum")
    return json.dumps({k: prop.get(k) for k in keys}, sort_keys=True)


def check_compatibility(old: dict[str, object], new: dict[str, object]) -> list[str]:
    """Incompatibilidades de old -> new segun ADR-005 (vacia si compatible)."""
    violations: list[str] = []
    old_props, new_props = _props(old), _props(new)
    old_req, new_req = _required(old), _required(new)

    for name, old_prop in old_props.items():
        if name not in new_props:
            violations.append(f"campo eliminado: {name}")
            continue
        if _type_sig(old_prop) != _type_sig(new_props[name]):
            violations.append(f"campo retipado: {name}")

    for name in sorted(new_req - old_req):
        violations.append(f"campo vuelto requerido: {name}")

    old_enum, new_enum = old.get("enum"), new.get("enum")
    if isinstance(old_enum, list) and isinstance(new_enum, list):
        for value in old_enum:
            if value not in new_enum:
                violations.append(f"valor de enum eliminado: {value!r}")

    return violations


def _git_show_head(rel_path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"HEAD:{rel_path}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else None


def main() -> int:
    problems: list[str] = []
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        previous = _git_show_head(rel)
        if previous is None:
            continue
        old = json.loads(previous)
        new = json.loads(path.read_text(encoding="utf-8"))
        problems += [f"{path.name}: {v}" for v in check_compatibility(old, new)]
    if problems:
        print("FAIL check 7.7: cambio de schema incompatible (ADR-005).")
        for p in problems:
            print(f"  - {p}")
        print("Regla: anadir + deprecar, nunca quitar/retipar/reducir enum.")
        return 1
    print("OK check 7.7: evolucion de schemas compatible.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
