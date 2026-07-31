"""Serializacion canonica de PivotState para el snapshot de pivotphase (P08c P5 T4b).

DICTAMEN P08c-PIVOT-06 Q3: formato CLAVE=VALOR en ORDEN ALFABETICO FIJO, un campo por
linea, Decimals como str exacto; parser inverso con round-trip BIT-EXACTO (ADR-007). Sin
JSON: legible para debug, sin dependencia de libreria, sin reordenamiento de claves. Es
lo que se guarda en la columna `state` (text) del pivotphase_snapshot y lo que ANCLA el
replay determinista de la FSM.
"""

from __future__ import annotations

from decimal import Decimal

from ce_v5.platform.rules.pivotphase import PivotState

# Campos de PivotState en ORDEN ALFABETICO FIJO. El orden es parte del contrato
# canonico: el mismo estado produce SIEMPRE el mismo texto (round-trip determinista).
_ORDERED_FIELDS: tuple[str, ...] = (
    "direction",
    "exhaustion_count",
    "flip_count",
    "impulse_count",
    "phase",
    "phase1_peak_delta",
    "phase2_level_price",
    "phase2_level_type",
    "phase3_zone_price",
    "phase3_zone_strength",
    "phase5_bars",
)


def serialize_state(state: PivotState) -> str:
    """PivotState -> texto canonico 'clave=valor' (una linea por campo, orden fijo).

    Decimals via str() (exacto: conserva escala); ints y strs tal cual. Los campos str
    (direction, phase2_level_type) son vocabulario controlado sin '=' ni salto de linea.
    """
    return "\n".join(f"{name}={getattr(state, name)}" for name in _ORDERED_FIELDS)


def parse_state(text: str) -> PivotState:
    """Texto canonico -> PivotState (inverso exacto de serialize_state).

    Valida que aparezcan EXACTAMENTE los campos esperados; cualquier campo faltante,
    sobrante o linea sin '=' es error (fail-loud, ADR-007: un estado corrupto no debe
    anclar un replay). Construccion explicita y tipada.
    """
    raw: dict[str, str] = {}
    for line in text.split("\n"):
        key, sep, value = line.partition("=")
        if sep != "=":
            msg = f"linea de PivotState sin '=': {line!r}."
            raise ValueError(msg)
        if key in raw:
            msg = f"campo de PivotState duplicado: {key}."
            raise ValueError(msg)
        raw[key] = value
    if set(raw) != set(_ORDERED_FIELDS):
        msg = (
            f"campos de PivotState invalidos: {sorted(raw)} != "
            f"{sorted(_ORDERED_FIELDS)}."
        )
        raise ValueError(msg)
    return PivotState(
        phase=int(raw["phase"]),
        direction=raw["direction"],
        impulse_count=int(raw["impulse_count"]),
        phase1_peak_delta=Decimal(raw["phase1_peak_delta"]),
        phase2_level_price=Decimal(raw["phase2_level_price"]),
        phase2_level_type=raw["phase2_level_type"],
        phase3_zone_price=Decimal(raw["phase3_zone_price"]),
        phase3_zone_strength=Decimal(raw["phase3_zone_strength"]),
        exhaustion_count=int(raw["exhaustion_count"]),
        flip_count=int(raw["flip_count"]),
        phase5_bars=int(raw["phase5_bars"]),
    )
