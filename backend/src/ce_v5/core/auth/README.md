# core/auth

Nucleo de autenticacion de CE v5 (P06b). Reglas neutras de identidad y sesion, sin
dependencia de framework web ni de base de datos: la infraestructura las consume, no
al reves (DOC_ESTRUCTURA sec.6).

Contenido actual:
- `email.py`: forma canonica del email como identificador de login.
- `config.py`: secreto de firma y vidas de los tokens. Se NIEGA a arrancar si falta el
  secreto o es mas corto de 32 caracteres.
- `ports.py`: puerto `PasswordHasher` (hash y verificacion, sin driver).
- `passwords.py`: implementacion del puerto con Argon2id (lento y costoso en memoria a
  proposito).
- `tokens.py`: JWT de acceso de vida corta. NO lleva el tenant dentro (lo resuelve el
  backend en cada peticion desde la pertenencia) y exige el algoritmo de forma
  explicita al verificar (defensa contra la confusion de algoritmo, `alg: none`).
- `sessions.py`: refresh token opaco y rotatorio. En la base vive solo su HUELLA
  (SHA-256), nunca el token.
