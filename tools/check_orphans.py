"""Check 7.6: huerfanos de Componentes (ADR-009; R3/R4).

Por cada carpeta de components/ falla si: no tiene manifest.json; el manifest
no declara entrypoint; o el entrypoint apunta a un modulo que no existe bajo
backend/src. No importa codigo de componentes: la existencia del entrypoint
se comprueba por ruta de fichero, no importando.
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


def _module_exists(src: Path, module_name: str) -> bool:
    pkg = src.joinpath(*module_name.split("."))
    return (pkg / "__init__.py").is_file() or pkg.with_suffix(".py").is_file()


def _problems(root: Path = COMPONENTS, src: Path = SRC) -> list[str]:
    problems: list[str] = []
    if not root.is_dir():
        return problems
    for folder in sorted(
        p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))
    ):
        manifest_file = folder / MANIFEST_FILENAME
        if not manifest_file.is_file():
            problems.append(f"{folder.name}: carpeta sin {MANIFEST_FILENAME}")
            continue
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
            manifest = validate_manifest(data)
        except (OSError, json.JSONDecodeError, ValidationError):
            continue
        entrypoint = manifest.entrypoint
        if entrypoint is None:
            problems.append(f"{folder.name}: manifest sin entrypoint")
            continue
        module_name = entrypoint.split(":", 1)[0]
        if not _module_exists(src, module_name):
            problems.append(f"{folder.name}: entrypoint inexistente ({module_name})")
    return problems


def main() -> int:
    problems = _problems()
    if problems:
        print("FAIL check 7.6 (huerfanos):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("OK check 7.6 (huerfanos): sin carpetas ni entrypoints huerfanos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
