"""Paquete raiz de la fuente de contratos (ADR-006).

Agrupa los subpaquetes de contratos (envelope, families, time) bajo un
unico paquete importable 'source', de modo que ninguno sea un nombre de
importacion de primer nivel. Motivo: 'time' es un modulo built-in de
Python y un paquete de primer nivel con ese nombre queda tapado. Revision
de la decision de construccion D3: la raiz de importacion sube de
contracts/source a contracts/; la estructura de carpetas de
DOC_ESTRUCTURA sec.3 no cambia. No contiene logica.
"""
