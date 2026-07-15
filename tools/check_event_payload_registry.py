"""Check: registro event_type -> payload (CA-06). Gate sin numero de norte.

Hace cumplir la regla de gobierno de source.families.registry: TODO event_type
declarado en contracts/source/families/* debe estar en EXACTAMENTE uno de los dos
mapas (EVENT_PAYLOAD_REGISTRY o DEFERRED_EVENT_TYPES). Falla si:
- un event_type declarado no esta en ninguno de los dos mapas;
- un event_type esta en los DOS (ambiguo: o tiene payload o no);
- una entrada del registro no es (clase, event_schema_version);
- el registro apunta a EventPayload BASE, a dict/Any, a una no-clase o a una
  clase que no es un modelo Pydantic que extienda EventPayload.

ENDURECIMIENTO (CSA, condicion previa al commit): un tipo diferido no se puede
"aparcar y olvidar". Ademas de lo anterior, falla si:
- una entrada de DEFERRED no es una DeferredEventType con sus SIETE campos, o
  alguno esta vacio;
- status no es exactamente 'deferred_until_piece';
- owner_piece no es una pieza del ROADMAP (lista explicita ROADMAP_PIECES);
- owner_piece es una pieza YA CERRADA (CLOSED_PIECES): diferir a una pieza que ya
  paso significa que nadie lo pagara nunca; es deuda disfrazada;
- el tipo diferido esta EN USO por el codigo actual (aparece su literal en
  backend/src): un diferido que alguien ya usa es una mentira en el registro.

Como check_types_frontend.mjs de P00, es un gate sin numero de norte; se engancha
al barrido de checks del job backend. No importa codigo de componentes: solo
inspecciona el registro, las familias y (para el escaneo) el arbol backend/src.
"""

import importlib
import sys
from collections.abc import Collection, Mapping
from enum import StrEnum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "contracts"))

from pydantic import BaseModel  # noqa: E402

from source.envelope import EventPayload  # noqa: E402
from source.families import validate_event_type  # noqa: E402
from source.families.registry import (  # noqa: E402
    DEFERRED_EVENT_TYPES,
    DEFERRED_STATUS,
    EVENT_PAYLOAD_REGISTRY,
    DeferredEventType,
)

_FAMILIES_DIR = REPO_ROOT / "contracts" / "source" / "families"
_BACKEND_SRC = REPO_ROOT / "backend" / "src"

# Ids de pieza del ROADMAP (lista explicita: la fuente de verdad del check).
ROADMAP_PIECES = frozenset(
    {
        "P00",
        "P01",
        "P02",
        "P02b",
        "P03",
        "P04",
        "P05",
        "P06",
        "P06b",
        "P07",
        "P08",
        "P09a",
        "P09b",
        "P10a",
        "P10b",
        "P11",
        "P12a",
        "P12b",
        "P13",
    }
)
# Piezas YA CERRADAS: diferir a una de ellas es deuda disfrazada (nadie lo
# pagara: la pieza ya paso). Se anade cada pieza AL CERRARLA.
# P06 y P06b se anaden en P07: estaban cerradas (M2, 2026-07-14) y NO figuraban,
# de modo que el check habria admitido diferir un tipo a una pieza ya pasada.
# Defecto del guardarrail hallado por el periferico de P07 al leerlo antes de
# tocarlo; se corrige hacia delante (mismo patron que la ENMIENDA HISTORICA 2).
CLOSED_PIECES = frozenset(
    {"P00", "P01", "P02", "P02b", "P03", "P04", "P05", "P06", "P06b"}
)


def _is_event_type(value: str) -> bool:
    try:
        validate_event_type(value)
    except ValueError:
        return False
    return True


def _declared_event_types() -> set[str]:
    """Todos los event_type declarados en los enums de contracts/source/families."""
    declared: set[str] = set()
    for path in sorted(_FAMILIES_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module = importlib.import_module(f"source.families.{path.stem}")
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, StrEnum)
                and obj is not StrEnum
            ):
                for member in obj:
                    if _is_event_type(str(member.value)):
                        declared.add(str(member.value))
    return declared


def scan_in_use(deferred_keys: Collection[str], root: Path) -> set[str]:
    """event_type diferidos cuyo LITERAL aparece en el codigo bajo ``root``.

    Escanea backend/src (excluye contracts/, el registro y los tests por estar
    fuera de ese arbol). Un diferido cuyo literal produce o consume el codigo
    actual es una mentira: su payload/productor no existen, no puede estar en uso.
    """
    remaining = set(deferred_keys)
    in_use: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        if not remaining:
            break
        text = path.read_text(encoding="utf-8")
        for event_type in sorted(remaining):
            if event_type in text:
                in_use.add(event_type)
        remaining -= in_use
    return in_use


_DEFERRED_FIELDS = (
    "event_type",
    "family",
    "motivo",
    "owner_piece",
    "dependency_reason",
    "exit_rule",
    "status",
)


def _deferred_problems(
    event_type: str, entry: object, in_use: Collection[str]
) -> list[str]:
    if not isinstance(entry, DeferredEventType):
        return [
            f"{event_type}: la entrada diferida no es una DeferredEventType "
            f"estructurada (es {type(entry)!r}); un tipo diferido no puede ser una "
            "cadena suelta que se aparca y se olvida (CSA)."
        ]
    problems: list[str] = []
    for name in _DEFERRED_FIELDS:
        value = getattr(entry, name)
        if not isinstance(value, str) or not value.strip():
            problems.append(
                f"{event_type}: el campo diferido {name!r} esta vacio o ausente; "
                "los siete campos son obligatorios (CSA)."
            )
    if entry.status != DEFERRED_STATUS:
        problems.append(
            f"{event_type}: status {entry.status!r} no es exactamente "
            f"{DEFERRED_STATUS!r}."
        )
    if entry.owner_piece.strip():
        if entry.owner_piece not in ROADMAP_PIECES:
            problems.append(
                f"{event_type}: owner_piece {entry.owner_piece!r} no es una pieza "
                "del roadmap."
            )
        elif entry.owner_piece in CLOSED_PIECES:
            problems.append(
                f"{event_type}: owner_piece {entry.owner_piece!r} es una pieza YA "
                "CERRADA; diferir a una pieza que ya paso es deuda disfrazada "
                "(nadie lo pagara nunca)."
            )
    if event_type in in_use:
        problems.append(
            f"{event_type}: tipo diferido EN USO por el codigo actual "
            "(backend/src); un diferido que alguien ya usa es una mentira en el "
            "registro."
        )
    return problems


def _entry_problems(event_type: str, entry: object) -> list[str]:
    if not isinstance(entry, tuple) or len(entry) != 2:
        return [f"{event_type}: la entrada no es (clase, event_schema_version)."]
    payload_cls, version = entry
    problems: list[str] = []
    if not isinstance(payload_cls, type):
        problems.append(
            f"{event_type}: el registro no apunta a una clase "
            f"(es {type(payload_cls)!r})."
        )
    elif payload_cls is EventPayload:
        problems.append(
            f"{event_type}: el registro apunta a EventPayload BASE; "
            "exige un payload concreto."
        )
    elif not issubclass(payload_cls, BaseModel):
        problems.append(
            f"{event_type}: el registro apunta a {payload_cls!r}, "
            "que no es un modelo Pydantic."
        )
    elif not issubclass(payload_cls, EventPayload):
        problems.append(
            f"{event_type}: el registro apunta a {payload_cls!r}, "
            "que no extiende EventPayload."
        )
    if not isinstance(version, int):
        problems.append(
            f"{event_type}: event_schema_version no es un entero ({version!r})."
        )
    return problems


def check_registry(
    declared: set[str],
    registry: Mapping[str, object],
    deferred: Mapping[str, object],
    *,
    in_use: Collection[str] = frozenset(),
) -> list[str]:
    """Logica pura del check: devuelve las violaciones (vacia = verde).

    ``in_use`` es el conjunto de event_type diferidos hallados EN USO en el codigo
    (lo calcula scan_in_use). Se inyecta para poder probar la regla sin ensuciar
    el repo.
    """
    problems: list[str] = []
    reg_keys = set(registry)
    def_keys = set(deferred)
    for event_type in sorted(declared):
        in_reg = event_type in reg_keys
        in_def = event_type in def_keys
        if not in_reg and not in_def:
            problems.append(
                f"{event_type}: event_type declarado sin entrada en el registro "
                "ni en los diferidos (CA-06)."
            )
        elif in_reg and in_def:
            problems.append(
                f"{event_type}: aparece en el registro Y en los diferidos "
                "(ambiguo: o tiene payload o no lo tiene)."
            )
    for event_type in sorted(reg_keys | def_keys):
        if event_type not in declared:
            problems.append(
                f"{event_type}: entrada para un event_type no declarado en "
                "contracts/source/families/*."
            )
    for event_type in sorted(reg_keys):
        problems.extend(_entry_problems(event_type, registry[event_type]))
    for event_type in sorted(def_keys):
        problems.extend(_deferred_problems(event_type, deferred[event_type], in_use))
    return problems


def main() -> int:
    in_use = scan_in_use(set(DEFERRED_EVENT_TYPES), _BACKEND_SRC)
    problems = check_registry(
        _declared_event_types(),
        EVENT_PAYLOAD_REGISTRY,
        DEFERRED_EVENT_TYPES,
        in_use=in_use,
    )
    if problems:
        print("FAIL check (registro event_type -> payload, CA-06):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        "OK check (registro event_type -> payload, CA-06): todos los event_type "
        "declarados estan registrados o diferidos, sin ambiguedad."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
