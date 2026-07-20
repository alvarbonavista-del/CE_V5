"""Presupuesto de complejidad de una Rule: los hard caps de plataforma (ADR-015).

FUENTE UNICA. Estos topes los necesitan capas que NO PUEDEN VERSE entre si: platform
(el validador de admision, que rechaza la regla que se pasa) e infra (el borde que
escribe la autoria y abre suscripciones reales a un exchange). Mientras vivieron en
platform.rules.validator, infra tuvo que DUPLICAR el numero -- y un tope duplicado es un
tope que un dia deja de coincidir consigo mismo. Viven aqui, en contracts, porque
contracts es la base neutral que ambas importan y nadie importa de ellas: el mismo sitio
donde ya vive MAX_INTENTS_PER_SUBJECT (source.families.market), por el mismo motivo.

QUE ES UN HARD CAP Y QUE NO. Estos son maximos ABSOLUTOS de plataforma: existen para que
una sola regla no pueda hacer un agujero en el motor, y son iguales para todo el mundo.
Los limites POR PLAN (cuota de reglas, de nodos booleanos totales, de intereses por
sujeto segun contrato comercial) son otra cosa: concern de P11 y del gate, y NO se
declaran aqui. Mezclar ambos convertiria un limite de supervivencia en algo negociable.
"""

# Un grupo por contexto de evaluacion (timeframe) y como mucho cinco: la regla mas
# compleja que v5.0 admite mira cinco granularidades a la vez.
MAX_GROUPS_PER_RULE = 5

# Features por grupo y condiciones por feature: acotan el ANCHO del arbol booleano, que
# es lo que se evalua entero en cada vela.
MAX_FEATURES_PER_GROUP = 3
MAX_CONDITIONS_PER_FEATURE = 5

# Fuentes DISTINTAS por feature: cada una es un fetch de historia por evaluacion, asi
# que este tope es el que acota el coste de I/O de una regla, no solo su tamano.
MAX_SOURCES_PER_FEATURE = 3

# NOTA sobre los intents por regla: NO hay una constante propia, y es deliberado. Una
# regla declara un SubscriptionIntent por evaluation_context DISTINTO, y los contextos
# distintos no pueden superar el numero de grupos -- asi que el tope de intents ES
# MAX_GROUPS_PER_RULE, no un numero paralelo que casualmente vale lo mismo. Darle nombre
# propio fue precisamente el duplicado que esta mudanza elimina (P08 7.2 -> B8.1).
