"""Traduccion de simbolos canonico <-> nativo de Binance.

El contrato usa SIEMPRE la forma canonica BASE-QUOTE (BTC-USDT). Binance usa la suya
(BTCUSDT), y en los nombres de stream la exige en MINUSCULAS (btcusdt@kline_1m).

LA VUELTA NO SE CALCULA, Y NO ES UN DESCUIDO: ES IMPOSIBLE HACERLO BIEN.
"BTCUSDT" podria ser BTC-USDT o BT-CUSDT; sin saber donde parte, cualquier regla que
inventemos es una adivinanza. Y adivinar aqui significa escribir el precio de una
moneda en el historico de otra.

Por eso existe native_symbol en el catalogo de instrumentos (prometido en B3A, y este
es el consumidor que lo cumple): el exchange nos DICE como llama a cada par, se guarda,
y la resolucion nativo -> canonico es una CONSULTA, no un calculo.
"""

from __future__ import annotations


class SymbolTranslationError(ValueError):
    """El simbolo no tiene la forma canonica BASE-QUOTE."""


def to_native(canonical: str) -> str:
    """'BTC-USDT' -> 'BTCUSDT'. La forma que Binance entiende."""
    base, sep, quote = canonical.partition("-")
    if not sep or not base or not quote:
        msg = (
            f"simbolo no canonico: {canonical!r}. Se espera BASE-QUOTE (p.ej. "
            "BTC-USDT); la forma nativa del exchange no vale aqui."
        )
        raise SymbolTranslationError(msg)
    return f"{base}{quote}"


def to_stream_name(canonical: str, timeframe: str) -> str:
    """El nombre del stream de velas que Binance exige, en MINUSCULAS.

    'BTC-USDT' + '1m' -> 'btcusdt@kline_1m'. Si se manda en mayusculas, Binance no
    reconoce el stream y la suscripcion se queda muda: no falla, simplemente no llegan
    datos nunca. Un fallo silencioso, que es el peor tipo.
    """
    return f"{to_native(canonical).lower()}@kline_{timeframe}"
