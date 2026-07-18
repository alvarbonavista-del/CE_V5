"""Grupo: features en un contexto de evaluacion (INFORME 6 sec 10.3, 10.5).

Un grupo reune 1..M features y declara:
- evaluation_context: EN QUE contexto se evalua. Es NEUTRAL en la raiz (un token
  opaco); cada especializacion lo interpreta -- en reglas de MERCADO es el timeframe
  ('1m', '1h', ...), en otras seria la ventana/el contexto que aplique. La raiz NO
  sabe que es un timeframe (criterio 4: el mercado vive en la hoja, no en la raiz);
  que el token sea un contexto valido para las fuentes referenciadas lo valida el
  Bloque 3 contra el catalogo (INFORME 6 sec 12.2). Por eso el contrato raiz NO
  importa Timeframe: leerlo aqui seria colar mercado en la raiz.
- combine_mode: como se combinan sus features (all/any). OBLIGATORIO y explicito
  (misma politica que la feature; el default 'all' lo aplica el normalizador).
- domain_label: etiqueta OPCIONAL de dominio (flujo/momentum/estructura de la
  plantilla curada), METADATO sin efecto en la evaluacion (INFORME 6 sec 10.3, 10.6);
  la regla fractal fija de 3 dominios de v4 se abandono (decision A2).

Los HARD CAPS (M features por grupo, N grupos por regla) los valida el Bloque 3, no el
contrato; el contrato exige lo ESTRUCTURAL: al menos una feature. El orden canonico de
las features lo fija el normalizador (1.11) por contenido.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from source.rules.feature import Feature
from source.rules.vocab import CombineMode

# Token neutral del contexto de evaluacion. En mercado sera el timeframe; el contrato
# raiz no importa Timeframe (eso leeria mercado en la raiz). Bounded, en minusculas.
EVALUATION_CONTEXT_PATTERN = r"^[a-z0-9][a-z0-9_]{0,31}$"


class Group(BaseModel):
    """1..M features en un contexto de evaluacion, con modo e id de nodo estable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: UUID
    evaluation_context: str = Field(pattern=EVALUATION_CONTEXT_PATTERN)
    combine_mode: CombineMode
    features: tuple[Feature, ...] = Field(min_length=1)
    domain_label: str | None = Field(default=None, max_length=64)
