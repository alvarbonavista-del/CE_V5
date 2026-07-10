"""Check 7.5: validacion de manifests de Componentes (ADR-008).

Por cada carpeta de components/ que tenga manifest.json, valida el manifest
con el modelo tipado (capa estatica). Un manifest mal formado hace FALLAR el
build. No importa codigo de componentes: solo lee y valida el manifest.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "backend" / "src"
COMPONENTS = SRC / "ce_v5" / "components"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(REPO_ROOT / "contracts"))

from pydantic import ValidationError  # noqa: E402

from ce_v5.core.discovery import MANIFEST_FILENAME  # noqa: E402
from ce_v5.core.manifest import validate_manifest  # noqa: E402


def _problems(root: Path = COMPONENTS) -> list[str]:
    problems: list[str] = []
    if not root.is_dir():
        return problems
    for folder in sorted(
        p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))
    ):
        manifest_file = folder / MANIFEST_FILENAME
        if not manifest_file.is_file():
            continue
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{folder.name}: manifest.json ilegible ({exc})")
            continue
        try:
            validate_manifest(data)
        except ValidationError as exc:
            problems.append(f"{folder.name}: manifest invalido ({exc})")
    return problems


def main() -> int:
    problems = _problems()
    if problems:
        print("FAIL check 7.5 (manifests):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("OK check 7.5 (manifests): todos los manifests validan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
