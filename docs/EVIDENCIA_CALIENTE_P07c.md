# Evidencia CRUDA de la validacion en caliente del libro L2 (P07c)

Salida VERBATIM de la validacion en caliente (5.32) y del diagnostico de siembra, contra
los TRES exchanges reales, SOLO LECTURA de feed publico. El CI es hermetico (5.18): esto
NO se ejecuta en CI; se corre en caliente y su evidencia se guarda AQUI (no solo resumida
en los informes de tanda). Herramientas: `tools/validate_orderbook_live.py` y
`tools/diag_binance_seed_window.py`. Runs de 2026-07-24.

Que demuestra cada bloque:
- reconstruccion por secuencia (is_complete=True, best bid/ask, num niveles, ultima seq);
- discontinuidad simulada -> is_complete=False + RESYNC publicado -> recuperacion por
  re-siembra -> is_complete=True;
- OKX: keepalive/mantenimiento NO disparan resync (resyncs durante mantenimiento=0);
- Bybit: RESET por u==1 (cold test) + siembra por snapshot WS;
- metricas b-i/b-ii: deltas/seg y coste de CPU por simbolo.

---

## 1. Binance (.vision) -- validacion en caliente del libro

```

######################################################################
# LIBRO L2 EN CALIENTE -- BINANCE (BTC-USDT)
######################################################################

=== FASE 0: abrir canal de libro + sembrar (binance) ===
  abierto market:orderbook:binance:spot:BTC-USDT; activos=['market:orderbook:binance:spot:BTC-USDT']
  intento 1: el libro no llego a COMPLETO.
  [SEMBRADO/ESTABLE (tras 1 re-siembra/s)] is_complete=True seq=97831306006 niveles=234+196 best_bid=(Decimal('64270.00000000'), Decimal('7.76559000')) best_ask=(Decimal('64270.01000000'), Decimal('5.78969000')) deltas=943 resyncs=1 reseeds=1 reconn=1
  [MANTENIMIENTO] is_complete=True seq=97831349564 niveles=626+583 best_bid=(Decimal('64255.28000000'), Decimal('2.80505000')) best_ask=(Decimal('64255.29000000'), Decimal('3.17992000')) deltas=1443 resyncs=1 reseeds=1 reconn=1
  [FASE 1 OK] 500 deltas en 49.8s (10.0 deltas/seg); is_complete=True; resyncs durante mantenimiento=0 (keepalive/mant. OKX no disparan resync: OK)

=== FASE 2: discontinuidad simulada (descartar deltas) ===
  [ANTES-HUECO] is_complete=True seq=97831349564 niveles=626+583 best_bid=(Decimal('64255.28000000'), Decimal('2.80505000')) best_ask=(Decimal('64255.29000000'), Decimal('3.17992000')) deltas=1443 resyncs=1 reseeds=1 reconn=1
  descartando deltas 3s para romper la secuencia...
  [TRAS-HUECO] is_complete=False seq=97831349564 niveles=626+583 best_bid=(Decimal('64255.28000000'), Decimal('2.80505000')) best_ask=(Decimal('64255.29000000'), Decimal('3.17992000')) deltas=1599 resyncs=2 reseeds=1 reconn=1
  descartados=30; is_complete->False=True; RESYNC publicado=True (resyncs 1->2, writer.resyncs=2)

=== FASE 3: recuperacion (reconexion real -> re-siembra) ===
  force_reconnect_all: cerro 1 conexion(es); espero foto fresca
  [RECUPERADO] is_complete=True seq=97831360607 niveles=117+120 best_bid=(Decimal('64277.53000000'), Decimal('7.47783000')) best_ask=(Decimal('64277.54000000'), Decimal('1.40595000')) deltas=1646 resyncs=2 reseeds=2 reconn=2
  reseeds=1; is_complete->True=True; discontinuidades_apuntadas=2

=== FASE 4: METRICAS (b-i/b-ii, cond.6) ===
  deltas/seg (mantenimiento) = 10.0
  CPU de mantenimiento       = 109.4 ms de CPU en 49.8s de reloj -> 0.22% de un core
  coste por delta            = 218.8 us de CPU/delta (500 deltas)
  resyncs=2 reseeds=2 discontinuidades=2 rechazos={}

[BINANCE] VALIDACION EN CALIENTE DEL LIBRO: OK

CONECTOR DETENIDO (hilo de fondo parado).
=========== EXIT BINANCE: 0 ===========
```

---

## 2. Binance (.vision) -- diagnostico de siembra TRAS el fix de pre-buffer (8 siembras)

`buffer_tras_foto` pasa de 0 (antes del fix, ventana perdida) a 10-11 (WS bufferizado
antes de la foto); `is_complete=True` y `resync=False` en las 8 -> siembra limpia al PRIMER
intento. Antes del fix eran ~2/8 limpias.

```
DIAGNOSTICO ventana de siembra Binance (.vision) x8

[1] t_seed-t_open=2104ms buffer_tras_foto=11 deltas | lastUpdateId=97833408582
    d00 U=97833408508 u=97833408511 -> DUP
    d01 U=97833408512 u=97833408516 -> DUP
    d02 U=97833408517 u=97833408517 -> DUP
    d03 U=97833408518 u=97833408519 -> DUP
    d04 U=97833408520 u=97833408527 -> DUP
    d05 U=97833408528 u=97833408546 -> DUP
    d06 U=97833408547 u=97833408554 -> DUP
    d07 U=97833408555 u=97833408562 -> DUP
    d08 U=97833408563 u=97833408578 -> DUP
    d09 U=97833408579 u=97833408579 -> DUP
    d10 U=97833408580 u=97833408582 -> DUP
    d11 U=97833408583 u=97833408588 -> APPLY
[1] primer_gap_en_delta=None | is_complete_final=True resync=False | encadena exacto (U==L+1): sin ventana perdida

[2] t_seed-t_open=2101ms buffer_tras_foto=11 deltas | lastUpdateId=97833409030
    d00 U=97833408853 u=97833408858 -> DUP
    d01 U=97833408859 u=97833408906 -> DUP
    d02 U=97833408907 u=97833408942 -> DUP
    d03 U=97833408943 u=97833408967 -> DUP
    d04 U=97833408968 u=97833408987 -> DUP
    d05 U=97833408988 u=97833408989 -> DUP
    d06 U=97833408990 u=97833408994 -> DUP
    d07 U=97833408995 u=97833409004 -> DUP
    d08 U=97833409005 u=97833409012 -> DUP
    d09 U=97833409013 u=97833409019 -> DUP
    d10 U=97833409020 u=97833409030 -> DUP
    d11 U=97833409031 u=97833409036 -> APPLY
[2] primer_gap_en_delta=None | is_complete_final=True resync=False | encadena exacto (U==L+1): sin ventana perdida

[3] t_seed-t_open=1988ms buffer_tras_foto=10 deltas | lastUpdateId=97833409448
    d00 U=97833409321 u=97833409323 -> DUP
    d01 U=97833409324 u=97833409327 -> DUP
    d02 U=97833409328 u=97833409331 -> DUP
    d03 U=97833409332 u=97833409334 -> DUP
    d04 U=97833409335 u=97833409345 -> DUP
    d05 U=97833409346 u=97833409376 -> DUP
    d06 U=97833409377 u=97833409401 -> DUP
    d07 U=97833409402 u=97833409421 -> DUP
    d08 U=97833409422 u=97833409438 -> DUP
    d09 U=97833409439 u=97833409448 -> DUP
    d10 U=97833409449 u=97833409465 -> APPLY
    d11 U=97833409466 u=97833409470 -> APPLY
[3] primer_gap_en_delta=None | is_complete_final=True resync=False | encadena exacto (U==L+1): sin ventana perdida

[4] t_seed-t_open=1959ms buffer_tras_foto=10 deltas | lastUpdateId=97833410002
    d00 U=97833409840 u=97833409849 -> DUP
    d01 U=97833409850 u=97833409878 -> DUP
    d02 U=97833409879 u=97833409897 -> DUP
    d03 U=97833409898 u=97833409907 -> DUP
    d04 U=97833409908 u=97833409915 -> DUP
    d05 U=97833409916 u=97833409931 -> DUP
    d06 U=97833409932 u=97833409942 -> DUP
    d07 U=97833409943 u=97833409969 -> DUP
    d08 U=97833409970 u=97833409992 -> DUP
    d09 U=97833409993 u=97833410002 -> DUP
    d10 U=97833410003 u=97833410028 -> APPLY
    d11 U=97833410029 u=97833410045 -> APPLY
[4] primer_gap_en_delta=None | is_complete_final=True resync=False | encadena exacto (U==L+1): sin ventana perdida

[5] t_seed-t_open=2060ms buffer_tras_foto=10 deltas | lastUpdateId=97833410497
    d00 U=97833410358 u=97833410375 -> DUP
    d01 U=97833410376 u=97833410390 -> DUP
    d02 U=97833410391 u=97833410413 -> DUP
    d03 U=97833410414 u=97833410421 -> DUP
    d04 U=97833410422 u=97833410441 -> DUP
    d05 U=97833410442 u=97833410451 -> DUP
    d06 U=97833410452 u=97833410453 -> DUP
    d07 U=97833410454 u=97833410464 -> DUP
    d08 U=97833410465 u=97833410485 -> DUP
    d09 U=97833410486 u=97833410497 -> DUP
    d10 U=97833410498 u=97833410519 -> APPLY
    d11 U=97833410520 u=97833410533 -> APPLY
[5] primer_gap_en_delta=None | is_complete_final=True resync=False | encadena exacto (U==L+1): sin ventana perdida

[6] t_seed-t_open=2092ms buffer_tras_foto=11 deltas | lastUpdateId=97833411074
    d00 U=97833410889 u=97833410896 -> DUP
    d01 U=97833410897 u=97833410909 -> DUP
    d02 U=97833410910 u=97833410914 -> DUP
    d03 U=97833410915 u=97833410915 -> DUP
    d04 U=97833410916 u=97833410926 -> DUP
    d05 U=97833410927 u=97833410957 -> DUP
    d06 U=97833410958 u=97833410976 -> DUP
    d07 U=97833410977 u=97833411010 -> DUP
    d08 U=97833411011 u=97833411045 -> DUP
    d09 U=97833411046 u=97833411074 -> DUP
    d10 U=97833411075 u=97833411085 -> APPLY
    d11 U=97833411086 u=97833411088 -> APPLY
[6] primer_gap_en_delta=None | is_complete_final=True resync=False | encadena exacto (U==L+1): sin ventana perdida

[7] t_seed-t_open=2121ms buffer_tras_foto=11 deltas | lastUpdateId=97833411480
    d00 U=97833411387 u=97833411390 -> DUP
    d01 U=97833411391 u=97833411393 -> DUP
    d02 U=97833411394 u=97833411398 -> DUP
    d03 U=97833411399 u=97833411402 -> DUP
    d04 U=97833411403 u=97833411405 -> DUP
    d05 U=97833411406 u=97833411423 -> DUP
    d06 U=97833411424 u=97833411441 -> DUP
    d07 U=97833411442 u=97833411452 -> DUP
    d08 U=97833411453 u=97833411473 -> DUP
    d09 U=97833411474 u=97833411475 -> DUP
    d10 U=97833411476 u=97833411480 -> DUP
    d11 U=97833411481 u=97833411482 -> APPLY
[7] primer_gap_en_delta=None | is_complete_final=True resync=False | encadena exacto (U==L+1): sin ventana perdida

[8] t_seed-t_open=2074ms buffer_tras_foto=10 deltas | lastUpdateId=97833411759
    d00 U=97833411663 u=97833411673 -> DUP
    d01 U=97833411674 u=97833411690 -> DUP
    d02 U=97833411691 u=97833411705 -> DUP
    d03 U=97833411706 u=97833411709 -> DUP
    d04 U=97833411710 u=97833411717 -> DUP
    d05 U=97833411718 u=97833411718 -> DUP
    d06 U=97833411719 u=97833411723 -> DUP
    d07 U=97833411724 u=97833411744 -> DUP
    d08 U=97833411745 u=97833411754 -> DUP
    d09 U=97833411755 u=97833411759 -> DUP
    d10 U=97833411760 u=97833411763 -> APPLY
    d11 U=97833411764 u=97833411770 -> APPLY
[8] primer_gap_en_delta=None | is_complete_final=True resync=False | encadena exacto (U==L+1): sin ventana perdida
```

---

## 3. OKX -- validacion en caliente del libro por /ws/v5/public (2a conexion, Tanda VI)

Siembra LIMPIA al primer intento (400+400 niveles, sin re-siembra), keepalive/mantenimiento
sin resync, discontinuidad -> resync -> recuperacion.

```

######################################################################
# LIBRO L2 EN CALIENTE -- OKX (BTC-USDT)
######################################################################

=== FASE 0: abrir canal de libro + sembrar (okx) ===
  abierto market:orderbook:okx:spot:BTC-USDT; activos=['market:orderbook:okx:spot:BTC-USDT']
  [SEMBRADO/ESTABLE] is_complete=True seq=79175591801 niveles=400+400 best_bid=(Decimal('64264'), Decimal('1.10673002')) best_ask=(Decimal('64264.1'), Decimal('1.80499712')) deltas=250 resyncs=0 reseeds=0 reconn=0
  [MANTENIMIENTO] is_complete=True seq=79175592137 niveles=400+400 best_bid=(Decimal('64264'), Decimal('1.10662539')) best_ask=(Decimal('64264.1'), Decimal('1.80495978')) deltas=267 resyncs=0 reseeds=0 reconn=0
  [MANTENIMIENTO] is_complete=True seq=79175597619 niveles=400+400 best_bid=(Decimal('64259.8'), Decimal('0.08204713')) best_ask=(Decimal('64259.9'), Decimal('14.43465662')) deltas=387 resyncs=0 reseeds=0 reconn=0
  [MANTENIMIENTO] is_complete=True seq=79175600407 niveles=400+400 best_bid=(Decimal('64254.5'), Decimal('0.06168659')) best_ask=(Decimal('64254.6'), Decimal('13.9437341')) deltas=468 resyncs=0 reseeds=0 reconn=0
  [FASE 1 OK] 218 deltas en 23.5s (9.3 deltas/seg); is_complete=True; resyncs durante mantenimiento=0 (keepalive/mant. OKX no disparan resync: OK)

=== FASE 2: discontinuidad simulada (descartar deltas) ===
  [ANTES-HUECO] is_complete=True seq=79175600407 niveles=400+400 best_bid=(Decimal('64254.5'), Decimal('0.06168659')) best_ask=(Decimal('64254.6'), Decimal('13.9437341')) deltas=468 resyncs=0 reseeds=0 reconn=0
  descartando deltas 3s para romper la secuencia...
  [TRAS-HUECO] is_complete=False seq=79175600407 niveles=400+400 best_bid=(Decimal('64254.5'), Decimal('0.06168659')) best_ask=(Decimal('64254.6'), Decimal('13.9437341')) deltas=588 resyncs=1 reseeds=0 reconn=0
  descartados=30; is_complete->False=True; RESYNC publicado=True (resyncs 0->1, writer.resyncs=1)

=== FASE 3: recuperacion (reconexion real -> re-siembra) ===
  force_reconnect_all: cerro 1 conexion(es); espero foto fresca
  [RECUPERADO] is_complete=True seq=79175586022 niveles=400+400 best_bid=(Decimal('64264'), Decimal('1.12595047')) best_ask=(Decimal('64264.1'), Decimal('1.01321001')) deltas=588 resyncs=1 reseeds=1 reconn=1
  reseeds=1; is_complete->True=True; discontinuidades_apuntadas=1

=== FASE 4: METRICAS (b-i/b-ii, cond.6) ===
  deltas/seg (mantenimiento) = 9.3
  CPU de mantenimiento       = 46.9 ms de CPU en 23.5s de reloj -> 0.20% de un core
  coste por delta            = 215.0 us de CPU/delta (218 deltas)
  resyncs=1 reseeds=1 discontinuidades=1 rechazos={}

[OKX] VALIDACION EN CALIENTE DEL LIBRO: OK

CONECTOR DETENIDO (hilo de fondo parado).
```

---

## 4. Bybit (orderbook.200) -- validacion en caliente del libro

Siembra LIMPIA por snapshot WS al primer intento (profundidad fija 200), discontinuidad ->
resync -> recuperacion. El coste de CPU es el mas bajo de los tres (0.07% de un core).

```
######################################################################
# LIBRO L2 EN CALIENTE -- BYBIT (BTC-USDT)
######################################################################

=== FASE 0: abrir canal de libro + sembrar (bybit) ===
  abierto market:orderbook:bybit:spot:BTC-USDT; activos=['market:orderbook:bybit:spot:BTC-USDT']
  [SEMBRADO/ESTABLE] is_complete=True seq=50429107 niveles=200+200 best_bid=(Decimal('64135.5'), Decimal('0.159568')) best_ask=(Decimal('64135.6'), Decimal('1.304644')) deltas=52 resyncs=0 reseeds=0 reconn=0
  [MANTENIMIENTO] is_complete=True seq=50429202 niveles=200+200 best_bid=(Decimal('64138.9'), Decimal('1.225624')) best_ask=(Decimal('64139'), Decimal('0.405476')) deltas=147 resyncs=0 reseeds=0 reconn=0
  [MANTENIMIENTO] is_complete=True seq=50429285 niveles=200+200 best_bid=(Decimal('64138.9'), Decimal('0.974976')) best_ask=(Decimal('64139'), Decimal('0.486863')) deltas=230 resyncs=0 reseeds=0 reconn=0
  [FASE 1 OK] 209 deltas en 22.9s (9.1 deltas/seg); is_complete=True; resyncs durante mantenimiento=0 (keepalive/mant. OKX no disparan resync: OK)

=== FASE 2: discontinuidad simulada (descartar deltas) ===
  [ANTES-HUECO] is_complete=True seq=50429316 niveles=200+200 deltas=261 resyncs=0 reseeds=0 reconn=0
  descartando deltas 3s para romper la secuencia...
  [TRAS-HUECO] is_complete=False seq=50429316 niveles=200+200 deltas=297 resyncs=1 reseeds=0 reconn=0
  descartados=27; is_complete->False=True; RESYNC publicado=True (resyncs 0->1, writer.resyncs=1)

=== FASE 3: recuperacion (reconexion real -> re-siembra) ===
  force_reconnect_all: cerro 1 conexion(es); espero foto fresca
  [RECUPERADO] is_complete=True seq=50429431 niveles=200+200 best_bid=(Decimal('64152.8'), Decimal('1.058111')) best_ask=(Decimal('64152.9'), Decimal('0.104886')) deltas=336 resyncs=1 reseeds=1 reconn=1
  reseeds=1; is_complete->True=True; discontinuidades_apuntadas=1

=== FASE 4: METRICAS (b-i/b-ii, cond.6) ===
  deltas/seg (mantenimiento) = 9.1
  CPU de mantenimiento       = 15.6 ms de CPU en 22.9s de reloj -> 0.07% de un core
  coste por delta            = 74.8 us de CPU/delta (209 deltas)
  resyncs=1 reseeds=1 discontinuidades=1 rechazos={}

[BYBIT] VALIDACION EN CALIENTE DEL LIBRO: OK
```

NOTA Bybit RESET u==1: el mensaje type=snapshot con u==1 (reinicio del servicio de Bybit)
se prueba EN FRIO (tests/unit/platform/market/test_orderbook_book.py::TestReset): un u==1 /
is_snapshot reconstruye el libro y recupera de un resync SIN reconexion. En caliente es
raro (solo en reinicios del servicio del exchange); la recuperacion por reconexion de la
Fase 3 ejercita el mismo camino de re-siembra.

---

## 5. OKX -- sonda del canal 'books' en /business vs /public (hallazgo Tanda V)

Confirma por que hace falta la 2a conexion: 'books' da 60018 en /business y funciona en
/public.

```
[business] conectando a wss://ws.okx.com:8443/ws/v5/business
[business] eventos=["event=error code=60018 msg=Wrong URL or channel:books,instId:BTC-USDT doesn't exist. Please use the correct URL, channel and parameters referring to API document."]
[business] snapshots=0 updates=0 -> BOOKS NO LLEGA

[public] conectando a wss://ws.okx.com:8443/ws/v5/public
[public] eventos=['event=subscribe code=None msg=None']
[public] snapshots=1 updates=2 -> BOOKS OK
```

---

## 6. Binance -- diagnostico de siembra ANTES del fix (ventana perdida, para contraste)

`buffer_tras_foto=0` en las 8: la foto se pedia con el buffer VACIO -> 6/8 arrancaban con
`U>lastUpdateId+1` (ventana perdida, faltaban 1-17 deltas) -> GAP+resync; solo 2/8 limpias.
Es la CAUSA que el fix de la seccion 2 cierra.

```
[1] t_seed-t_open=1004ms buffer_tras_foto=0 deltas | lastUpdateId=97833173805
    d00 U=97833173806 u=97833173829 -> APPLY   (encadena exacto: sin ventana perdida)
[2] t_seed-t_open=950ms  buffer_tras_foto=0 deltas | lastUpdateId=97833174185
    d00 U=97833174192 u=97833174199 -> GAP     (VENTANA PERDIDA: U>L+1, faltan 6)
[3] buffer_tras_foto=0 | VENTANA PERDIDA (faltan 17)   -> is_complete=False resync=True
[4] buffer_tras_foto=0 | VENTANA PERDIDA (faltan 6)    -> is_complete=False resync=True
[5] buffer_tras_foto=0 | encadena exacto (U==L+1)      -> is_complete=True
[6] buffer_tras_foto=0 | VENTANA PERDIDA (faltan 1)    -> is_complete=False resync=True
[7] buffer_tras_foto=0 | VENTANA PERDIDA (faltan 1)    -> is_complete=False resync=True
[8] buffer_tras_foto=0 | VENTANA PERDIDA (faltan 17)   -> is_complete=False resync=True
RESULTADO: 2/8 limpias (antes del fix). Con el fix: 8/8 (seccion 2).
```
