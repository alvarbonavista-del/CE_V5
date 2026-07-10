"""ComponentManifest tipado y su validacion estatica (ADR-008).

El manifest es el contrato de capacidades de un Componente: modelo Pydantic
v2 que es fuente de autoria y validacion, y se serializa a JSON/YAML por
componente para el discovery (ADR-009). Aqui vive la CAPA ESTATICA de
validacion (estructura, campos requeridos, formas bien construidas). La
capa SEMANTICA (dependencias resolubles, existencia de los schemas
referenciados, permisos/flags) la aplican el discovery y el registro
(Bloques 4 y 5) y los checks 7.5/7.6 (Bloque 6). Minimo obligatorio
(ADR-008): id, version, manifest_schema_version, type.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from source.families import validate_event_type

# Version del formato del manifest (ADR-008: manifest_schema_version).
# Evoluciona bajo ADR-005, independiente del envelope y de los payloads.
MANIFEST_SCHEMA_VERSION = 1


class ComponentType(StrEnum):
    """Vocabulario controlado de tipos de Componente (ADR-008).

    "Enum abierto": la lista crece subiendo manifest_schema_version (cambio
    gobernado), nunca admitiendo texto libre. Sembrado con los tipos que
    nombran los ADRs (ADR-001/002/008).
    """

    ENGINE = "engine"
    WORKER = "worker"
    CONNECTOR = "connector"
    NOTIFICATION_PROVIDER = "notification_provider"
    AUTH_PROVIDER = "auth_provider"
    EXPORTER = "exporter"
    UI_PLUGIN = "ui_plugin"


class CapabilityKind(StrEnum):
    """Clases de capability (ADR-008), extensibles via 'custom'."""

    DATASOURCE = "datasource"
    NOTIFICATION_CHANNEL = "notification_channel"
    CONNECTOR = "connector"
    UI = "ui"
    EXPORTER = "exporter"
    AUTH = "auth"
    EXECUTION = "execution"
    CUSTOM = "custom"


class SchemaRef(BaseModel):
    """Referencia a un contrato de evento en shared-contracts (ADR-004/008)."""

    model_config = ConfigDict(extra="forbid")

    event_type: str
    event_schema_version: int = Field(ge=1)

    @field_validator("event_type")
    @classmethod
    def _event_type_valido(cls, value: str) -> str:
        return validate_event_type(value)


class Requires(BaseModel):
    """Dependencias que el Componente declara necesitar (ADR-008)."""

    model_config = ConfigDict(extra="forbid")

    clock: bool = False
    database: bool = False
    event_bus: bool = False
    services: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()


class Capability(BaseModel):
    """Capability generica y extensible (ADR-008).

    P04 garantiza que este bien formada; el DETALLE concreto (datasources,
    notificacion, ejecucion...) lo valida la pieza duena contra schema_ref.
    'name' solo aplica a kind=custom.
    """

    model_config = ConfigDict(extra="forbid")

    kind: CapabilityKind
    version: int = Field(ge=1)
    name: str | None = None
    schema_ref: str | None = None
    detail: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _custom_exige_nombre(self) -> "Capability":
        if self.kind is CapabilityKind.CUSTOM and not self.name:
            raise ValueError("kind=custom exige 'name'.")
        if self.kind is not CapabilityKind.CUSTOM and self.name is not None:
            raise ValueError("'name' solo aplica a kind=custom.")
        return self


class UiDeclaration(BaseModel):
    """Superficies de UI que aporta el Componente (ADR-008)."""

    model_config = ConfigDict(extra="forbid")

    panel: bool = False
    widget: bool = False
    config_screen: bool = False
    supported_surfaces: tuple[str, ...] = ()


class PolicyRequirements(BaseModel):
    """Lo que el Componente NECESITA de la politica (ADR-008).

    Declara requisitos; NO evalua (eso es el PolicyEvaluator, P06).
    """

    model_config = ConfigDict(extra="forbid")

    permissions_required: tuple[str, ...] = ()
    feature_flags_required: tuple[str, ...] = ()
    entitlements_required: tuple[str, ...] = ()
    sensitive_capabilities: tuple[str, ...] = ()


class ComponentManifest(BaseModel):
    """Manifest tipado de un Componente (ADR-008). Inmutable, sin extras."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Identidad: minimo obligatorio (ADR-008).
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    manifest_schema_version: int = Field(ge=1)
    type: ComponentType
    # Entrypoint declarado que el discovery carga tras validar (ADR-009).
    # Su ausencia la detecta el check de huerfanos 7.6 (Bloque 6).
    entrypoint: str | None = None

    # Bloques obligatorio-si-aplica; por defecto vacios (ADR-008).
    produces: tuple[SchemaRef, ...] = ()
    consumes: tuple[SchemaRef, ...] = ()
    requires: Requires = Field(default_factory=Requires)
    capabilities: tuple[Capability, ...] = ()
    ui: UiDeclaration | None = None
    policy_requirements: PolicyRequirements = Field(default_factory=PolicyRequirements)
    config_schema: dict[str, object] | None = None

    @field_validator("entrypoint")
    @classmethod
    def _entrypoint_no_vacio(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("entrypoint no puede ser cadena vacia.")
        return value


def validate_manifest(data: object) -> ComponentManifest:
    """Validacion estatica de un manifest (ADR-008, capa 1).

    Construye el ComponentManifest desde data (un dict cargado de JSON/YAML).
    Lanza pydantic.ValidationError si la estructura, los campos requeridos o
    las formas no cumplen. NO comprueba existencia de schemas referenciados
    ni resolubilidad de dependencias: eso es la capa semantica (Bloques 4-6).
    """
    return ComponentManifest.model_validate(data)
