"""Traduccion de simbolos canonico -> nativo de Binance. SIN RED."""

from __future__ import annotations

import pytest

from ce_v5.infra.connectors.binance.symbols import (
    SymbolTranslationError,
    to_native,
    to_stream_name,
)


@pytest.mark.parametrize(
    ("canonico", "nativo"),
    [
        ("BTC-USDT", "BTCUSDT"),
        ("ETH-EUR", "ETHEUR"),
        ("DOGE-USDT", "DOGEUSDT"),
        ("1INCH-BTC", "1INCHBTC"),
        # REGRESION del hallazgo en caliente (B12b): el ticker 'T' de Binance
        # (Threshold) tiene base de UN caracter. El traductor ya lo resolvia bien
        # (parte por el guion, no por un regex {2,15}); aqui queda fijado.
        ("T-USDT", "TUSDT"),
    ],
)
def test_to_native(canonico: str, nativo: str) -> None:
    assert to_native(canonico) == nativo


@pytest.mark.parametrize("basura", ["BTCUSDT", "", "-USDT", "BTC-", "BTC"])
def test_un_simbolo_no_canonico_se_rechaza(basura: str) -> None:
    # La forma NATIVA no vale aqui: el contrato usa SIEMPRE BASE-QUOTE.
    with pytest.raises(SymbolTranslationError):
        to_native(basura)


def test_to_stream_name_va_en_minusculas() -> None:
    # Binance exige el nombre del stream en minusculas. En mayusculas no falla: la
    # suscripcion se queda MUDA y no llegan datos nunca. Un fallo silencioso, que es el
    # peor tipo, y por eso tiene su test.
    assert to_stream_name("BTC-USDT", "1m") == "btcusdt@kline_1m"
    assert to_stream_name("ETH-EUR", "4h") == "etheur@kline_4h"


def test_la_vuelta_no_se_calcula() -> None:
    # AQUI ESTA LA RAZON DE SER de native_symbol en el catalogo (prometido en B3A).
    # 'BTCUSDT' podria ser BTC-USDT o BT-CUSDT: no hay forma de deducirlo, y adivinar
    # significaria escribir el precio de una moneda en el historico de otra. Por eso el
    # modulo NO expone ninguna funcion from_native: la resolucion es una CONSULTA al
    # catalogo, no un calculo.
    import ce_v5.infra.connectors.binance.symbols as symbols

    assert not hasattr(symbols, "from_native")
    assert to_native("BTC-USDT") == to_native("BT-CUSDT") == "BTCUSDT"
