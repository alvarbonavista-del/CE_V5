"""Check 7.9: documentacion minima de Componentes (anti-R1).

Cada carpeta de components/ debe tener README.md con su proposito declarado.
Su ausencia hace FALLAR el build.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENTS = REPO_ROOT / "backend" / "src" / "ce_v5" / "components"


def _problems(root: Path = COMPONENTS) -> list[str]:
    problems: list[str] = []
    if not root.is_dir():
        return problems
    for folder in sorted(
        p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))
    ):
        if not (folder / "README.md").is_file():
            problems.append(f"{folder.name}: falta README.md")
    return problems


def main() -> int:
    problems = _problems()
    if problems:
        print("FAIL check 7.9 (docs de Componente):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("OK check 7.9 (docs de Componente): todos tienen README.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
