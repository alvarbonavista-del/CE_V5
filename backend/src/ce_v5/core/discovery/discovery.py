"""Discovery de Componentes por convencion de carpetas (ADR-009).

Escanea el directorio components/, y por cada subcarpeta: localiza su
manifest.json, lo VALIDA estaticamente (ADR-008) ANTES de tocar codigo,
comprueba duplicados de id, y SOLO DESPUES carga el entrypoint declarado
mediante un loader inyectado. Nunca importa codigo para saber que un
componente existe (ADR-009): la existencia se decide leyendo el manifest
(dato), no importando modulos. El import del entrypoint es DINAMICO
(importlib), por lo que el nucleo no adquiere dependencia estatica de
components/* (frontera de capas, DOC_ESTRUCTURA sec.6). El resultado es
OBSERVABLE: componentes registrados y rechazados con su motivo. Sin
hot-reload en v5.0: el discovery ocurre al arranque.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import ValidationError

from ce_v5.core.component.definition import ComponentDefinition
from ce_v5.core.manifest import validate_manifest

MANIFEST_FILENAME = "manifest.json"

# Loader inyectable: dado el entrypoint declarado, importa su objetivo. Se
# inyecta para validar antes de importar y para testear sin paquetes reales.
EntrypointLoader = Callable[[str], object]


class RejectionReason(StrEnum):
    """Motivos por los que el discovery rechaza una carpeta (ADR-009)."""

    NO_MANIFEST = "no_manifest"
    INVALID_MANIFEST = "invalid_manifest"
    DUPLICATE_ID = "duplicate_id"
    NO_ENTRYPOINT = "no_entrypoint"
    ENTRYPOINT_LOAD_ERROR = "entrypoint_load_error"


@dataclass(frozen=True, slots=True)
class Rejection:
    """Una carpeta rechazada por el discovery, con su motivo (observable)."""

    path: Path
    reason: RejectionReason
    detail: str


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Resultado observable del discovery (ADR-009)."""

    registered: tuple[ComponentDefinition, ...]
    rejected: tuple[Rejection, ...]


def discover(components_root: Path, loader: EntrypointLoader) -> DiscoveryResult:
    """Escanea components_root y descubre Componentes (ADR-009).

    Orden por carpeta: leer manifest -> validar (capa estatica, ADR-008) ->
    comprobar duplicado de id -> exigir entrypoint -> cargar el entrypoint
    con loader. Un manifest invalido NUNCA llega a cargar codigo. Las
    carpetas se recorren en orden determinista.
    """
    registered: list[ComponentDefinition] = []
    rejected: list[Rejection] = []
    seen_ids: set[str] = set()

    if not components_root.is_dir():
        return DiscoveryResult(registered=(), rejected=())

    for path in sorted(
        p
        for p in components_root.iterdir()
        if p.is_dir() and not p.name.startswith((".", "_"))
    ):
        manifest_file = path / MANIFEST_FILENAME
        if not manifest_file.is_file():
            rejected.append(
                Rejection(path, RejectionReason.NO_MANIFEST, MANIFEST_FILENAME)
            )
            continue

        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            rejected.append(Rejection(path, RejectionReason.INVALID_MANIFEST, str(exc)))
            continue

        try:
            manifest = validate_manifest(data)
        except ValidationError as exc:
            rejected.append(Rejection(path, RejectionReason.INVALID_MANIFEST, str(exc)))
            continue

        if manifest.id in seen_ids:
            rejected.append(Rejection(path, RejectionReason.DUPLICATE_ID, manifest.id))
            continue
        seen_ids.add(manifest.id)

        entrypoint = manifest.entrypoint
        if entrypoint is None:
            rejected.append(Rejection(path, RejectionReason.NO_ENTRYPOINT, manifest.id))
            continue

        try:
            loader(entrypoint)
        except Exception as exc:
            rejected.append(
                Rejection(path, RejectionReason.ENTRYPOINT_LOAD_ERROR, str(exc))
            )
            continue

        registered.append(ComponentDefinition(manifest=manifest, path=path))

    return DiscoveryResult(registered=tuple(registered), rejected=tuple(rejected))


def import_entrypoint(reference: str) -> object:
    """Loader real: importa el modulo del entrypoint y resuelve el atributo.

    Formato 'modulo[:atributo]'. Unico punto que importa codigo de un
    Componente, y solo tras validar su manifest (ADR-009). El import es
    dinamico (importlib): el nucleo no depende estaticamente de components/*.
    """
    module_name, sep, attr = reference.partition(":")
    module = importlib.import_module(module_name)
    if not sep:
        return module
    target: object = getattr(module, attr)
    return target
