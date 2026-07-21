"""Check: ningun codigo de EMISION construye Envelope[EventPayload] base (5.21).

Gate sin numero de norte. Hace cumplir el mecanismo del guardarrail 5.21 en su mitad
ESTATICA (la otra mitad es tests/unit/test_envelope_payload_roundtrip.py).

POR QUE EXISTE. El envelope es generico sobre su payload y pydantic serializa por el
tipo DECLARADO del campo, no por el tipo runtime del valor. Declarar el generico con la
base -- Envelope[EventPayload] -- fija el tipo de payload a EventPayload, que tiene CERO
campos, asi que model_dump() vuelca payload={} AUNQUE reciba un payload concreto real.
Ese fue el defecto B6.5 (rule.firing viajando vacio a la outbox), reincidencia de la
ENMIENDA HISTORICA 1 de P03. El recon B8.2 confirmo que NO hay un unico punto por el que
pasen todas las emisiones (la outbox valida al drenar, pero el camino directo-al-bus de
supervisor/ingestor no), asi que la barrera se pone en la FUENTE: prohibir el subscript
base en el codigo que emite. A nivel de fuente cubre los dos caminos y falla en CI
antes de ejecutar nada.

QUE ES Y QUE NO ES VIOLACION. Se analiza por AST (no regex: asi no matchea docstrings
ni comentarios que citen el patron, como este mismo modulo). Solo es violacion el
SUBSCRIPT concreto Envelope[EventPayload]. NO lo son:
- la DEFINICION del generico  class Envelope[PayloadT: EventPayload](...)  -- ahi
  EventPayload es la COTA de un type param (PEP 695), no un subscript;
- Envelope[PayloadT]  -- el parametro de tipo, no la base;
- Envelope[RuleFiringPayload] y demas concretos.

LISTA BLANCA. Dos usos LEGITIMOS confirmados por el recon, que separan a proposito la
validacion de ESTRUCTURA del sobre de la validacion del PAYLOAD contra su clase:
- backend/src/ce_v5/infra/db/outbox_publisher.py: valida la estructura del sobre con el
  payload sustituido por {} (el payload se valida aparte contra su clase concreta);
- tools/gen_schemas.py: genera el JSON Schema del sobre base (no emite eventos).
El arbol tests/ NO se analiza: un test puede usar la base para probar sus propias reglas
(p.ej. las reglas de scope del envelope), y eso no es codigo de emision.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Raices de codigo de EMISION que se analizan. tests/ queda fuera a proposito.
_SCAN_ROOTS = (
    REPO_ROOT / "backend" / "src",
    REPO_ROOT / "contracts" / "source",
)

# Usos LEGITIMOS de la base, como rutas repo-relativas POSIX. gen_schemas vive fuera de
# las raices analizadas (tools/), pero se lista para dejar constancia del unico otro uso
# legitimo confirmado por el recon; si un dia se anadiera tools/ a las raices, seguiria
# exento.
ALLOWLIST: frozenset[str] = frozenset(
    {
        "backend/src/ce_v5/infra/db/outbox_publisher.py",
        "tools/gen_schemas.py",
    }
)

_ENVELOPE_NAME = "Envelope"
_BASE_PAYLOAD_NAME = "EventPayload"


def _is_envelope_ref(node: ast.expr) -> bool:
    """El objeto subscrito es Envelope (importado directo o por atributo)."""
    if isinstance(node, ast.Name):
        return node.id == _ENVELOPE_NAME
    if isinstance(node, ast.Attribute):
        return node.attr == _ENVELOPE_NAME
    return False


def _is_base_payload(node: ast.expr) -> bool:
    """El indice es EventPayload base (Name o Attribute .EventPayload)."""
    if isinstance(node, ast.Name):
        return node.id == _BASE_PAYLOAD_NAME
    if isinstance(node, ast.Attribute):
        return node.attr == _BASE_PAYLOAD_NAME
    return False


def find_base_subscripts(source: str) -> list[int]:
    """Lineas (1-indexadas) donde aparece el subscript Envelope[EventPayload].

    Solo el subscript concreto con la base. La cota de un type param
    (class Envelope[PayloadT: EventPayload]) no es un Subscript, asi que no entra aqui.
    """
    tree = ast.parse(source)
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and _is_envelope_ref(node.value)
            and _is_base_payload(node.slice)
        ):
            lines.append(node.lineno)
    return lines


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def check_files(paths: Iterable[Path]) -> list[str]:
    """Logica pura: devuelve las violaciones (vacia = verde)."""
    problems: list[str] = []
    for path in paths:
        rel = _rel(path)
        if rel in ALLOWLIST:
            continue
        source = path.read_text(encoding="utf-8")
        for line in find_base_subscripts(source):
            problems.append(
                f"{rel}:{line}: construye Envelope[EventPayload] base. La emision "
                "real debe usar Envelope[PayloadConcreto]: la base tiene cero campos y "
                "pydantic serializa por el tipo declarado, asi que el payload viajaria "
                "VACIO (guardarrail 5.21, origen defecto B6.5). Si es validacion "
                "estructural legitima, va en la lista blanca del check."
            )
    return problems


def _emission_files() -> list[Path]:
    files: list[Path] = []
    for root in _SCAN_ROOTS:
        files.extend(sorted(root.rglob("*.py")))
    return files


def main() -> int:
    problems = check_files(_emission_files())
    if problems:
        print("FAIL check (Envelope[EventPayload] en emision, guardarrail 5.21):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        "OK check (guardarrail 5.21): ningun codigo de emision construye "
        "Envelope[EventPayload] base; la emision usa payloads concretos y no puede "
        "serializar un payload vacio."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
