"""Generador de JSON Schema desde la fuente Pydantic (ADR-006).

Cadena de contratos (DOC_ESTRUCTURA 2.5): contracts/source (Pydantic v2)
-> contracts/schemas (JSON Schema). Este script SOLO genera; el check de
regenerar-y-comparar (7.3/7.4) vive aparte. La salida es determinista
(claves ordenadas, sangria 2, salto final LF) para que la comparacion en
CI sea byte a byte.

Uso: python tools/gen_schemas.py
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = REPO_ROOT / "contracts" / "schemas"

sys.path.insert(0, str(REPO_ROOT / "contracts"))

from pydantic import TypeAdapter  # noqa: E402

from source.envelope import Envelope, EventPayload  # noqa: E402
from source.families import Family  # noqa: E402
from source.families.component import ComponentLifecyclePayload  # noqa: E402
from source.families.policy import (  # noqa: E402
    KillSwitchPayload,
    PolicyVersionPublishedPayload,
    SubjectInvalidatedPayload,
)


def serialize(schema: dict[str, object]) -> str:
    text = json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False)
    return text + "\n"


def _dump(path: Path, schema: dict[str, object]) -> None:
    path.write_text(serialize(schema), encoding="utf-8", newline="\n")


def build_schemas() -> dict[str, dict[str, object]]:
    envelope_schema = Envelope[EventPayload].model_json_schema()
    envelope_schema["title"] = "Envelope"
    family_schema = TypeAdapter(Family).json_schema()
    family_schema["title"] = "Family"
    component_schema = ComponentLifecyclePayload.model_json_schema()
    component_schema["title"] = "ComponentLifecyclePayload"
    kill_switch_schema = KillSwitchPayload.model_json_schema()
    kill_switch_schema["title"] = "KillSwitchPayload"
    version_published_schema = PolicyVersionPublishedPayload.model_json_schema()
    version_published_schema["title"] = "PolicyVersionPublishedPayload"
    subject_invalidated_schema = SubjectInvalidatedPayload.model_json_schema()
    subject_invalidated_schema["title"] = "SubjectInvalidatedPayload"
    return {
        "envelope.schema.json": envelope_schema,
        "family.schema.json": family_schema,
        "component_lifecycle.schema.json": component_schema,
        "policy_kill_switch.schema.json": kill_switch_schema,
        "policy_version_published.schema.json": version_published_schema,
        "policy_subject_invalidated.schema.json": subject_invalidated_schema,
    }


def main() -> int:
    SCHEMAS.mkdir(parents=True, exist_ok=True)
    for name, schema in build_schemas().items():
        _dump(SCHEMAS / name, schema)
        print(f"generado contracts/schemas/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
