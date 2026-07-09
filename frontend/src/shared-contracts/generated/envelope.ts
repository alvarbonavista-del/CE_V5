// Generado desde contracts/schemas. NO editar a mano (ADR-006).

export type CausationId = (string | null)
export type CorrelationId = string
export type EnvelopeVersion = number
export type EventId = string
export type EventSchemaVersion = number
export type EventTime = (number | null)
export type EventType = string
export type IdempotencyKey = string
export type IngestionTime = (number | null)
export type ProcessingTime = (number | null)
/**
 * Alcance del evento (ADR-003).
 */
export type Scope = ("public_market" | "tenant" | "user" | "system")
export type Source = string
export type SourceEventId = (string | null)
export type SourceSequence = (number | null)
export type StreamKey = string
export type TenantId = (string | null)
export type TimeAnchorRef = (string | null)
export type UserId = (string | null)

export interface Envelope {
causation_id?: CausationId
correlation_id: CorrelationId
envelope_version?: EnvelopeVersion
event_id?: EventId
event_schema_version: EventSchemaVersion
event_time?: EventTime
event_type: EventType
idempotency_key: IdempotencyKey
ingestion_time?: IngestionTime
payload: EventPayload
processing_time?: ProcessingTime
scope: Scope
source: Source
source_event_id?: SourceEventId
source_sequence?: SourceSequence
stream_key: StreamKey
tenant_id?: TenantId
time_anchor_ref?: TimeAnchorRef
user_id?: UserId
}
/**
 * Raiz tipada de todo payload de evento. Sin campos en P01.
 */
export interface EventPayload {

}
