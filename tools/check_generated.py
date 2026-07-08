"""Check 7.4 (base P00): los artefactos generados no se editan a mano.

En P00 aun no existe el generador (llega en P01 con contracts/source ->
JSON Schema -> TS). La unica invariante verificable ahora es que los
directorios de artefactos generados solo contienen ficheros producidos
por generacion. En el esqueleto estan vacios (solo .gitkeep); cualquier
fichero extra se considera edicion manual prohibida y hace fallar el
check. Cuando P01 anada el generador, este check se amplia a "regenerar
y comparar" (ADR-006); no se rebaja.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

GENERATED_DIRS: tuple[str, ...] = (
    "contracts/schemas",
    "frontend/src/shared-contracts/generated",
)

ALLOWED_NAMES: frozenset[str] = frozenset({".gitkeep"})


def find_manual_edits() -> list[Path]:
    offenders: list[Path] = []
    for rel in GENERATED_DIRS:
        directory = REPO_ROOT / rel
        if not directory.is_dir():
            continue
        for entry in sorted(directory.rglob("*")):
            if entry.is_file() and entry.name not in ALLOWED_NAMES:
                offenders.append(entry.relative_to(REPO_ROOT))
    return offenders


def main() -> int:
    offenders = find_manual_edits()
    if offenders:
        print("FAIL check 7.4: ficheros no generados en zona generada:")
        for path in offenders:
            print(f"  - {path.as_posix()}")
        return 1
    print("OK check 7.4: zonas generadas sin ediciones manuales.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
