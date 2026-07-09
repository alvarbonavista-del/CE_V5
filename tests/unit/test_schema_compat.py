from check_schema_compat import check_compatibility


def _schema(
    props: dict[str, dict[str, object]], required: list[str]
) -> dict[str, object]:
    return {"type": "object", "properties": props, "required": required}


def test_identico_es_compatible() -> None:
    s = _schema({"a": {"type": "string"}}, ["a"])
    assert check_compatibility(s, s) == []


def test_campo_eliminado_incompatible() -> None:
    old = _schema({"a": {"type": "string"}, "b": {"type": "string"}}, ["a"])
    new = _schema({"a": {"type": "string"}}, ["a"])
    assert check_compatibility(old, new) == ["campo eliminado: b"]


def test_campo_retipado_incompatible() -> None:
    old = _schema({"a": {"type": "string"}}, ["a"])
    new = _schema({"a": {"type": "integer"}}, ["a"])
    assert check_compatibility(old, new) == ["campo retipado: a"]


def test_campo_vuelto_requerido_incompatible() -> None:
    old = _schema({"a": {"type": "string"}, "b": {"type": "string"}}, ["a"])
    new = _schema({"a": {"type": "string"}, "b": {"type": "string"}}, ["a", "b"])
    assert check_compatibility(old, new) == ["campo vuelto requerido: b"]


def test_campo_nuevo_opcional_es_compatible() -> None:
    old = _schema({"a": {"type": "string"}}, ["a"])
    new = _schema({"a": {"type": "string"}, "b": {"type": "string"}}, ["a"])
    assert check_compatibility(old, new) == []


def test_enum_reducido_incompatible() -> None:
    old: dict[str, object] = {"enum": ["x", "y"], "type": "string"}
    new: dict[str, object] = {"enum": ["x"], "type": "string"}
    assert check_compatibility(old, new) == ["valor de enum eliminado: 'y'"]


def test_enum_ampliado_es_compatible() -> None:
    old: dict[str, object] = {"enum": ["x"], "type": "string"}
    new: dict[str, object] = {"enum": ["x", "y"], "type": "string"}
    assert check_compatibility(old, new) == []
