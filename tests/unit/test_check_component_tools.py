"""Unit tests de los checks de Componentes 7.5, 7.6 y 7.9."""

from __future__ import annotations

import json
from pathlib import Path

import check_component_docs
import check_manifests
import check_orphans

_VALID: dict[str, object] = {
    "id": "dummy",
    "version": "1.0.0",
    "manifest_schema_version": 1,
    "type": "worker",
    "entrypoint": "pkg.mod:build",
}


def _component(
    root: Path,
    name: str,
    *,
    manifest: object = _VALID,
    readme: bool = True,
) -> Path:
    folder = root / name
    folder.mkdir()
    if manifest is not None:
        text = manifest if isinstance(manifest, str) else json.dumps(manifest)
        (folder / "manifest.json").write_text(text, encoding="utf-8")
    if readme:
        (folder / "README.md").write_text("# proposito\n", encoding="utf-8")
    return folder


def _src_with_module(root: Path, module_name: str) -> Path:
    src = root / "src"
    pkg = src.joinpath(*module_name.split("."))
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    return src


def test_75_manifest_valido_no_da_problemas(tmp_path: Path) -> None:
    _component(tmp_path, "dummy")
    assert check_manifests._problems(tmp_path) == []


def test_75_manifest_invalido_se_detecta(tmp_path: Path) -> None:
    _component(tmp_path, "malo", manifest={"id": "x"})
    problems = check_manifests._problems(tmp_path)
    assert len(problems) == 1
    assert "manifest invalido" in problems[0]


def test_75_json_malformado_se_detecta(tmp_path: Path) -> None:
    _component(tmp_path, "roto", manifest="{ no json")
    problems = check_manifests._problems(tmp_path)
    assert len(problems) == 1
    assert "ilegible" in problems[0]


def test_75_carpeta_sin_manifest_no_es_problema_de_75(tmp_path: Path) -> None:
    _component(tmp_path, "sinman", manifest=None)
    assert check_manifests._problems(tmp_path) == []


def test_75_ignora_carpetas_privadas(tmp_path: Path) -> None:
    (tmp_path / "__pycache__").mkdir()
    _component(tmp_path, "dummy")
    assert check_manifests._problems(tmp_path) == []


def test_76_carpeta_sin_manifest_es_huerfano(tmp_path: Path) -> None:
    _component(tmp_path, "sinman", manifest=None)
    problems = check_orphans._problems(tmp_path, tmp_path / "src")
    assert len(problems) == 1
    assert "carpeta sin manifest.json" in problems[0]


def test_76_manifest_sin_entrypoint_es_huerfano(tmp_path: Path) -> None:
    manifest = dict(_VALID)
    del manifest["entrypoint"]
    _component(tmp_path, "sinep", manifest=manifest)
    problems = check_orphans._problems(tmp_path, tmp_path / "src")
    assert len(problems) == 1
    assert "sin entrypoint" in problems[0]


def test_76_entrypoint_inexistente_se_detecta(tmp_path: Path) -> None:
    _component(tmp_path, "dummy")
    problems = check_orphans._problems(tmp_path, tmp_path / "src")
    assert len(problems) == 1
    assert "entrypoint inexistente" in problems[0]


def test_76_entrypoint_existente_no_da_problema(tmp_path: Path) -> None:
    components = tmp_path / "components"
    components.mkdir()
    src = _src_with_module(tmp_path, "pkg.mod")
    _component(components, "dummy")
    assert check_orphans._problems(components, src) == []


def test_76_ignora_carpetas_privadas(tmp_path: Path) -> None:
    components = tmp_path / "components"
    components.mkdir()
    (components / "__pycache__").mkdir()
    src = _src_with_module(tmp_path, "pkg.mod")
    _component(components, "dummy")
    assert check_orphans._problems(components, src) == []


def test_79_con_readme_no_da_problema(tmp_path: Path) -> None:
    _component(tmp_path, "dummy")
    assert check_component_docs._problems(tmp_path) == []


def test_79_sin_readme_se_detecta(tmp_path: Path) -> None:
    _component(tmp_path, "dummy", readme=False)
    problems = check_component_docs._problems(tmp_path)
    assert len(problems) == 1
    assert "falta README.md" in problems[0]


def test_79_ignora_carpetas_privadas(tmp_path: Path) -> None:
    (tmp_path / "__pycache__").mkdir()
    _component(tmp_path, "dummy")
    assert check_component_docs._problems(tmp_path) == []
