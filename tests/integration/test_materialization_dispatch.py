"""Dispatch de materializacion de market.close por _series_for (CE-14, MAT-06).

POR QUE ESTE TEST FALTABA. El camino REAL de market.close -- _series_for -> dispatch
por SOURCE_ID -> _materialize -> read_close_window -- no lo cubria NINGUN test de
pytest: test_rules_cycle.py INYECTA la serie ya construida
({MARKET_CLOSE_SOURCE_ID: (Decimal(...),)}) y por eso nunca ejecuta el dispatch, y
read_close_window no aparecia en tests/. La unica cobertura viva era el script
tools/validate_rules_worker.py (T22-b), que NO corre en la bateria de CI.

Consecuencia del hueco: si _materialize enrutara market.close mal -- a
UnwiredSourceError, o a un spec de footprint que leyera otra tabla -- la bateria
seguiria verde y el motor se quedaria mudo (o mentiroso) en produccion. Este test peta
en ese caso; el de test_rules_cycle no.

Contra PostgreSQL REAL y con los DOS roles que manda la regla 5.20: las velas las
ESCRIBE el rol de INGESTA por el camino real (PostgresCandleWriter, fixture
persistir_vela) y la serie la LEE el rol de REGLAS, que es el unico con el GRANT
SELECT de la 0016. Con un doble en memoria no se probaria ni el grant ni el SQL.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from decimal import Decimal
from uuid import uuid4

import pytest

from ce_v5.entrypoints.worker_rules.composition import _series_for, build_catalog
from ce_v5.infra.db.psycopg_adapter import PsycopgDatabase
from ce_v5.platform.rules.compiler import compile
from ce_v5.platform.rules.rawclose import MARKET_CLOSE_SOURCE_ID
from source.families.market import (
    CandleClosedPayload,
    CandlePayload,
    MarketCandleEventType,
    MarketType,
    Timeframe,
)
from source.rules.condition import Condition
from source.rules.feature import Feature
from source.rules.group import Group
from source.rules.market_rules import AlertRule, AnyRule, MarketScope, RuleProduct
from source.rules.reference import DataSourceRef
from source.rules.rule import BindingKind, TargetBinding
from source.rules.scalar import ScalarType, ScalarValue
from source.rules.term import SourceTerm, Term, TermKind
from source.rules.vocab import (
    CanonicalFunction,
    CombineMode,
    ComparisonOperator,
    RuleCombineMode,
    TriggerPolicy,
)
from source.time import MaturityState

_DSN = os.environ.get("CE_V5_DATABASE_URL")
pytestmark = pytest.mark.skipif(_DSN is None, reason="requiere CE_V5_DATABASE_URL")

_EXCHANGE = "binance"
_SYMBOL = "BTC-USDT"
_TF = Timeframe.H1
_OPEN = 1_784_073_600_000
# Cuantas velas se siembran Y cuantas barras exige el plan (average sobre M barras).
_M = 4

Persistir = Callable[[CandlePayload, MarketCandleEventType, int], bool]


@pytest.fixture
def limpiar_velas(migrator_db: PsycopgDatabase) -> Iterator[None]:
    """market_candle y outbox no cuelgan de ningun tenant: se limpian a mano."""

    def _wipe() -> None:
        with migrator_db.transaction() as session:
            session.execute("DELETE FROM market_candle")
            session.execute("DELETE FROM outbox")

    _wipe()
    yield
    _wipe()


def _vela(indice: int, close: Decimal) -> CandleClosedPayload:
    """La vela CERRADA numero `indice` del flujo, con su cierre propio.

    El rango OHLC se DERIVA del cierre: el contrato exige high >= open/close y
    low <= open/close, asi que un high fijo se quedaria corto en cuanto los cierres
    suben. Lo que este test mide es el cierre; el resto del cuerpo solo tiene que ser
    coherente.
    """
    open_time = _OPEN + indice * _TF.duration_ms
    return CandleClosedPayload(
        maturity_state=MaturityState.CLOSED,
        exchange=_EXCHANGE,
        market_type=MarketType.SPOT,
        symbol=_SYMBOL,
        timeframe=_TF,
        open_time=open_time,
        close_time=open_time + _TF.duration_ms,
        open=close,
        high=close + Decimal("10"),
        low=close - Decimal("10"),
        close=close,
        volume=Decimal("12.5"),
    )


def _regla_que_pide_m_barras() -> AnyRule:
    """Regla MINIMA sobre market.close cuya historia dimensionada es EXACTAMENTE _M.

    average(count=n) exige n barras (functions.history_bars_needed), asi que el plan
    que salga del compilador REAL pedira _M velas. Con un acceso directo pediria 1 y la
    asercion sobre la ventana completa no probaria el recorte ni el orden.
    """
    condicion = Condition(
        node_id=uuid4(),
        left=Term(
            term_kind=TermKind.SOURCE,
            source=SourceTerm(
                ref=DataSourceRef(source_id=MARKET_CLOSE_SOURCE_ID),
                function=CanonicalFunction.AVERAGE,
                offset=_M,
            ),
        ),
        operator=ComparisonOperator.GT,
        right=Term(
            term_kind=TermKind.CONSTANT,
            constant=ScalarValue(scalar_type=ScalarType.DECIMAL, decimal_value="0"),
        ),
    )
    return AlertRule(
        product=RuleProduct.ALERT,
        rule_id=uuid4(),
        tenant_id=uuid4(),
        name="regla-de-dispatch",
        target_binding=TargetBinding(binding_kind=BindingKind.MARKET),
        trigger_policy=TriggerPolicy.CANDLE_CLOSE,
        groups=(
            Group(
                node_id=uuid4(),
                evaluation_context=_TF.value,
                combine_mode=CombineMode.ALL,
                features=(
                    Feature(
                        node_id=uuid4(),
                        conditions=(condicion,),
                        combine_mode=CombineMode.ALL,
                    ),
                ),
            ),
        ),
        veto=None,
        rule_combine_mode=RuleCombineMode.ALL,
        enabled=True,
        market_scope=MarketScope(exchange=_EXCHANGE, symbol=_SYMBOL),
    )


def test_market_close_dispatch_via_series_for(
    rules_db: PsycopgDatabase,
    persistir_vela: Persistir,
    limpiar_velas: None,
) -> None:
    """market.close materializa por el camino REAL, sin inyectar la serie."""
    # 1. Siembra con el rol de INGESTA: _M velas cerradas del mismo flujo, con cierres
    #    DISTINTOS. Si el dispatch devolviera la ventana desordenada o desplazada, con
    #    cierres iguales coincidiria por casualidad; con distintos, no.
    cierres = [Decimal(f"{20000 + indice * 1500}.25") for indice in range(_M)]
    assert len(set(cierres)) == _M
    for indice, close in enumerate(cierres):
        assert (
            persistir_vela(
                _vela(indice, close),
                MarketCandleEventType.CANDLE_CLOSED,
                _OPEN + indice,
            )
            is True
        )
    ultimo_open_time = _OPEN + (_M - 1) * _TF.duration_ms

    # 2. El plan sale del compilador REAL contra el catalogo VIVO del worker
    #    (build_catalog: discovery + validate + cache_key). El source_id que se afirma
    #    mas abajo viene del plan, no de una constante escrita a mano en el assert.
    plan = compile(_regla_que_pide_m_barras(), build_catalog())
    resueltas = {fuente.source_id: fuente for fuente in plan.resolved_sources}
    assert MARKET_CLOSE_SOURCE_ID in resueltas
    assert resueltas[MARKET_CLOSE_SOURCE_ID].history_bars == _M

    # 3. LA LLAMADA QUE CIERRA EL HUECO: _series_for con una Session del rol de REGLAS.
    #    Aqui es donde el dispatch por SOURCE_ID elige read_close_window; si enrutara a
    #    UnwiredSourceError o a un spec de footprint, esta linea revienta o devuelve
    #    otra cosa.
    with rules_db.transaction() as session:
        series = _series_for(session, plan, _TF.value, ultimo_open_time)

    # 4. El Series lleva la fuente del PLAN y su serie es la ventana de cierres exacta,
    #    en el CARRIER (tuple[ScalarValue, ...], D1): market.close se envuelve en
    #    composition._materialize igual que cualquier otra fuente DECIMAL del registro.
    for fuente in plan.resolved_sources:
        assert fuente.source_id in series, fuente.source_id
    serie = series[MARKET_CLOSE_SOURCE_ID]
    assert serie != ()
    assert len(serie) == _M
    for valor, cierre in zip(serie, cierres, strict=True):
        # ScalarValue DECIMAL con el Decimal REAL, no float ni None: el cierre es
        # dinero y un binario perderia digitos en silencio antes de llegar al
        # evaluador. Bit a bit, no solo == (dos Decimal iguales pueden diferir en
        # exponente).
        assert isinstance(valor, ScalarValue)
        assert valor.scalar_type is ScalarType.DECIMAL
        assert valor.decimal_value == cierre
        assert not isinstance(valor.decimal_value, bool)
        assert str(valor.decimal_value) == str(cierre)
