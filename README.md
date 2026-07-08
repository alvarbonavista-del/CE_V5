# Crypto Engine V5 (ce_v5)

Monorepo de Crypto Engine V5: plataforma multiusuario de analisis
cuantitativo sobre mercados de criptomonedas (web y PWA instalable).
CE v5 NO es un bot de trading: el trading es una capacidad gateada
(opcional, solo BYOC, solo donde la regulacion lo permite), no el eje.

## Estado
Pieza P00 - Esqueleto de repositorio + CI base. El repo arranca sin
logica de negocio, pero con estructura y guardarrailes vivos desde el
primer commit.

## Estructura (DOC_ESTRUCTURA sec.3)
- backend/   nucleo, componentes, plataforma, entrypoints, infra
- contracts/ contratos (se puebla en P01)
- frontend/  capas de cliente (se puebla desde P12a)
- docs/      documentos-norte firmados y estado de construccion
- tools/     scripts de checks de CI
- infra/     docker/compose/ci (estructural)
