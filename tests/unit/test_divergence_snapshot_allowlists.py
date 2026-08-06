"""La 0028 dada de alta en LOS TRES sitios que tienen que conocerla (LOTE 5).

POR QUE ESTE FICHERO EXISTE. Una tabla de estado nueva no se termina con la migracion:
hay que anotarla en tres allowlists que viven fuera del codigo de produccion, y
olvidarse de una NO rompe ningun test de la fuente -- rompe el CI, en un check que corre
contra la BD viva y que en local hace falta levantar. Ya paso en el LOTE 2. Este candado
mueve el fallo a donde se ve en frio y en un segundo:

  - tools/check_rules_access.py (RULES_STATE_TABLES): sin ella, el check no exige el
    SELECT+INSERT del motor NI prohibe los destructivos sobre la tabla; la rendija
    quedaria sin vigilar en los dos sentidos.
  - tools/check_tenancy.py (TABLAS_SIN_TENANT_PERMITIDAS): sin ella, la regla R6 ve una
    tabla system sin tenant_id que no esta allowlistada y la canta como violacion.
  - tools/validate_rules_worker.py: el mapa de grants reales del arnes, que se alimenta
    de RULES_STATE_TABLES y cuyo comentario documenta que migracion trae cada tabla.

Los tests de comportamiento de los dos checks ya recorren RULES_STATE_TABLES en bucle,
asi que dar de alta la tabla los extiende sola. Lo que aqui se fija es el ALTA.
"""

from __future__ import annotations

from pathlib import Path

import check_rules_access
import check_tenancy

_TABLA = "divergence_snapshot"
_MIGRACION = "0028_rules_divergence_snapshot.sql"
_RAIZ = Path(__file__).resolve().parents[2]
_SQL = (_RAIZ / "backend/src/ce_v5/infra/db/migrations/sql" / _MIGRACION).read_text(
    encoding="utf-8"
)


def test_esta_en_las_tablas_de_estado_del_motor() -> None:
    assert _TABLA in check_rules_access.RULES_STATE_TABLES


def test_esta_declarada_como_scope_system() -> None:
    # system y NO tenant: divergence.* es shared_evaluation/public_cross_tenant, asi que
    # el snapshot es un artefacto de evaluacion COMPARTIDO. Darle tenant_id duplicaria
    # la
    # misma serie del mismo flujo por cada tenant (la explosion que ADR-014 evita).
    assert check_tenancy.TABLAS_SIN_TENANT_PERMITIDAS[_TABLA] == "system"


def test_el_arnes_del_worker_documenta_la_migracion() -> None:
    # El comentario de validate_rules_worker.py es la fuente de verdad de QUE migracion
    # trae cada grant. Si se queda atras, el siguiente que toque los grants no sabra
    # donde mirar.
    validador = (_RAIZ / "tools/validate_rules_worker.py").read_text(encoding="utf-8")
    assert "0028 -> divergence_snapshot" in validador


class TestLaMigracionDiceLoQueLasAllowlistsAsUMEN:
    """Las allowlists describen la tabla; la 0028 tiene que cumplirlo de verdad."""

    def test_concede_select_e_insert_al_rol_de_reglas(self) -> None:
        assert f"GRANT SELECT, INSERT ON {_TABLA} TO ce_v5_rules;" in _SQL

    def test_revoca_los_destructivos_de_forma_explicita(self) -> None:
        # EXPLICITA a proposito: el check 5.20 comprueba el NEGATIVO, y sin el REVOKE
        # escrito no habria nada que morder si alguien reintrodujera el privilegio.
        assert f"REVOKE UPDATE, DELETE, TRUNCATE ON {_TABLA} FROM ce_v5_rules;" in _SQL

    def test_no_lleva_tenant_id(self) -> None:
        # Coherencia con scope=system: si la tabla tuviera tenant_id, la allowlist de
        # arriba estaria mintiendo y R6 morderia por el otro lado. Se mira el DDL, no el
        # fichero entero: el comentario habla de tenant_id precisamente para explicar
        # por que NO esta.
        ddl = _SQL.split("CREATE TABLE", 1)[1].split(");", 1)[0]
        assert "tenant_id" not in ddl

    def test_declara_su_isolation_scope_en_el_comentario(self) -> None:
        # R1: toda tabla declara su scope en el COMMENT, que es lo que check_tenancy lee
        # de la BD viva. Sin el, la tabla no es clasificable.
        assert "isolation_scope=system" in _SQL

    def test_los_dos_params_entran_en_la_identidad(self) -> None:
        # cadena(2,14) != cadena(7,21): si strength o rsi_period se cayeran de la PK,
        # dos
        # parametrizaciones distintas compartirian ancla y una sembraria a la otra.
        assert (
            "exchange, market_type, symbol, timeframe, strength, rsi_period, open_time"
            in _SQL
        )
