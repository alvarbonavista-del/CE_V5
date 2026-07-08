# REGISTRO DE HITOS - Crypto Engine V5

Archivo vivo (sin logica). Mantenido por Claude Code; Alvaro lo resube
al knowledge al cerrar cada hito (DOC_ENTREGABLES sec.8).

Ultima actualizacion: 2026-07-08.

| Hito | Definicion breve (DOC_ROADMAP sec.4) | Piezas | Estado |
|------|--------------------------------------|--------|--------|
| M0 | Repo creado + CI de guardarrailes en verde (base estructural) | P00 | EN CURSO |
| M1 | Un evento viaja de punta a punta con envelope, idempotencia y Clock sobre el bus externo, con outbox transaccional; reinicio sin perdida | P01, P02, P02b, P03 | PENDIENTE |
| M2 | Un Componente se descubre por carpeta, aislado por tenant/RLS, con capacidades por el gate fail-closed; API/auth/realtime en pie; kill switch en caliente | P04, P05, P06, P06b | PENDIENTE |
| M3 | Una Rule dispara sobre datos reales y proyecta signal.*/alert.*; el router backend entrega por un canal no-PWA/mock (sin overlay, sin ejecucion) | P07, P08, P09a | PENDIENTE |
| M4 | PWA instalable con dashboard, chart y overlays de signal.* en movil real; push PWA; geo-blocking corta ejecucion, no visualizacion | P12a, P12b, P13, P09b | PENDIENTE |
| M5 | Ejecucion gateada: bloqueo UE/EEA/UK, orden manual BYOC, autotrade BYOC, reconciliacion | P10a, P10b, P11 | PENDIENTE |
