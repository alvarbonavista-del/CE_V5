# Componente market_ingestor_public

Ingestor de market data PUBLICO de Crypto Engine V5. Trae las velas de los exchanges
(flujos publicos compartidos cross-tenant, ADR-014), las convierte en hechos del
sistema y las publica: las cerradas y sus correcciones por outbox transaccional, y las
provisionales directas al bus como vista viva.

Es el PRIMER Componente real sobre el sustrato de P04 (ADR-001/008/009/010): hasta
ahora solo existia el demostrador `sample`. Como cualquier Componente, se descubre POR
CARPETA, su manifest se valida ANTES de que se cargue una sola linea de su codigo, y el
supervisor lo lleva por el lifecycle emitiendo eventos `component.*` observables
("copiar carpeta + reiniciar", CE-14).

## Que hace en cada ciclo

`tick()` hace dos cosas y en este orden:

1. **Reconcilia la demanda**: pregunta cuantos sujetos quieren cada flujo publico y
   ajusta los streams abiertos contra el exchange. Un flujo que nadie pide se cierra
   (con histeresis, para no castigar al exchange con parpadeos); un flujo que alguien
   pide se abre al instante.
2. **Drena el feed**: procesa lo que haya llegado, valida cada vela en la frontera de
   confianza y persiste o publica segun corresponda.

Una excepcion en un ciclo NO mata el componente: se marca el ciclo como degradado y el
siguiente reintenta. Un worker que muere por un fallo transitorio deja de ingerir para
TODOS los usuarios.

## Reconexion + bootstrap, de forma autonoma

La reconexion y el bootstrap REST tras ella (ADR-014) se realizan SOLOS dentro del
bucle del componente, sin intervencion externa. El conector detecta cada reconexion y
deja una senal por stream; el motor (`drain_once`, que el componente ejecuta en cada
`tick()`) recoge esa senal (`drain_reconnected`) y dispara el bootstrap por el MISMO
camino de normalizacion y dedup que las velas del feed: rellena el hueco que hubo
mientras el socket estuvo caido, y el dedup absorbe el solape con lo ya persistido (no
se pierde ni se duplica). Es **fault isolation por stream**: el bootstrap fallido de un
stream se cuenta (`bootstrap_errors`) y se salta, sin tumbar el ciclo ni a los demas.

## Su cerebro se cablea fuera

El componente NO construye nada: recibe el `SubscriptionManager`, el `IngestionEngine`
y el feed del exchange YA CONSTRUIDOS, por el constructor. Los declara como puertos
minimos (Protocol) en su propio modulo, porque `components`, `platform` e `infra` son
capas HERMANAS e independientes y no pueden verse entre si. Quien las une es el
composition root (`entrypoints`), la unica capa autorizada a conocerlas todas.

Gracias a eso, el dia que haya un segundo exchange o un ingestor privado BYOC, este
fichero no se toca: se cablea otra cosa.

## Lo que NO hace

No evalua reglas, no genera senales y no ejecuta ordenes. Solo trae datos y los
convierte en hechos.
