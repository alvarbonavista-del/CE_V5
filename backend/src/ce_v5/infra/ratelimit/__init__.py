"""Adaptadores del limitador de intentos (P06b, CA-10).

El puerto vive en core/auth/rate_limit.py; aqui esta su implementacion sobre Redis, que
es donde vive el estado EFIMERO (con TTL), nunca en una tabla.
"""
