"""Check: paridad registro <-> artefactos de contrato (ADR-006). Gate sin norte.

ADR-006 fija la cadena contracts/source -> contracts/schemas -> frontend/generated como
frontera unica y fuente unica de verdad: CADA payload concreto que se puede emitir tiene
su JSON Schema en contracts/schemas Y su tipo TS en frontend/generated. Este check
hace cumplir esa paridad contra EVENT_PAYLOAD_REGISTRY: si un event_type esta
registrado (luego es emitible) pero su payload no tiene schema o no tiene tipo TS, la
superficie de contrato es inconsistente y un consumidor externo no puede
deserializarlo.

COMO SE EMPAREJA. gen_schemas fija el `title` de cada schema al NOMBRE de la clase de
payload (ComponentLifecyclePayload, RuleFiringPayload, ...), y gen_ts_types nombra el
tipo TS con ese title y el fichero <base>.ts junto al <base>.schema.json. Asi que la
paridad se comprueba por el title del schema y la existencia del .ts hermano, sin un
mapa paralelo clase->fichero que pudiera desincronizarse.

ALCANCE. Se comprueba UNA vez por clase de payload DISTINTA (los 11 event_type de
component.* comparten ComponentLifecyclePayload -> un solo artefacto). El check reporta
CADA familia sin artefacto, de CUALQUIER pieza: es transversal. La decision de GENERAR o
no lo que falte es de cada pieza (frontera de pieza), no de este check.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "contracts"))

from source.families.registry import EVENT_PAYLOAD_REGISTRY  # noqa: E402

SCHEMAS_DIR = REPO_ROOT / "contracts" / "schemas"
TS_DIR = REPO_ROOT / "frontend" / "src" / "shared-contracts" / "generated"


def _schema_titles(schemas_dir: Path) -> dict[str, str]:
    """title -> nombre base del fichero, para cada *.schema.json presente."""
    titles: dict[str, str] = {}
    for path in sorted(schemas_dir.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        title = schema.get("title")
        if isinstance(title, str):
            titles[title] = path.name[: -len(".schema.json")]
    return titles


def check_artifacts(
    registry: Mapping[str, tuple[type, int]],
    schema_titles: Mapping[str, str],
    ts_basenames: frozenset[str],
) -> list[str]:
    """Logica pura: devuelve las violaciones (vacia = verde).

    Por cada clase de payload DISTINTA del registro exige un schema (por title) y su .ts
    hermano. Reporta el event_type de muestra para ubicar la familia.
    """
    problems: list[str] = []
    # clase de payload -> un event_type de muestra que la usa (para el mensaje).
    sample_event_type: dict[str, str] = {}
    for event_type, (payload_cls, _version) in registry.items():
        sample_event_type.setdefault(payload_cls.__name__, event_type)

    for class_name in sorted(sample_event_type):
        sample = sample_event_type[class_name]
        base = schema_titles.get(class_name)
        if base is None:
            problems.append(
                f"{class_name} (p.ej. {sample}): sin JSON Schema en contracts/schemas "
                "(ningun *.schema.json con ese title). ADR-006 exige un schema por "
                "payload emitible."
            )
            continue
        if base not in ts_basenames:
            problems.append(
                f"{class_name} (p.ej. {sample}): tiene schema {base}.schema.json pero "
                f"falta su tipo TS {base}.ts en frontend/generated (ADR-006)."
            )
    return problems


def _ts_basenames(ts_dir: Path) -> frozenset[str]:
    return frozenset(path.stem for path in ts_dir.glob("*.ts"))


def main() -> int:
    problems = check_artifacts(
        EVENT_PAYLOAD_REGISTRY,
        _schema_titles(SCHEMAS_DIR),
        _ts_basenames(TS_DIR),
    )
    if problems:
        print("FAIL check (paridad registro <-> artefactos de contrato, ADR-006):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        "OK check (artefactos de contrato, ADR-006): cada payload emitible del "
        "registro tiene su JSON Schema y su tipo TS generados."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
