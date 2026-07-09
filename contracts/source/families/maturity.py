"""Payload base con madurez para familias temporales (ADR-007).

maturity_state se modela en el PAYLOAD de las familias que lo necesitan
(market.*, datasource.*), no como campo universal del envelope. Una
correccion no muta el original (append-only): es un evento nuevo que
referencia el idempotency_key del corregido (corrects_idempotency_key).

Nota de import: este modulo depende de source.envelope.payload; por eso NO
se reexporta desde source.families.__init__ (evita un ciclo de imports
envelope <-> families).
"""

from pydantic import model_validator

from source.envelope.payload import EventPayload
from source.time import MaturityState


class MaturityAwarePayload(EventPayload):
    """Payload base para familias con madurez (market.*, datasource.*).

    maturity_state indica si el dato es provisional, cerrado, una
    correccion o una reemision. corrects_idempotency_key referencia el
    idempotency_key del evento corregido: obligatorio en una correccion,
    prohibido en provisional/closed, opcional en una reemision (ADR-007 no
    fija la referencia de reemision; se deja abierta).
    """

    maturity_state: MaturityState
    corrects_idempotency_key: str | None = None

    @model_validator(mode="after")
    def _correccion_referencia_original(self) -> "MaturityAwarePayload":
        if (
            self.maturity_state is MaturityState.CORRECTION
            and self.corrects_idempotency_key is None
        ):
            msg = "maturity_state=correction exige corrects_idempotency_key (ADR-007)."
            raise ValueError(msg)
        if (
            self.maturity_state in (MaturityState.PROVISIONAL, MaturityState.CLOSED)
            and self.corrects_idempotency_key is not None
        ):
            msg = "corrects_idempotency_key no aplica a provisional/closed (ADR-007)."
            raise ValueError(msg)
        return self
