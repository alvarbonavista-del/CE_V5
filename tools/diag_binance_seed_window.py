"""DIAGNOSTICO (no fix): la ventana de siembra de Binance (.vision). Solo lectura.

Replica el ORDEN REAL del connector -- open() (subscribe WS, el hilo lector bufferiza) y
LUEGO seed() por REST /api/v3/depth -- con el Motor REAL (OrderbookBook, con el fix del
puente). Por cada siembra FRESCA registra CRUDO:
  - orden y timestamps: t_open -> t_seed (cuanto se bufferizo antes de la foto);
  - qsize del buffer JUSTO tras la foto (cuantos deltas ya habia al llegar la foto);
  - lastUpdateId de la foto;
  - los ~12 primeros deltas del primer drain: U, u, veredicto Motor (DUP/APPLY/GAP) y
    en CUAL salta el primer GAP+resync;
  - HUECO foto<->buffer: el primer delta no-dup, su U vs lastUpdateId+1 (si U>L+1 hay
    ventana perdida: deltas entre la foto y el buffer que no llegaron).

NO toca produccion: usa el connector y el Motor tal cual. NO se ejecuta en CI (5.18).
Uso: python tools/diag_binance_seed_window.py [n_iteraciones]  (def. 6)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))
sys.path.insert(0, str(REPO_ROOT / "contracts"))

from ce_v5.infra.connectors.binance.connector import BinanceSpotConnector  # noqa: E402
from ce_v5.platform.market.orderbook_book import OrderbookBook  # noqa: E402
from source.families.market import (  # noqa: E402
    Instrument,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    RawOrderbookDelta,
)

_OB_KEY = MarketStreamKey(
    exchange="binance",
    market_type=MarketType.SPOT,
    symbol="BTC-USDT",
    data_kind=MarketDataKind.ORDERBOOK,
)


def _veredicto(delta: RawOrderbookDelta, last: int, primero: bool) -> tuple[str, int]:
    """Replica _classify_binance CON el fix del puente, para etiquetar CRUDO."""
    u_ini = delta.first_update_id or 0
    u_fin = delta.final_update_id or 0
    if u_fin <= last:
        return "DUP", last
    if (u_ini <= last + 1) if primero else (u_ini == last + 1):
        return "APPLY", u_fin
    return "GAP", last


def _una_siembra(intento: int) -> None:
    connector = BinanceSpotConnector(native_to_canonical={"BTCUSDT": "BTC-USDT"})
    connector.set_symbol_map(
        [Instrument("binance", "spot", "BTC-USDT", "BTCUSDT", active=True)]
    )
    try:
        t_open = time.monotonic()
        connector.open(_OB_KEY)  # subscribe WS; el lector empieza a bufferizar.
        # ORDEN DEL CONECTOR: seed() INMEDIATO tras open (como el motor en su primer
        # drain). La foto REST se pide ya; el WS lleva bufferizando desde t_open.
        seed = connector.seed(_OB_KEY)  # REST /api/v3/depth -> lastUpdateId.
        t_seed = time.monotonic()
        qsize = connector._cola_orderbook.qsize()  # noqa: SLF001 - diagnostico
        base = seed.base_sequence
        print(
            f"\n[{intento}] t_seed-t_open={1000 * (t_seed - t_open):.0f}ms "
            f"buffer_tras_foto={qsize} deltas | lastUpdateId={base}",
            flush=True,
        )

        # Drena los ~12 primeros deltas (buffer + algo vivo) y clasifica con el Motor.
        book = OrderbookBook()
        book.seed(seed)
        deltas: list[RawOrderbookDelta] = []
        fin = time.monotonic() + 4.0
        while len(deltas) < 12 and time.monotonic() < fin:
            deltas.extend(connector.poll_deltas(500))
        if not deltas:
            print(f"[{intento}] sin deltas en el drena", flush=True)
            return

        primer_no_dup: RawOrderbookDelta | None = None
        primer_gap_idx: int | None = None
        last = base
        primero = True
        for i, d in enumerate(deltas[:12]):
            v, nuevo_last = _veredicto(d, last, primero)
            if v != "DUP" and primer_no_dup is None:
                primer_no_dup = d
            print(
                f"    d{i:02d} U={d.first_update_id} u={d.final_update_id} -> {v}",
                flush=True,
            )
            # aplica al Motor real para confirmar la transicion is_complete
            estaba = book.is_complete
            book.apply(d)
            if estaba and not book.is_complete and primer_gap_idx is None:
                primer_gap_idx = i
            if v == "APPLY":
                last = nuevo_last
                primero = False

        # HUECO foto<->buffer.
        if primer_no_dup is not None:
            u_ini = primer_no_dup.first_update_id or 0
            gap = u_ini - (base + 1)
            if gap > 0:
                lect = (
                    f"VENTANA PERDIDA foto<->buffer "
                    f"(U={u_ini} > L+1={base + 1}, faltan {gap})"
                )
            elif gap == 0:
                lect = "encadena exacto (U==L+1): sin ventana perdida"
            else:
                lect = f"ABARCA (U={u_ini} < L+1={base + 1}): el puente lo salva"
        else:
            lect = "todos los deltas fueron duplicados (foto por delante del buffer)"
        print(
            f"[{intento}] primer_gap_en_delta={primer_gap_idx} | is_complete_final="
            f"{book.is_complete} resync={book.resync_required} | {lect}",
            flush=True,
        )
    finally:
        connector.shutdown()


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    print(f"DIAGNOSTICO ventana de siembra Binance (.vision) x{n}", flush=True)
    for i in range(1, n + 1):
        try:
            _una_siembra(i)
        except Exception as exc:  # noqa: BLE001 - la sonda REPORTA cualquier fallo.
            print(f"[{i}] FALLO: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(1.5)


if __name__ == "__main__":
    main()
