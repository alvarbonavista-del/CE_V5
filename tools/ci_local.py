"""LA BATERIA COMPLETA DEL CI, EN LOCAL, CON UN SOLO COMANDO (regla 5.30).

    python tools/ci_local.py

POR QUE EXISTE. La 5.30 dice que "verde" es la bateria COMPLETA del CI y no un
subconjunto elegido a ojo. Nace del run #26: un commit verde en ruff+mypy+pytest y ROJO
en check_tenancy, porque quien verificaba escogio a mano los checks que le parecieron
relevantes y se dejo uno. Mientras la bateria local sea un ensamblaje manual, ese fallo
puede repetirse: la memoria falla, y por eso la regla exige UN comando.

LA FUENTE DE VERDAD ES .github/workflows/ci.yml, NO ESTE FICHERO. Este modulo declara
como se ejecuta cada paso EN LOCAL, pero no decide cuales hay: eso lo dice el workflow.
La GUARDIA ANTI-DERIVA (_guardia_deriva) lo LEE en cada ejecucion y compara, en las DOS
direcciones:

  - un comando del workflow que aqui no este espejado ni exento -> FALLA. Es el caso
    que origino la regla: el CI crece y la bateria local se queda corta sin enterarse.
  - un comando espejado aqui que el workflow ya no corre -> FALLA. Un paso local que
    sobrevive a su paso de CI da una falsa sensacion de cobertura.

Asi la sincronizacion no queda "a la memoria", que es lo que la 5.30 prohibe.

RUN-ALL-AND-REPORT: no se corta en el primer fallo. Ejecuta TODO, imprime un resumen de
que paso y que fallo, y sale con codigo != 0 si algo fallo. Cortar en el primero
obligaria a N vueltas para descubrir N problemas.

CERO SECRETOS: los DSN se leen del entorno. Si falta alguno, se dice CUAL y se para
(espiritu de la 5.18: nada se salta en silencio). Aqui no se escribe ninguna credencial.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess  # noqa: S404 - ejecutar los checks del CI es literalmente su trabajo.
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# El python del venv que ejecuta ESTE script: el mismo que corre todo lo demas.
_PY = sys.executable

# DSN y URL que la parte de integracion necesita. NO se hardcodean valores: solo se
# comprueba que estan, y si falta alguno se dice cual exportar.
_ENTORNO_INTEGRACION = (
    "CE_V5_DATABASE_URL",
    "CE_V5_MIGRATIONS_DATABASE_URL",
    "CE_V5_OPERATOR_DATABASE_URL",
    "CE_V5_INGESTION_DATABASE_URL",
    "CE_V5_RULES_DATABASE_URL",
    "CE_V5_REDIS_URL",
)


@dataclass(frozen=True, slots=True)
class Paso:
    """Un paso del CI y como se ejecuta en local.

    ``ci`` es el comando TAL COMO aparece en ci.yml: es la clave con la que la guardia
    anti-deriva empareja. Si el workflow lo cambia aunque sea un caracter, la guardia
    lo canta en vez de dejar que este fichero espeje algo que ya no existe.
    """

    job: str
    ci: str
    local: tuple[str, ...]
    etiqueta: str


@dataclass(slots=True)
class Resultado:
    paso: Paso
    codigo: int
    salida: str


def _tools(nombre: str) -> tuple[str, ...]:
    return (_PY, str(REPO_ROOT / "tools" / nombre))


def _binario_del_venv(nombre: str) -> str:
    """El ejecutable instalado junto al python que corre esto (lint-imports).

    Se resuelve desde sys.executable y no desde el PATH: el PATH puede tener otra
    instalacion y entonces el check correria contra un entorno que no es el nuestro.
    """
    directorio = Path(_PY).parent
    for candidato in (directorio / nombre, directorio / f"{nombre}.exe"):
        if candidato.exists():
            return str(candidato)
    encontrado = shutil.which(nombre)
    if encontrado is None:
        msg = f"no se encuentra el ejecutable {nombre!r} junto a {_PY} ni en el PATH."
        raise FileNotFoundError(msg)
    return encontrado


def _binario_externo(nombre: str) -> str:
    """Una herramienta del PATH (pnpm, node), resuelta con shutil.which.

    SE RESUELVE, NO SE PASA POR NOMBRE. En Windows `pnpm` es un shim .cmd y
    CreateProcess no lo encuentra por nombre pelado: hay que darle la ruta que which
    devuelve. Si no esta, se devuelve el nombre tal cual para que el paso falle como un
    paso FALLIDO con su mensaje, y no como un traceback que se lleva por delante el
    resto de la bateria.
    """
    return shutil.which(nombre) or nombre


def _pasos() -> tuple[Paso, ...]:
    """Los pasos de ci.yml que la bateria local ejecuta, con su traduccion.

    El orden es el LOGICO del workflow: primero lo barato que falla pronto (lint,
    tipos), luego los checks de contrato, luego la base y por ultimo las suites.
    """
    return (
        # -- Job backend ----------------------------------------------------
        Paso(
            "backend",
            "uv run ruff check .",
            (_PY, "-m", "ruff", "check", "."),
            "ruff lint",
        ),
        Paso(
            "backend",
            "uv run ruff format --check .",
            (_PY, "-m", "ruff", "format", "--check", "."),
            "ruff format",
        ),
        Paso("backend", "uv run mypy", (_PY, "-m", "mypy"), "mypy (strict)"),
        Paso(
            "backend",
            "uv run lint-imports",
            (_binario_del_venv("lint-imports"),),
            "fronteras de import (7.1)",
        ),
        Paso(
            "backend",
            "uv run python tools/check_generated.py",
            _tools("check_generated.py"),
            "schemas en sincronia (7.3/7.4)",
        ),
        Paso(
            "backend",
            "uv run python tools/check_schema_compat.py",
            _tools("check_schema_compat.py"),
            "evolucion de schemas (7.7)",
        ),
        Paso(
            "backend",
            "uv run python tools/check_manifests.py",
            _tools("check_manifests.py"),
            "manifests (7.5)",
        ),
        Paso(
            "backend",
            "uv run python tools/check_orphans.py",
            _tools("check_orphans.py"),
            "componentes huerfanos (7.6)",
        ),
        Paso(
            "backend",
            "uv run python tools/check_component_docs.py",
            _tools("check_component_docs.py"),
            "documentacion de componentes (7.9)",
        ),
        Paso(
            "backend",
            "uv run python tools/check_event_payload_registry.py",
            _tools("check_event_payload_registry.py"),
            "registro event_type -> payload (CA-06)",
        ),
        Paso(
            "backend",
            "uv run python tools/check_envelope_base_usage.py",
            _tools("check_envelope_base_usage.py"),
            "sobre no vacio (5.21)",
        ),
        Paso(
            "backend",
            "uv run python tools/check_contract_artifacts.py",
            _tools("check_contract_artifacts.py"),
            "paridad registro <-> artefactos (ADR-006)",
        ),
        # -- Job backend-integration ----------------------------------------
        Paso(
            "backend-integration",
            "uv run python -m ce_v5.infra.db.migrations",
            (_PY, "-m", "ce_v5.infra.db.migrations"),
            "migraciones",
        ),
        Paso(
            "backend-integration",
            "uv run python tools/check_tenancy.py",
            _tools("check_tenancy.py"),
            "tenancy y RLS (7.8)",
        ),
        Paso(
            "backend-integration",
            "uv run python tools/check_audit.py",
            _tools("check_audit.py"),
            "auditoria de seguridad (P06)",
        ),
        Paso(
            "backend-integration",
            "uv run python tools/check_identity_access.py",
            _tools("check_identity_access.py"),
            "ventanillas de identidad (CA-07)",
        ),
        Paso(
            "backend-integration",
            "uv run python tools/check_market_access.py",
            _tools("check_market_access.py"),
            "acceso a market data (5.20)",
        ),
        Paso(
            "backend-integration",
            "uv run python tools/check_rules_access.py",
            _tools("check_rules_access.py"),
            "acceso del motor de reglas (5.20)",
        ),
        # -- Suites ----------------------------------------------------------
        # `pytest` PELADO, como en el workflow: con testpaths incluye tests/ y los
        # componentes. En el CI la parte de integracion se salta ahi (ese job no levanta
        # base); en local, con la base viva y los DSN puestos, corre entera. Acotarlo a
        # tests/unit seria el subconjunto elegido a ojo que la 5.30 prohibe.
        Paso("backend", "uv run pytest", (_PY, "-m", "pytest", "-q"), "pytest (todo)"),
        Paso(
            "backend-integration",
            "uv run pytest tests/integration -q",
            (_PY, "-m", "pytest", "tests/integration", "-q"),
            "pytest integracion",
        ),
        # -- Job frontend ----------------------------------------------------
        Paso(
            "frontend",
            "pnpm exec biome check .",
            (_binario_externo("pnpm"), "exec", "biome", "check", "."),
            "biome lint + format",
        ),
        Paso(
            "frontend",
            "node tools/check_types_frontend.mjs",
            (_binario_externo("node"), "tools/check_types_frontend.mjs"),
            "type-check frontend",
        ),
        Paso(
            "frontend",
            "pnpm exec depcruise frontend/src --config .dependency-cruiser.cjs",
            (
                _binario_externo("pnpm"),
                "exec",
                "depcruise",
                "frontend/src",
                "--config",
                ".dependency-cruiser.cjs",
            ),
            "fronteras de import frontend (7.2)",
        ),
        Paso(
            "frontend",
            "node tools/check_generated_ts.mjs",
            (_binario_externo("node"), "tools/check_generated_ts.mjs"),
            "tipos TS en sincronia (7.3/7.4)",
        ),
    )


# Comandos del workflow que la bateria local NO ejecuta, cada uno CON SU MOTIVO. Estar
# exento es una decision escrita, no un olvido: la guardia exige que todo comando de
# ci.yml este espejado o aqui, y un motivo se lee en el diff.
_EXENTOS: dict[str, str] = {
    "uv sync --extra dev": (
        "prepara el entorno del runner desde cero. En local el venv ya existe y es el "
        "que ejecuta esta bateria; re-sincronizarlo no comprueba nada del repo."
    ),
    "pnpm install --frozen-lockfile=false": (
        "mismo motivo para el lado node: node_modules ya esta instalado en local."
    ),
    "uv run python -m ce_v5.infra.db.provision": (
        "crea los roles de PostgreSQL (app, operador, ingesta, reglas) en una base "
        "VIRGEN. En local ya estan provisionados y rehacerlo exigiria portar las "
        "contrasenas de los cuatro roles en el entorno de esta bateria, que es "
        "exactamente el poder que la 5.20 evita concentrar."
    ),
}


def _comandos_de_ci() -> tuple[tuple[str, str], ...]:
    """(job, comando) de cada `run:` de ci.yml. Sin dependencias: parseo por lineas.

    NO usa un parser de YAML porque el proyecto no lo lleva de dependencia, y anadir una
    para leer un fichero de estructura fija seria peor. A cambio, lo que el parser NO
    entiende lo DICE: si un `run:` usa un bloque multilinea (`|`, `>`), aborta pidiendo
    que se extienda, en vez de saltarselo en silencio -- que es justo la clase de
    agujero que esta guardia existe para cerrar.
    """
    if not CI_YML.exists():
        msg = f"no se encuentra el workflow en {CI_YML}: sin fuente de verdad, nada."
        raise FileNotFoundError(msg)

    job_re = re.compile(r"^  ([A-Za-z][\w-]*):\s*$")
    run_re = re.compile(r"^\s*run:\s*(.+?)\s*$")
    comandos: list[tuple[str, str]] = []
    job = "?"
    en_jobs = False
    for linea in CI_YML.read_text(encoding="utf-8").splitlines():
        if linea.rstrip() == "jobs:":
            en_jobs = True
            continue
        if en_jobs:
            encaje_job = job_re.match(linea)
            if encaje_job is not None:
                job = encaje_job.group(1)
                continue
        encaje_run = run_re.match(linea)
        if encaje_run is None:
            continue
        comando = encaje_run.group(1)
        if comando in {"|", ">", ">-", "|-", "|+", ">+"}:
            msg = (
                f"ci.yml usa un bloque multilinea en un `run:` del job {job!r}. El "
                "parser de tools/ci_local.py no lo soporta y NO se lo salta: "
                "extiendelo antes de seguir, o la bateria local dejaria de cubrir ese "
                "paso sin que nadie se entere (regla 5.30)."
            )
            raise ValueError(msg)
        comandos.append((job, comando))
    return tuple(comandos)


def _guardia_deriva(pasos: tuple[Paso, ...]) -> list[str]:
    """Compara ci.yml con la bateria en LAS DOS DIRECCIONES. Vacia si no hay deriva."""
    del_ci = {comando for _, comando in _comandos_de_ci()}
    espejados = {paso.ci for paso in pasos}
    problemas: list[str] = []

    for comando, _ in sorted((c, j) for j, c in _comandos_de_ci()):
        if comando in espejados or comando in _EXENTOS:
            continue
        problemas.append(
            f"ci.yml corre `{comando}` y la bateria local NO lo cubre. Anade su Paso "
            "en tools/ci_local.py, o exentalo con su motivo. Es la deriva que la regla "
            "5.30 existe para cazar."
        )

    for comando in sorted(espejados - del_ci):
        problemas.append(
            f"la bateria local corre `{comando}` pero ci.yml ya NO. Un paso local que "
            "sobrevive a su paso de CI da una falsa sensacion de cobertura: quitalo."
        )
    for comando in sorted(set(_EXENTOS) - del_ci):
        problemas.append(
            f"`{comando}` esta exento en tools/ci_local.py pero ci.yml ya no lo corre: "
            "la exencion sobra."
        )
    return problemas


def _exigir_entorno() -> list[str]:
    faltan = [
        var for var in _ENTORNO_INTEGRACION if not os.environ.get(var, "").strip()
    ]
    if not faltan:
        return []
    return [
        "faltan variables para la parte de integracion: "
        + ", ".join(faltan)
        + ". Exportalas en ESTA sesion (no las dejes permanentes: los guardias de "
        "arranque abortan la API y el worker si portan un DSN ajeno). Los valores "
        "locales son los mismos que declara .github/workflows/ci.yml."
    ]


def _ejecutar(paso: Paso) -> Resultado:
    try:
        proceso = subprocess.run(  # noqa: S603 - comandos declarados aqui, no externos.
            list(paso.local),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=REPO_ROOT,
        )
    except OSError as exc:
        # UNA HERRAMIENTA QUE FALTA ES UN PASO FALLIDO, no una excepcion que se lleva
        # por delante el resto: con RUN-ALL-AND-REPORT hay que poder ver los 24
        # resultados aunque uno no se pueda ni lanzar.
        return Resultado(
            paso=paso,
            codigo=127,
            salida=(
                f"no se pudo ejecutar {paso.local[0]!r}: {type(exc).__name__}: {exc}. "
                "Comprueba que la herramienta esta instalada y en el PATH."
            ),
        )
    return Resultado(
        paso=paso,
        codigo=proceso.returncode,
        salida=(proceso.stdout or "") + (proceso.stderr or ""),
    )


@dataclass(slots=True)
class _Informe:
    resultados: list[Resultado] = field(default_factory=list)
    guardia: list[str] = field(default_factory=list)
    entorno: list[str] = field(default_factory=list)

    @property
    def fallidos(self) -> list[Resultado]:
        return [r for r in self.resultados if r.codigo != 0]

    @property
    def verde(self) -> bool:
        return not self.fallidos and not self.guardia and not self.entorno


def main() -> int:
    pasos = _pasos()
    informe = _Informe()

    print("=" * 78)
    print("BATERIA COMPLETA DEL CI EN LOCAL (regla 5.30)")
    print(f"fuente de verdad: {CI_YML.relative_to(REPO_ROOT).as_posix()}")
    print("=" * 78)

    # La GUARDIA va primero: si la bateria no cubre el CI, lo que venga despues no
    # significa "verde" aunque salga verde.
    informe.guardia = _guardia_deriva(pasos)
    estado = "OK" if not informe.guardia else "FALLO"
    print(f"\n[{estado}] guardia anti-deriva ci.yml <-> bateria local")
    for problema in informe.guardia:
        print(f"      - {problema}")

    informe.entorno = _exigir_entorno()
    estado = "OK" if not informe.entorno else "FALLO"
    print(f"[{estado}] entorno de integracion")
    for problema in informe.entorno:
        print(f"      - {problema}")
    if informe.entorno:
        print("\nSe abortan los pasos: sin los DSN, los checks de base mentirian.")
        return 1

    print()
    for indice, paso in enumerate(pasos, start=1):
        etiqueta = f"[{indice:2}/{len(pasos)}] {paso.job:20} {paso.etiqueta}"
        print(f"{etiqueta} ...", flush=True)
        resultado = _ejecutar(paso)
        informe.resultados.append(resultado)
        marca = "OK   " if resultado.codigo == 0 else "FALLO"
        print(f"        -> {marca}", flush=True)

    print("\n" + "=" * 78)
    print("RESUMEN")
    print("=" * 78)
    for resultado in informe.resultados:
        marca = "OK   " if resultado.codigo == 0 else "FALLO"
        print(f"  [{marca}] {resultado.paso.job:20} {resultado.paso.etiqueta}")

    if informe.fallidos:
        print("\n" + "=" * 78)
        print("SALIDA DE LO QUE FALLO")
        print("=" * 78)
        for resultado in informe.fallidos:
            print(f"\n--- {resultado.paso.etiqueta} (codigo {resultado.codigo}) ---")
            print(resultado.salida.strip()[-3000:])

    print()
    if informe.verde:
        print("BATERIA COMPLETA: VERDE. Listo para push (regla 5.30).")
        return 0
    print(
        f"BATERIA COMPLETA: ROJA ({len(informe.fallidos)} paso(s) fallidos"
        f"{', guardia anti-deriva rota' if informe.guardia else ''}). NO se empuja."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
