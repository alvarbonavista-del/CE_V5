"""Validacion EN CALIENTE del libro L2 (P07c Tanda V; paso 8 DoD, 5.32, dato publico).

Contra los TRES exchanges reales (Binance por data-*.binance.vision, OKX, Bybit),
demuestra con EVIDENCIA CRUDA, SOLO LECTURA de feed publico y EN MEMORIA (sin DB, sin
bus), lo que un fake no puede:

  1. RECONSTRUCCION POR SECUENCIA: se abre el canal de libro de BTC-USDT, se siembra
     (Binance REST /api/v3/depth por .vision; OKX/Bybit el primer snapshot WS) y se
     mantiene el libro N segundos aplicando deltas EN ORDEN por el Motor real. CRUDO:
     mejor bid/ask, num niveles, ultima secuencia, is_complete, deltas y deltas/seg.

  2. DISCONTINUIDAD (simulada, como admite la ficha): el arnes DESCARTA deltas unos
     segundos, de modo que el Motor vea un salto de secuencia. El Motor marca
     is_complete=False y dispara el RESYNC; el arnes fuerza una reconexion REAL
     (force_reconnect_all) y el Motor RE-SIEMBRA con foto fresca -> is_complete=True.
     (Las excepciones OKX keepalive seqId==prevSeqId / mantenimiento seqId<prevSeqId son
     NOOP en el Motor: durante el mantenimiento resyncs queda en 0, y se verifica.)

  3. METRICAS para b-i/b-ii (cond.6): deltas/seg y COSTE de mant. (tiempo de apply
     por lote). Bybit por orderbook.200 (100 ms). Se reportan los tres.

Es la MISMA maquinaria de P07c (OrderbookIngestionEngine + OrderbookBook) validando los
tres exchanges: parte del veredicto de CE-14. El connector se construye POR EL REGISTRO.

REGLA DURA: el connector usa hilos daemon. Este arnes cierra el datasource
(connector.shutdown()) en un finally PASE LO QUE PASE, y es ACOTADO en el tiempo. Sin
bucle infinito. NO abre la base ni el bus: el Motor escribe contra un writer EN MEMORIA.

Uso: python tools/validate_orderbook_live.py [binance|okx|bybit]  (def. binance)
     CE_V5_LIVE_WINDOW_S ajusta la ventana de mantenimiento (def. 30 s).
NO se ejecuta en CI (5.18): el CI es hermetico, ningun test abre socket.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))
sys.path.insert(0, str(REPO_ROOT / "contracts"))

from ce_v5.core.clock import Clock, SystemClock  # noqa: E402
from ce_v5.entrypoints.worker_ingestion.connector_registry import (  # noqa: E402
    build_default_registry,
)
from ce_v5.infra.connectors.binance.connector import BinanceSpotConnector  # noqa: E402
from ce_v5.infra.connectors.bybit.connector import BybitSpotConnector  # noqa: E402
from ce_v5.infra.connectors.okx.connector import OkxSpotConnector  # noqa: E402
from ce_v5.platform.market.orderbook_ingestor import (  # noqa: E402
    OrderbookIngestionEngine,
)
from source.families.market import (  # noqa: E402
    Instrument,
    MarketDataKind,
    MarketStreamKey,
    MarketType,
    RawOrderbookDelta,
)
from source.families.orderbook import (  # noqa: E402
    OrderbookResyncedPayload,
    OrderbookSnapshotPayload,
)

_SOURCE = "worker_orderbook_hot"
_EXCHANGES = ("binance", "okx", "bybit")
_SYMBOL = "BTC-USDT"
_NATIVE = {"binance": "BTCUSDT", "okx": "BTC-USDT", "bybit": "BTCUSDT"}

_Connector = BinanceSpotConnector | OkxSpotConnector | BybitSpotConnector

_DEFAULT_WINDOW_S = 30.0
_DROP_S = 3.0  # cuanto se descartan deltas para provocar el hueco de secuencia.
_RECOVER_S = 25.0  # margen para reconectar + re-sembrar con foto fresca.
_TICK_S = 0.2
_PRINT_EVERY_S = 3.0


def _ob_key(exchange: str) -> MarketStreamKey:
    return MarketStreamKey(
        exchange=exchange,
        market_type=MarketType.SPOT,
        symbol=_SYMBOL,
        data_kind=MarketDataKind.ORDERBOOK,
    )


class _FakeWriter:
    """Writer EN MEMORIA (OrderbookWriterPort por forma): el Motor publica el resync
    en vez de a la base. Cuenta lo que persistiria para poder afirmarlo CRUDO.
    """

    def __init__(self) -> None:
        self.resyncs: list[str] = []
        self.discontinuities = 0

    def persist_and_enqueue(
        self,
        *,
        envelope_json: bytes,
        payload: OrderbookSnapshotPayload | OrderbookResyncedPayload,
        event_type: str,
        stream_key: str,
        idempotency_key: str,
        event_time: int,
    ) -> bool:
        self.resyncs.append(idempotency_key)
        return True

    def persist_sample(
        self, payload: OrderbookSnapshotPayload, event_time: int
    ) -> bool:  # pragma: no cover - el arnes no muestrea.
        return True

    def record_discontinuity(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        from_sequence: int,
        to_sequence: int | None,
        event_time: int,
        reason: str,
    ) -> bool:
        self.discontinuities += 1
        return True


class _InjectingSource:
    """Decorador TRANSPARENTE sobre el connector real (OrderbookDataSourcePort, forma).

    Delega todo; su unico poder es DESCARTAR los deltas que entrega poll_deltas en una
    ventana armada. Descartar deltas rompe la cadena de secuencia SIN tocar el socket:
    el siguiente delta que el Motor si vea saltara la secuencia -> hueco. Es la
    discontinuidad 'simulada' que admite la ficha, en el borde del Motor, no un dato
    inventado.
    """

    def __init__(self, inner: _Connector) -> None:
        self._inner = inner
        self._drop_until = 0.0
        self.total_deltas = 0
        self.dropped = 0

    def seed(self, key: MarketStreamKey) -> object:
        return self._inner.seed(key)

    def open(self, key: MarketStreamKey) -> None:
        self._inner.open(key)

    def close(self, key: MarketStreamKey) -> None:
        self._inner.close(key)

    def active(self) -> set[str]:
        return set(self._inner.active())

    def drain_reconnected(self) -> set[str]:
        return set(self._inner.drain_reconnected())

    def poll_deltas(self, timeout_ms: int) -> list[RawOrderbookDelta]:
        lote = list(self._inner.poll_deltas(timeout_ms))
        if not lote:
            return []
        if time.monotonic() < self._drop_until:
            self.dropped += len(lote)
            return []  # DESCARTADO: el Motor no lo vera -> salto de secuencia.
        self.total_deltas += len(lote)
        return lote

    def arm_drop(self, seconds: float) -> None:
        self._drop_until = time.monotonic() + seconds


def _build_connector(exchange: str) -> _Connector:
    """El connector POR EL REGISTRO (resolve). Binance/Bybit necesitan el mapa
    nativo->canonico (sin DB: se construye a mano para BTC-USDT); OKX resuelve por
    identidad.
    """
    connector = build_default_registry().resolve(exchange)
    if exchange == "binance":
        assert isinstance(connector, BinanceSpotConnector)
        connector.set_symbol_map(
            [Instrument("binance", "spot", _SYMBOL, "BTCUSDT", active=True)]
        )
        return connector
    if exchange == "okx":
        assert isinstance(connector, OkxSpotConnector)
        return connector
    assert isinstance(connector, BybitSpotConnector)
    connector.set_symbol_map(
        [Instrument("bybit", "spot", _SYMBOL, "BTCUSDT", active=True)]
    )
    return connector


class _Harness:
    def __init__(self, exchange: str, window_s: float) -> None:
        self._exchange = exchange
        self._window_s = window_s
        self._connector = _build_connector(exchange)
        self._source = _InjectingSource(self._connector)
        self._writer = _FakeWriter()
        clock: Clock = SystemClock()
        self._engine = OrderbookIngestionEngine(
            self._source,  # type: ignore[arg-type]  # forma estructural del puerto
            self._writer,  # type: ignore[arg-type]  # forma estructural del puerto
            clock,
            component_source=_SOURCE,
        )
        self._stream_id = _ob_key(exchange).as_stream_key()

    def _snapshot(self, label: str) -> None:
        book = self._engine.book_for(self._stream_id)
        m = self._engine.metrics
        if book is None:
            print(f"  [{label}] libro AUN SIN SEMILLA (deltas={m.deltas_applied})")
            return
        print(
            f"  [{label}] is_complete={book.is_complete} seq={book.sequence} "
            f"niveles={len(book.bids())}+{len(book.asks())} "
            f"best_bid={book.best_bid()} best_ask={book.best_ask()} "
            f"deltas={m.deltas_applied} resyncs={m.resyncs} reseeds={m.reseeds} "
            f"reconn={self._connector.metrics.reconnections}"
        )

    def _drain_for(self, seconds: float, label: str) -> None:
        fin = time.monotonic() + seconds
        ultimo = 0.0
        while time.monotonic() < fin:
            self._engine.drain_once()
            ahora = time.monotonic()
            if ahora - ultimo >= _PRINT_EVERY_S:
                self._snapshot(label)
                ultimo = ahora
            time.sleep(_TICK_S)

    def _complete(self) -> bool:
        book = self._engine.book_for(self._stream_id)
        return book is not None and book.is_complete

    def _drain_hasta_completo(self, timeout_s: float) -> bool:
        fin = time.monotonic() + timeout_s
        while time.monotonic() < fin:
            self._engine.drain_once()
            if self._complete():
                return True
            time.sleep(_TICK_S)
        return False

    def _wait_seed(self, timeout_s: float, intentos: int = 5) -> bool:
        """Lleva el libro a un estado SANO: completo Y manteniendose (aplica deltas sin
        perder la completitud). Si el arranque tropieza (p.ej. la carrera de siembra de
        Binance: el delta-puente con U<lastUpdateId+1 que el Motor marca como hueco), se
        RE-SIEMBRA por el mecanismo REAL (reconexion -> foto fresca), acotado a N.
        NO se oculta: cada re-siembra se imprime.
        """
        print(f"\n=== FASE 0: abrir canal de libro + sembrar ({self._exchange}) ===")
        self._source.open(_ob_key(self._exchange))
        print(f"  abierto {self._stream_id}; activos={sorted(self._source.active())}")
        for intento in range(1, intentos + 1):
            if not self._drain_hasta_completo(timeout_s):
                print(f"  intento {intento}: el libro no llego a COMPLETO.")
                self._connector.force_reconnect_all()
                continue
            # Comprueba que SE MANTIENE: aplica deltas unos segundos sin caer en resync.
            deltas0 = self._engine.metrics.deltas_applied
            estable_fin = time.monotonic() + 4.0
            while time.monotonic() < estable_fin:
                self._engine.drain_once()
                if not self._complete():
                    break
                time.sleep(_TICK_S)
            if self._complete() and self._engine.metrics.deltas_applied > deltas0:
                extra = "" if intento == 1 else f" (tras {intento - 1} re-siembra/s)"
                self._snapshot(f"SEMBRADO/ESTABLE{extra}")
                return True
            print(
                f"  intento {intento}: el libro perdio la completitud tras sembrar "
                f"(carrera de siembra); re-siembra por reconexion real."
            )
            self._connector.force_reconnect_all()
        print(
            "  FALLO: el libro no alcanzo un estado sano en los intentos dados.",
            file=sys.stderr,
        )
        return False

    def run(self) -> bool:
        raya = "#" * 70
        print(
            f"\n{raya}\n# LIBRO L2 EN CALIENTE -- {self._exchange.upper()} (BTC-USDT)"
            f"\n{raya}"
        )
        if not self._wait_seed(timeout_s=25.0):
            return False

        # FASE 1: mantenimiento por secuencia. Se cronometra con CPU (process_time), NO
        # con reloj de pared: el drain BLOQUEA esperando el socket, y esa espera NO es
        # coste. process_time excluye el sleep/IO y mide el trabajo REAL (parse del hilo
        # lector + apply del Motor) que mantener el libro le cuesta al PROCESO -- que es
        # justo la senal b-i/b-ii: si ahoga velas/trades o no.
        deltas_antes = self._source.total_deltas
        resyncs_antes = self._engine.metrics.resyncs
        t0 = time.monotonic()
        cpu0 = time.process_time()
        self._drain_for(self._window_s, "MANTENIMIENTO")
        cpu_mant = time.process_time() - cpu0
        dur = time.monotonic() - t0
        deltas_fase1 = self._source.total_deltas - deltas_antes
        book = self._engine.book_for(self._stream_id)
        if deltas_fase1 == 0 or book is None or not book.is_complete:
            print(
                "  FALLO: sin deltas vivos o el libro no se mantuvo completo.",
                file=sys.stderr,
            )
            return False
        # DELTA de resyncs durante el mantenimiento (la Fase 0 pudo re-sembrar): en un
        # tramo sano no debe haber ninguno; las excepciones OKX (keepalive/mant.) no lo
        # disparan.
        resyncs_mant = self._engine.metrics.resyncs - resyncs_antes
        veredicto_noop = "OK" if resyncs_mant == 0 else "REVISAR"
        print(
            f"  [FASE 1 OK] {deltas_fase1} deltas en {dur:.1f}s "
            f"({deltas_fase1 / dur:.1f} deltas/seg); is_complete=True; "
            f"resyncs durante mantenimiento={resyncs_mant} "
            f"(keepalive/mant. OKX no disparan resync: {veredicto_noop})"
        )

        # FASE 2: provocar el hueco de secuencia (descartando deltas).
        print("\n=== FASE 2: discontinuidad simulada (descartar deltas) ===")
        resyncs_pre = self._engine.metrics.resyncs
        self._snapshot("ANTES-HUECO")
        self._source.arm_drop(_DROP_S)
        print(f"  descartando deltas {_DROP_S:.0f}s para romper la secuencia...")
        # Drena durante y despues del descarte hasta que el Motor marque el hueco.
        fin = time.monotonic() + _DROP_S + 8.0
        while time.monotonic() < fin:
            self._engine.drain_once()
            book = self._engine.book_for(self._stream_id)
            if book is not None and not book.is_complete:
                break
            time.sleep(_TICK_S)
        book = self._engine.book_for(self._stream_id)
        resyncs_post = self._engine.metrics.resyncs
        incompleto = book is not None and not book.is_complete
        disparo = resyncs_post > resyncs_pre
        self._snapshot("TRAS-HUECO")
        print(
            f"  descartados={self._source.dropped}; is_complete->False={incompleto}; "
            f"RESYNC publicado={disparo} (resyncs {resyncs_pre}->{resyncs_post}, "
            f"writer.resyncs={len(self._writer.resyncs)})"
        )
        if not (incompleto and disparo):
            print(
                "  FALLO: el Motor no marco el hueco o no disparo el resync.",
                file=sys.stderr,
            )
            return False

        # FASE 3: recuperacion por reconexion REAL -> re-siembra.
        print("\n=== FASE 3: recuperacion (reconexion real -> re-siembra) ===")
        reseeds_pre = self._engine.metrics.reseeds
        cerradas = self._connector.force_reconnect_all()
        print(
            f"  force_reconnect_all: cerro {cerradas} conexion(es); espero foto fresca"
        )
        fin = time.monotonic() + _RECOVER_S
        while time.monotonic() < fin:
            self._engine.drain_once()
            book = self._engine.book_for(self._stream_id)
            if (
                book is not None
                and book.is_complete
                and self._engine.metrics.reseeds > reseeds_pre
            ):
                break
            time.sleep(_TICK_S)
        book = self._engine.book_for(self._stream_id)
        recuperado = book is not None and book.is_complete
        reseeds = self._engine.metrics.reseeds - reseeds_pre
        self._snapshot("RECUPERADO")
        print(
            f"  reseeds={reseeds}; is_complete->True={recuperado}; "
            f"discontinuidades_apuntadas={self._engine.metrics.discontinuities_recorded}"
        )
        if not recuperado:
            print(
                "  FALLO: el libro no se recupero tras la re-siembra.", file=sys.stderr
            )
            return False

        # FASE 4: metricas para b-i/b-ii. CPU real de mantener el libro (process_time),
        # no reloj de pared.
        dps = deltas_fase1 / dur if dur > 0 else 0.0
        us_por_delta = (cpu_mant / deltas_fase1 * 1_000_000.0) if deltas_fase1 else 0.0
        pct_core = (cpu_mant / dur * 100.0) if dur > 0 else 0.0
        print("\n=== FASE 4: METRICAS (b-i/b-ii, cond.6) ===")
        print(
            f"  deltas/seg (mantenimiento) = {dps:.1f}\n"
            f"  CPU de mantenimiento       = {cpu_mant * 1000.0:.1f} ms de CPU en "
            f"{dur:.1f}s de reloj -> {pct_core:.2f}% de un core\n"
            f"  coste por delta            = {us_por_delta:.1f} us de CPU/delta "
            f"({deltas_fase1} deltas)\n"
            f"  resyncs={self._engine.metrics.resyncs} "
            f"reseeds={self._engine.metrics.reseeds} "
            f"discontinuidades={self._engine.metrics.discontinuities_recorded} "
            f"rechazos={self._engine.metrics.rejected}"
        )
        print(f"\n[{self._exchange.upper()}] VALIDACION EN CALIENTE DEL LIBRO: OK")
        return True


def main() -> None:
    exchange = sys.argv[1].lower() if len(sys.argv) > 1 else "binance"
    if exchange not in _EXCHANGES:
        print(
            f"FALLO: exchange {exchange!r} no soportado. Usa: {', '.join(_EXCHANGES)}.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    window_s = float(os.environ.get("CE_V5_LIVE_WINDOW_S", str(_DEFAULT_WINDOW_S)))
    harness = _Harness(exchange, window_s)
    ok = False
    try:
        ok = harness.run()
    finally:
        harness._connector.shutdown()  # noqa: SLF001 - el arnes ES el duenno del connector
        print("\nCONECTOR DETENIDO (hilo de fondo parado).")
    if not ok:
        print(
            f"\nVALIDACION EN CALIENTE {exchange.upper()} (libro L2): FALLIDA. Una "
            "validacion que miente es peor que ninguna.",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
