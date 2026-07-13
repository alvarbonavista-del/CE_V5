"""Validacion en caliente de P06b, de un solo comando (Bloque H).

QUE HACE: comprueba el entorno, siembra el escenario FALSO, arranca la API como proceso
real, espera a que acepte conexiones, corre el arnes en modo automatico y para la API.

POR QUE PUEDE ORQUESTARLO TODO SIN DEBILITAR NADA: la separacion de poderes de CA-03 es
entre PROCESOS CON ENTORNOS DISTINTOS, no entre personas con terminales distintas. Aqui
el proceso de la API se lanza con un entorno del que se ELIMINA
CE_V5_OPERATOR_DATABASE_URL (y su guardia de arranque abortaria si la encontrara), y la
herramienta de operador la ejecuta el arnes como SUBPROCESO APARTE, el unico que recibe
esa credencial. La puerta publica jamas porta la llave capaz de mover kill switches.

Uso: python tools/run_p06b_hot_validation.py
Requiere el entorno completo (.env.example lo lista). No inventa NINGUN valor por
defecto para un secreto: si falta, dice cual falta y para.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))
sys.path.insert(0, str(REPO_ROOT / "contracts"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import seed_p06b_fake  # noqa: E402

_OPERATOR_DSN_VAR = "CE_V5_OPERATOR_DATABASE_URL"
_OPERATOR_PASSWORD_VAR = "CE_V5_OPERATOR_DB_PASSWORD"

# Lo que hace falta para que esto corra. Cada una con su motivo, para que quien vea el
# fallo sepa QUE le falta y no solo que algo le falta.
_REQUERIDAS: tuple[tuple[str, str], ...] = (
    ("CE_V5_DATABASE_URL", "rol de APLICACION: con el corre la API"),
    ("CE_V5_MIGRATIONS_DATABASE_URL", "rol de MIGRACIONES: con el se siembra"),
    ("CE_V5_REDIS_URL", "bus y limitador de intentos"),
    ("CE_V5_JWT_SECRET", "firma de los tokens de acceso"),
    ("CE_V5_RATE_LIMIT_SECRET", "huellas del limitador (nunca emails en claro)"),
    (_OPERATOR_DSN_VAR, "rol de OPERADOR: SOLO para el subproceso del operador"),
)

_ARRANQUE_MAXIMO_SEGUNDOS = 30.0
_PARADA_MAXIMA_SEGUNDOS = 10.0


def _say(mensaje: str = "") -> None:
    print(mensaje, flush=True)


def _comprobar_entorno() -> None:
    faltan = [
        f"  {variable}  ({motivo})"
        for variable, motivo in _REQUERIDAS
        if not os.environ.get(variable, "").strip()
    ]
    if not faltan:
        return
    _say(
        "FALTAN VARIABLES DE ENTORNO. Ningun secreto tiene valor por defecto: un valor"
    )
    _say("por defecto para un secreto es un secreto conocido.")
    _say()
    for linea in faltan:
        _say(linea)
    _say()
    _say("Plantilla: .env.example")
    raise SystemExit(2)


def _entorno_de_la_api() -> dict[str, str]:
    """El entorno de la API, SIN la credencial de operador.

    ESTO es lo que hace cumplir CA-03 en la practica. Si la variable siguiera aqui,
    DbConfig.from_env lanzaria OperatorDsnInRuntimeError y la API ni arrancaria: el
    guardia esta puesto precisamente para que este descuido no pueda ocurrir en
    silencio. Se quita tambien la contrasena del operador: una credencial que un proceso
    no necesita es una credencial que no debe portar.
    """
    entorno = dict(os.environ)
    entorno.pop(_OPERATOR_DSN_VAR, None)
    entorno.pop(_OPERATOR_PASSWORD_VAR, None)
    return entorno


def _volcar(proceso: subprocess.Popen[str]) -> threading.Thread:
    """Reemite la salida de la API con prefijo, entrelazada con la del arnes."""

    def bombear() -> None:
        salida = proceso.stdout
        if salida is None:
            return
        for linea in salida:
            _say(f"[API] {linea.rstrip()}")

    hilo = threading.Thread(target=bombear, name="api-stdout", daemon=True)
    hilo.start()
    return hilo


def _esperar_puerto(host: str, puerto: int, proceso: subprocess.Popen[str]) -> None:
    """Espera a que el puerto ACEPTE conexiones. Nada de dormir a ciegas."""
    empezado = time.monotonic()
    while time.monotonic() - empezado < _ARRANQUE_MAXIMO_SEGUNDOS:
        if proceso.poll() is not None:
            raise SystemExit(
                f"La API murio al arrancar (codigo {proceso.returncode}). Su salida "
                "esta arriba, con el prefijo [API]."
            )
        try:
            with socket.create_connection((host, puerto), timeout=1.0):
                _say(
                    f"    la API acepta conexiones en {host}:{puerto} "
                    f"({time.monotonic() - empezado:.1f} s)"
                )
                return
        except OSError:
            time.sleep(0.2)
    proceso.terminate()
    raise SystemExit(
        f"La API no acepto conexiones en {_ARRANQUE_MAXIMO_SEGUNDOS:.0f} s. Su salida "
        "esta arriba, con el prefijo [API]."
    )


def _parar(proceso: subprocess.Popen[str]) -> None:
    """Parada LIMPIA: primero se pide, y solo si no obedece se mata."""
    if proceso.poll() is not None:
        return
    proceso.terminate()
    try:
        proceso.wait(timeout=_PARADA_MAXIMA_SEGUNDOS)
    except subprocess.TimeoutExpired:
        _say("    la API no se paro sola; se mata.")
        proceso.kill()
        proceso.wait()


def main() -> int:
    _say(
        "Validacion en caliente de P06b: entorno, siembra, API, arnes. Un solo comando."
    )
    _say(
        f"La API se lanza SIN {_OPERATOR_DSN_VAR}: la puerta publica jamas porta la "
        "credencial de operador (CA-03)."
    )
    _say()

    _say("--- 1. entorno")
    _comprobar_entorno()
    _say("    todas las variables necesarias estan puestas.")

    _say()
    _say("--- 2. siembra del escenario FALSO")
    seed_p06b_fake.main()

    host = os.environ.get("CE_V5_API_HOST", "127.0.0.1")
    puerto = int(os.environ.get("CE_V5_API_PORT", "8000"))

    _say()
    _say("--- 3. arranque de la API (proceso real, Uvicorn, no TestClient)")
    api = subprocess.Popen(  # noqa: S603 - orden fija, sin shell
        [sys.executable, "-m", "ce_v5.entrypoints.api"],
        env=_entorno_de_la_api(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(REPO_ROOT),
    )
    _volcar(api)

    try:
        _say()
        _say("--- 4. espera a que el puerto acepte conexiones")
        _esperar_puerto(host, puerto, api)

        _say()
        _say("--- 5. el arnes (modo automatico)")
        arnes = subprocess.run(  # noqa: S603 - orden fija, sin shell
            [sys.executable, str(REPO_ROOT / "tools" / "validate_p06b_api.py")],
            env=dict(os.environ),
            cwd=str(REPO_ROOT),
            check=False,
        )
    finally:
        _say()
        _say("--- 6. parada de la API")
        _parar(api)
        _say("    API parada.")

    return arnes.returncode


if __name__ == "__main__":
    sys.exit(main())
