// HUECO PARA FUTURAS DATASOURCES -- la FORMA lista, SIN construir ninguna todavia.
//
// El visor de hoy solo pinta velas. Cuando existan los indicadores (RSI en P08b;
// pivotphase, divergencias y footprint en P08c) se ENCHUFARAN aqui como descriptores, sin
// rehacer el visor: cada indicador declara su nombre, su dimension, sus trazos y de donde
// saca sus valores. El registro esta VACIO a proposito -- declarar descriptores sin su
// productor seria codigo muerto (I-04). Lo que se fija ahora es el CONTRATO de enchufe.
//
// ─────────────────────────────────────────────────────────────────────────────
// CRITICO 1 de I-01 (para P08b, NO para ahora, pero se documenta donde tocara):
// cuando se registre un indicador REAL con la API de indicadores de KLineChart, su
// funcion `calc` devuelve un ARRAY alineado POR POSICION con las velas (data[i] <-> el
// indicador de la vela i), NO un objeto indexado por timestamp. La documentacion oficial
// sugiere lo segundo y MIENTE: fiarse de ella alinea los valores con el timestamp
// equivocado y el indicador se dibuja corrido. El PRIMER indicador real exige una
// comprobacion EMPIRICA de 5 minutos de ese contrato (imprimir indice vs timestamp sobre
// datos reales) ANTES de fiarse. Este visor no registra indicadores, asi que hoy no se
// toca; queda escrito aqui para que P08b no lo redescubra a base de un bug.
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Dimension del indicador (regla de dimension, INFORME_TRADINGVIEW seccion 2.7):
 *  - "overlay": se dibuja SOBRE el precio, en la misma escala (p.ej. medias, pivots).
 *  - "panel": va en un panel APARTE, con su propia escala (p.ej. RSI 0-100, footprint).
 * Confundirlas aplasta la escala del precio o esconde el indicador.
 */
export type DataSourceDimension = "overlay" | "panel";

/**
 * De donde salen los valores del indicador:
 *  - Via A: el valor viene PEGADO a la vela (un campo mas del propio flujo de velas).
 *  - Via B: el valor llega por un stream aparte (datasource.*) y se casa con la vela por
 *    TIMESTAMP, mediante un Map open_time -> valor. `stream` nombra ese flujo.
 */
export type DataSourceFetch =
  | { readonly via: "A" }
  | { readonly via: "B"; readonly stream: string };

/** Un trazo dibujable del indicador (una linea/serie con su clave y su etiqueta). */
export interface DataSourceTrace {
  readonly key: string;
  readonly label: string;
}

/** El descriptor que un indicador declara para enchufarse al visor sin tocarlo. */
export interface DataSourceDescriptor {
  readonly name: string;
  readonly dimension: DataSourceDimension;
  readonly traces: readonly DataSourceTrace[];
  readonly fetch: DataSourceFetch;
}

/**
 * El registro de descriptores. VACIO hoy: la forma esta lista, los productores llegan en
 * P08b (RSI) y P08c (pivotphase / divergencias / footprint). Cuando se anada el primero,
 * antes hay que resolver el CRITICO 1 de arriba.
 */
export const DATASOURCES: readonly DataSourceDescriptor[] = [];
