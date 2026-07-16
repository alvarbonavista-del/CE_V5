"""Conector de Bybit v5 Spot: feed PUBLICO (ADR-014).

Solo datos publicos: velas y catalogo. CERO credenciales (BYOC es P10a).

Reparto de responsabilidades (mismo molde que Binance/OKX):
- symbols.py   : canonico <-> nativo y timeframe -> codigo/topic de Bybit. Sin IO.
- translate.py : vela WS (objeto) y vela REST (array) -> RawCandle. Sin IO.
- pool.py      : reparto de suscripciones entre conexiones (capa IO, pendiente).
- connector.py : IO real WebSocket + REST (no se prueba en CI, 5.18; pendiente).

DIFERENCIAS DE BYBIT (verificadas contra la doc vigente):
- WS publico spot: wss://stream.bybit.com/v5/public/spot.
- Simbolo PEGADO (BTCUSDT), como Binance: la vuelta nativo->canonico se CONSULTA al
  catalogo (este connector SI implementa SymbolMapSink, a diferencia de OKX).
- Suscripcion por topic kline.{interval}.{symbol}, intervalo en codigo propio
  (1, 5, 15, 60, 240, D).
- La vela WS es un OBJETO con campos nombrados: trae start, end, timestamp (event_time
  del push) y confirm (bool). El REST es un array de 7 campos, historico. Dos formas.
- Keep-alive: el cliente envia JSON {"op":"ping"} cada 20 s (no texto como OKX).
"""
