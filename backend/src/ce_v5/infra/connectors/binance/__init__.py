"""Conector de Binance Spot: feed PUBLICO (ADR-014).

Solo datos publicos: velas y catalogo de instrumentos. CERO credenciales (las claves
BYOC de exchange son P10a, y viven en otra pieza con otro rol de DB).

Reparto de responsabilidades, a proposito:
- symbols.py   : traduccion canonico <-> nativo. Sin IO.
- translate.py : mensaje de Binance -> RawCandle. Sin IO. LO QUE EL CI PRUEBA A FONDO.
- pool.py      : reparto de streams entre conexiones. Sin IO. Testeable entero.
- connector.py : el IO real (WebSocket + REST). NO se prueba en CI (regla 5.18).
"""
