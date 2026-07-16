"""Conector de OKX Spot: feed PUBLICO (ADR-014).

Solo datos publicos: velas y catalogo. CERO credenciales (las claves BYOC son P10a).

Reparto de responsabilidades, calcado del adaptador de Binance:
- symbols.py   : canonico <-> nativo y timeframe -> canal OKX. Sin IO.
- translate.py : array de vela de OKX -> RawCandle. Sin IO. El CI lo prueba a fondo.
- pool.py      : reparto de streams entre conexiones (capa de IO, pendiente).
- connector.py : IO real WebSocket + REST (no se prueba en CI, 5.18; pendiente).

DIFERENCIAS DE OKX FRENTE A BINANCE (verificadas contra la doc vigente):
- Las velas van por /ws/v5/business, NO por /ws/v5/public (migracion 20-jun-2023).
  Sin credenciales igualmente.
- El instId nativo YA es la forma canonica BASE-QUOTE (BTC-USDT): la traduccion de
  simbolo es IDENTIDAD. Por eso este adaptador NO implementa SymbolMapSink.
- La suscripcion es por (channel, instId). El canal de velas es candle<bar>, con el
  bar de OKX (1m, 5m, 15m, 1H, 4H, 1D).
- La vela cerrada se marca con confirm ('1' cerrada / '0' en curso), no con 'x'.
- El array solo trae la hora de APERTURA (ts): la de cierre se deriva y el event_time
  del origen es ts (OKX no manda un push-time como el 'E' de Binance).
"""
