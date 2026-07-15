# Componente market_connector_private_fake

El CAMINO PRIVADO (BYOC) de market data, en su version FAKE.

Un flujo privado NO es publico: no se comparte cross-tenant, pertenece a UN sujeto
(tenant + usuario) y exige conectarse a la cuenta que ese sujeto tiene en el exchange.
Eso lo convierte en una capacidad SENSIBLE, y por eso este componente declara
`connect_broker` en su manifest, una de las cinco capacidades sensibles de la lista
cerrada de P06.

## Va GATEADO, y el gate corta ANTES de conectar

El supervisor evalua la politica **antes** de invocar `initialize()` (ADR-010 +
ADR-012). Un sujeto sin entitlement explicito de `connect_broker` queda **DENEGADO
fail-closed** (D6): la instancia va a `QUARANTINED`, el `initialize()` del connector
**no llega a ejecutarse**, y por tanto no se abre ninguna conexion privada. La
denegacion es observable: se emite `component.quarantined` con su `reason_code`.

Que la conexion viva dentro de `initialize()`, y no en el constructor, es precisamente
lo que hace que el gate pueda impedirla.

Los inputs reales de jurisdiccion, KYC y VPN los aporta P06b desde la sesion verificada,
y hoy no hay proveedor comercial detras (es frontera de decision de Alvaro): sin esos
datos, lo sensible se DENIEGA. P07 demuestra el **camino** de enforcement, no la fuente
del dato de jurisdiccion.

## En P07 es FAKE, y eso significa tres cosas

- **Sin credenciales.** No las pide, no las guarda, no las acepta. Las credenciales BYOC
  reales son **P10a**: cifradas, con su propio rol de DB y su propio gate.
- **Sin ejecucion.** Las ordenes son **P10b**.
- **Sin eventos de dominio.** El hecho privado real (un fill, un cambio de balance)
  pertenece a la familia `execution.*`, que define y produce **P10b**. Fabricar aqui un
  `execution.*` seria inventar un contrato ajeno y poner en el bus un hecho que nunca
  ocurrio. Este componente demuestra que el camino esta **gateado y aislado**, y se
  calla.

## Aislamiento

Los intereses privados (`stream_scope='user'`) estan aislados por RLS: un sujeto no ve
los de otro. Y **jamas** pasan por la ventanilla de demanda publica: el worker publico
agrega solo flujos publicos y nunca aprende que pide un usuario privado.

## Su cerebro se cablea fuera

El componente no construye nada: recibe el feed privado ya construido. `components`,
`platform` e `infra` son capas hermanas y no pueden verse entre si; quien las une es el
composition root.
