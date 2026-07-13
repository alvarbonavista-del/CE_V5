"""Normalizacion canonica del email como identificador de login (P06b).

Un mismo buzon escrito con mayusculas distintas es la MISMA persona: si no se
normaliza, 'Ana@x.com' y 'ana@x.com' serian dos cuentas y el login de una no
encontraria a la otra. La normalizacion vive en el NUCLEO (no en la base ni en la
API) para que exista UNA sola definicion; la restriccion CHECK de app_user
(email = lower(email)) es la red de seguridad del motor, no la regla.
"""

from __future__ import annotations


def normalize_email(raw: str) -> str:
    """Forma canonica del email: sin espacios alrededor y en minusculas."""
    normalized = raw.strip().lower()
    if not normalized:
        msg = "El email no puede estar vacio."
        raise ValueError(msg)
    return normalized
