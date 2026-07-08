/**
 * Check 7.2 - fronteras del frontend (DOC_ESTRUCTURA sec.6, ADR-019).
 * Sobre el esqueleto vacio no hay modulos y el check pasa en verde;
 * las reglas quedan vivas para cuando llegue codigo real (P12a+).
 */
module.exports = {
  forbidden: [
    {
      name: "no-circular",
      comment: "Sin dependencias circulares.",
      severity: "error",
      from: {},
      to: { circular: true },
    },
    {
      name: "app-core-no-device-web",
      comment: "app-core usa device-ports (interfaces), nunca device-web.",
      severity: "error",
      from: { path: "^frontend/src/app-core" },
      to: { path: "^frontend/src/device-web" },
    },
    {
      name: "ui-core-no-device-web",
      comment: "ui-core no llama a la API ni a adapters; consume via app-core.",
      severity: "error",
      from: { path: "^frontend/src/ui-core" },
      to: { path: "^frontend/src/device-web" },
    },
    {
      name: "no-logic-in-generated",
      comment: "Nadie define codigo dentro de generated (ADR-006).",
      severity: "error",
      from: { path: "^frontend/src/shared-contracts/generated" },
      to: { pathNot: "^frontend/src/shared-contracts/generated" },
    },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
  },
};
