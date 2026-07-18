"""Catalogo de DataSources: registro y validacion del grafo (ADR-008, ADR-009).

Los componentes productores publican sus DataSources al catalogo por discovery (INFORME
6 sec 12.3); el validador, el compilador y el motor consumen el catalogo, no conocen los
componentes. Aqui viven el registro por id (resolve fail-loud) y la validacion de que el
grafo de derivacion (consumes) esta COMPLETO (toda fuente consumida existe) y es
ACICLICO. La deteccion de ciclos A->B->A vive AQUI, no en una declaracion suelta, porque
solo el catalogo ve el grafo entero (hallazgo de Claude Code en 2a).
"""

from source.datasource import DataSourceDeclaration


class DataSourceCatalogError(RuntimeError):
    """Error del catalogo de DataSources."""


class UnknownDataSourceError(DataSourceCatalogError):
    """Se referencio un source_id que no esta en el catalogo."""


class DuplicateDataSourceError(DataSourceCatalogError):
    """Se registro dos veces el mismo source_id."""


class MissingDependencyError(DataSourceCatalogError):
    """Una fuente derivada consume un source_id que no existe en el catalogo."""


class CyclicDependencyError(DataSourceCatalogError):
    """El grafo de derivacion (consumes) tiene un ciclo."""


class DataSourceCatalog:
    """Registro en memoria de DataSources, resoluble por id y validable como grafo."""

    def __init__(self) -> None:
        self._by_id: dict[str, DataSourceDeclaration] = {}

    def register(self, declaration: DataSourceDeclaration) -> None:
        """Anade una declaracion. Falla si el id ya esta (fail-loud, no sobrescribe)."""
        if declaration.source_id in self._by_id:
            msg = f"source_id duplicado en el catalogo: {declaration.source_id!r}."
            raise DuplicateDataSourceError(msg)
        self._by_id[declaration.source_id] = declaration

    def resolve(self, source_id: str) -> DataSourceDeclaration:
        """Devuelve la declaracion de un id, o falla fuerte si no existe."""
        declaration = self._by_id.get(source_id)
        if declaration is None:
            msg = f"source_id no registrado en el catalogo: {source_id!r}."
            raise UnknownDataSourceError(msg)
        return declaration

    def validate(self) -> None:
        """Comprueba que el grafo consumes esta completo y es aciclico. Fail-loud."""
        for declaration in self._by_id.values():
            for dependency in declaration.consumes:
                if dependency not in self._by_id:
                    msg = (
                        f"la fuente {declaration.source_id!r} consume "
                        f"{dependency!r}, que no existe en el catalogo."
                    )
                    raise MissingDependencyError(msg)
        self._check_acyclic()

    def _check_acyclic(self) -> None:
        visiting: set[str] = set()
        done: set[str] = set()

        def visit(source_id: str) -> None:
            if source_id in done:
                return
            if source_id in visiting:
                msg = f"ciclo en el grafo de derivacion (consumes) en {source_id!r}."
                raise CyclicDependencyError(msg)
            visiting.add(source_id)
            for dependency in self._by_id[source_id].consumes:
                visit(dependency)
            visiting.discard(source_id)
            done.add(source_id)

        for source_id in self._by_id:
            visit(source_id)
