"""Unit tests del discovery por carpeta (ADR-009)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ce_v5.core.discovery import RejectionReason, discover, import_entrypoint


class _RecordingLoader:
    """Loader de test: apunta cada entrypoint que se le pide cargar."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, reference: str) -> object:
        self.calls.append(reference)
        return object()


def _failing_loader(reference: str) -> object:
    raise ImportError(f"boom: {reference}")


def _manifest(
    component_id: str = "dummy",
    entrypoint: str | None = "pkg.mod:build",
) -> dict[str, object]:
    data: dict[str, object] = {
        "id": component_id,
        "version": "1.0.0",
        "manifest_schema_version": 1,
        "type": "worker",
    }
    if entrypoint is not None:
        data["entrypoint"] = entrypoint
    return data


def _write_component(root: Path, name: str, manifest: dict[str, object] | str) -> Path:
    folder = root / name
    folder.mkdir()
    content = manifest if isinstance(manifest, str) else json.dumps(manifest)
    (folder / "manifest.json").write_text(content, encoding="utf-8")
    return folder


def test_descubre_componente_valido(tmp_path: Path) -> None:
    _write_component(tmp_path, "dummy", _manifest())
    loader = _RecordingLoader()
    result = discover(tmp_path, loader)
    assert len(result.registered) == 1
    assert result.rejected == ()
    assert result.registered[0].component_id == "dummy"
    assert loader.calls == ["pkg.mod:build"]


def test_carpeta_sin_manifest_se_rechaza(tmp_path: Path) -> None:
    (tmp_path / "vacia").mkdir()
    loader = _RecordingLoader()
    result = discover(tmp_path, loader)
    assert result.registered == ()
    assert len(result.rejected) == 1
    assert result.rejected[0].reason is RejectionReason.NO_MANIFEST
    assert loader.calls == []


def test_manifest_invalido_no_carga_codigo(tmp_path: Path) -> None:
    _write_component(tmp_path, "malo", {"id": "malo"})
    loader = _RecordingLoader()
    result = discover(tmp_path, loader)
    assert result.registered == ()
    assert result.rejected[0].reason is RejectionReason.INVALID_MANIFEST
    assert loader.calls == []


def test_json_malformado_se_rechaza(tmp_path: Path) -> None:
    _write_component(tmp_path, "roto", "{ no es json")
    loader = _RecordingLoader()
    result = discover(tmp_path, loader)
    assert result.rejected[0].reason is RejectionReason.INVALID_MANIFEST
    assert loader.calls == []


def test_manifest_sin_entrypoint_se_rechaza(tmp_path: Path) -> None:
    _write_component(tmp_path, "sinep", _manifest(entrypoint=None))
    loader = _RecordingLoader()
    result = discover(tmp_path, loader)
    assert result.rejected[0].reason is RejectionReason.NO_ENTRYPOINT
    assert loader.calls == []


def test_id_duplicado_se_rechaza(tmp_path: Path) -> None:
    _write_component(tmp_path, "a_primero", _manifest(component_id="dup"))
    _write_component(tmp_path, "b_segundo", _manifest(component_id="dup"))
    loader = _RecordingLoader()
    result = discover(tmp_path, loader)
    assert len(result.registered) == 1
    assert len(result.rejected) == 1
    assert result.rejected[0].reason is RejectionReason.DUPLICATE_ID


def test_entrypoint_que_falla_al_cargar(tmp_path: Path) -> None:
    _write_component(tmp_path, "falla", _manifest())
    result = discover(tmp_path, _failing_loader)
    assert result.registered == ()
    assert result.rejected[0].reason is RejectionReason.ENTRYPOINT_LOAD_ERROR


def test_root_inexistente_da_resultado_vacio(tmp_path: Path) -> None:
    loader = _RecordingLoader()
    result = discover(tmp_path / "no_existe", loader)
    assert result.registered == ()
    assert result.rejected == ()


def test_import_entrypoint_resuelve_modulo_y_atributo() -> None:
    assert import_entrypoint("json") is json
    assert import_entrypoint("json:dumps") is json.dumps


def test_import_entrypoint_atributo_inexistente_lanza() -> None:
    with pytest.raises(AttributeError):
        import_entrypoint("json:no_existe_atributo")


def test_ignora_carpetas_privadas(tmp_path: Path) -> None:
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "_privada").mkdir()
    _write_component(tmp_path, "dummy", _manifest())
    loader = _RecordingLoader()
    result = discover(tmp_path, loader)
    assert len(result.registered) == 1
    assert result.rejected == ()
