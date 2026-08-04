"""CVD: Cumulative Volume Delta desde el delta de orderflow (P08c, nueva en v5.0).

CVD = acumulado del delta de barra (orderflow.delta). INTEGRADOR: no olvida; el valor
de T es el de T-1 mas el delta de T dentro de su ventana de reset. Por eso una
correccion en la barra k desplaza TODO el acumulado posterior (NO-CONFORME para
correccion en v5.0, como RECURSIVE); el snapshot/replay por ventana de reset es del
materializer + CE-14, no de aqui.

reset_policy es PARAMETRO declarado, EN la cache_key (OA-1; rolling-CVD y session-CVD
son identidades distintas, no colisionan):
- rolling (DEFAULT, ratificado por Central): acumulado continuo, sin reset. Es el que
  F3 (divergencia de CVD) referencia por su CONTINUIDAD entre swings; un reset entre los
  dos swings comparados romperia la divergencia, y cripto es 24/7 sin sesion natural.
- session_utc: resetea el acumulado en cada frontera de dia UTC.

DETERMINISTA (no detector): DEC-AHP-01 NO aplica. El compute puro lleva la LOGICA de
reset; el materializer decide la EXTENSION/ancla de la secuencia (CE-14). El valor
ABSOLUTO del rolling depende de ese ancla, pero la divergencia es anchor-invariante, asi
que F3 no lo necesita.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from ce_v5.platform.rules.orderflow import ORDERFLOW_DELTA_SOURCE_ID
from source.datasource import (
    DataSourceDeclaration,
    HistoryUnit,
    MemoryModel,
    ParamSpec,
    Servibility,
    SharingScope,
    SourceType,
)
from source.families.market import Timeframe
from source.rules.scalar import ScalarType, ScalarValue

if TYPE_CHECKING:
    from collections.abc import Sequence

CVD_SOURCE_ID = "cvd.value"

# Indice de dia UTC = open_time_ms // _MS_PER_DAY. La epoca es medianoche UTC, asi
# que el indice cambia EXACTAMENTE en cada medianoche UTC. Entero, sin datetime.
_MS_PER_DAY = 86_400_000


class ResetPolicy(StrEnum):
    """Politica de reset del acumulado de CVD (OA-1). Parametro en la cache_key."""

    ROLLING = "rolling"
    SESSION_UTC = "session_utc"


# reset_policy entra en la cache_key: dos CVD con distinta politica son HECHOS DISTINTOS
# (rolling-CVD y session-CVD), no comparten evaluacion.
_CVD_CACHE_KEY_SCHEMA: tuple[str, ...] = (
    "exchange",
    "symbol",
    "market_type",
    "timeframe",
    "reset_policy",
)


def cvd_declaration() -> DataSourceDeclaration:
    """cvd.value: acumulado del delta (INTEGRATOR), reset_policy en la cache_key."""
    return DataSourceDeclaration(
        source_id=CVD_SOURCE_ID,
        source_type=SourceType.OBSERVABLE,
        servibility=Servibility.CONTINUOUS,
        # INTEGRATOR: acumula desde un origen y no olvida; una correccion arrastra el
        # error sin fin dentro de su ventana de reset -> NO-CONFORME para correccion en
        # v5.0 (como RECURSIVE). El snapshot/replay por ventana de reset es de CE-14.
        memory_model=MemoryModel.INTEGRATOR,
        value_type=ScalarType.DECIMAL,
        evaluation_contexts=tuple(tf.value for tf in Timeframe),
        history_units=(HistoryUnit.BARS,),
        params=(
            ParamSpec(
                name="reset_policy",
                value_type=ScalarType.STRING,
                default=ScalarValue(
                    scalar_type=ScalarType.STRING,
                    string_value=ResetPolicy.ROLLING.value,
                ),
                # Dominio en strings planos (MAT-05 Q2 fix): cvd.py conoce su propio
                # enum y lo vierte aqui; el contrato no importa platform.
                valid_values=tuple(policy.value for policy in ResetPolicy),
            ),
        ),
        # OVERRIDE-HABILITADO (MAT-05 Q2 fix): with_params (materializers.py) lo
        # consume hoy. Es el consumidor de referencia de la propagacion de params.
        overridable_params=("reset_policy",),
        shared_evaluation=True,
        sharing_scope=SharingScope.PUBLIC_CROSS_TENANT,
        cache_key_schema=_CVD_CACHE_KEY_SCHEMA,
        consumes=(ORDERFLOW_DELTA_SOURCE_ID,),
    )


def declarations() -> tuple[DataSourceDeclaration, ...]:
    """Declaraciones que este modulo publica al catalogo vivo (discovery, MAT-02)."""
    return (cvd_declaration(),)


def session_starts(
    open_times: Sequence[int], *, previous_open_time: int | None = None
) -> tuple[bool, ...]:
    """Por barra: si ABRE una sesion UTC nueva respecto a su predecesora.

    previous_open_time es la barra ANTERIOR a la ventana -- el ANCLA del replay -- o
    None si la ventana arranca la secuencia. La primera barra sin predecesora NO abre
    sesion: arranca el acumulado, no lo resetea (por eso el bootstrap y el replay desde
    ancla dan la MISMA cola, ADR-007).

    Es el UNICO sitio donde vive la frontera de sesion: compute_cvd y el materializador
    INTEGRATOR (que siembra desde snapshot y no puede usar compute_cvd tal cual) la
    comparten en vez de reimplementarla cada uno.
    """
    starts: list[bool] = []
    previous_day = (
        None if previous_open_time is None else previous_open_time // _MS_PER_DAY
    )
    for open_time in open_times:
        day = open_time // _MS_PER_DAY
        starts.append(previous_day is not None and day != previous_day)
        previous_day = day
    return tuple(starts)


def compute_cvd(
    bars: Sequence[tuple[int, Decimal]],
    *,
    reset_policy: ResetPolicy = ResetPolicy.ROLLING,
) -> tuple[Decimal, ...]:
    """Acumulado del delta (oldest->newest) segun reset_policy. Serie de igual longitud.

    Cada barra es (open_time_ms, delta). rolling: acumulado continuo, sin reset.
    session_utc: el acumulado vuelve a cero al cambiar el dia UTC (session_starts).
    Ventana vacia -> tupla vacia. El ancla (donde arranca la secuencia) lo decide el
    materializer (CE-14); aqui el acumulado arranca en el primer elemento recibido.
    """
    if not bars:
        return ()
    resets = (
        session_starts([open_time for open_time, _ in bars])
        if reset_policy is ResetPolicy.SESSION_UTC
        else (False,) * len(bars)
    )
    cvd: list[Decimal] = []
    running = Decimal(0)
    for (_open_time, delta), starts_session in zip(bars, resets, strict=True):
        running = delta if starts_session else running + delta
        cvd.append(running)
    return tuple(cvd)
