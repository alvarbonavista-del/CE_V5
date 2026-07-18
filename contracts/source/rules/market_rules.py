"""Especializaciones de mercado: AlertRule y TradingSignalRule (INFORME 6 sec 11).

UNA maquinaria (la raiz Rule, neutral), DOS productos. Estas son las UNICAS clases
que anaden mercado (market_scope): la trading-ness vive en la hoja, jamas en la raiz
(criterio 4). Por eso este fichero SI importa el vocabulario de mercado, a diferencia
de la raiz.

- AlertRule: producto AVISAR (sec 11.1). Consumidor: el pipeline de notificacion
  (P09a). Proyectara alert.*.
- TradingSignalRule: producto SENALAR TRADING (sec 11.1). Consumidor: el overlay
  grafico universal. Proyectara signal.*. Puede llevar notification_policy_ref
  opcional (senala Y avisa, sec 11.2).

MarketRule es el factor comun de mercado del que cuelgan los dos productos; NO es
persistible por si mismo: las entidades persistidas son SIEMPRE AlertRule o
TradingSignalRule, discriminadas por 'product'. product es OBLIGATORIO y sin default:
un discriminador que exclude_defaults pudiera borrar romperia la deserializacion.

market_scope es {exchange, symbol} (sec 10.3); el market_type (spot) y data_kind
(candles) de v5.0 y el timeframe (evaluation_context del grupo) los combina el runtime
para formar la MarketStreamKey de la suscripcion (ADR-014).

COHERENCIA binding_kind. La raiz declara target_binding.binding_kind; en v5.0 su unico
valor es 'market', asi que la coherencia con market_scope es automatica y NO se valida
aqui (seria codigo inalcanzable). OBLIGACION FUTURA: cuando BindingKind gane un valor
no de mercado, estas hojas DEBEN anadir un validador binding_kind==market.
"""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from source.families.market import EXCHANGE_PATTERN, SYMBOL_PATTERN
from source.rules.rule import Rule


class RuleProduct(StrEnum):
    """Los dos productos v5.0 de la maquinaria unica (INFORME 6 sec 11.1)."""

    ALERT = "alert"
    TRADING_SIGNAL = "trading_signal"


class MarketScope(BaseModel):
    """Binding concreto de mercado: exchange + simbolo canonico BASE-QUOTE.

    Mismo patron canonico que market.* : la traduccion a la forma nativa del exchange
    es del adaptador (P07), no de la regla.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    exchange: str = Field(pattern=EXCHANGE_PATTERN)
    symbol: str = Field(pattern=SYMBOL_PATTERN)


class MarketRule(Rule):
    """Factor comun de mercado de los dos productos (no persistible por si solo).

    Anade market_scope y el ref opcional a la politica de notificacion (entidad de
    P09a; aqui opaco). Las entidades reales son sus subclases Alert/TradingSignalRule.
    """

    market_scope: MarketScope
    notification_policy_ref: UUID | None = None


class AlertRule(MarketRule):
    """Producto AVISAR: proyectara alert.* (INFORME 6 sec 11.1)."""

    product: Literal[RuleProduct.ALERT]


class TradingSignalRule(MarketRule):
    """Producto SENALAR TRADING: proyectara signal.* (INFORME 6 sec 11.1)."""

    product: Literal[RuleProduct.TRADING_SIGNAL]


# Union discriminada por 'product': deserializa una regla persistida al producto
# correcto. Solo las dos hojas son persistibles; Rule y MarketRule son base.
AnyRule = Annotated[AlertRule | TradingSignalRule, Field(discriminator="product")]
RULE_ADAPTER: TypeAdapter[AnyRule] = TypeAdapter(AnyRule)
