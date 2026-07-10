# Componente sample

Componente de muestra de Crypto Engine V5. Su unico proposito es demostrar el
sustrato de Componentes de P04 (ADR-001/008/009/010): se descubre por carpeta,
su manifest se valida antes de cargar codigo, y el supervisor lo lleva por el
lifecycle emitiendo eventos component.* observables. No realiza trabajo de
dominio; solo lleva una bandera de si esta en marcha. Sirve de referencia
minima de como se escribe un Componente ("copiar carpeta + reiniciar", CE-14).
