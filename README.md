# Auditor de Cláusulas MASC / Arbitrales y Simulador BATNA–EMV (`v0.2`)

Servidor local bajo el **Model Context Protocol (MCP)** que desacopla la extracción semántica realizada por un modelo de lenguaje de la verificación normativa y financiera determinista en Python (`Pydantic v2` + `decimal.Decimal`).

El sistema resuelve dos tareas operativas en derecho mercantil y mecanismos alternativos de solución de controversias (MASC):
1. **Auditoría trazable de cláusulas compromisorias y escalonadas** en contratos (`.txt`, `.docx` —incluyendo tablas— y `.pdf`) mediante verificación literal de citas (*evidence spans*) y reglas basadas en el **Título Cuarto del Libro Quinto del Código de Comercio (CCom)**.
2. **Modelado financiero determinista de negociación (BATNA / EMV / VPN / ZOPA)** para comparar racionalmente el valor esperado descontado de litigar frente a cerrar un convenio en MASC.

---

## Arquitectura del Sistema

```text
[Contrato .txt/.docx/.pdf]
          │
          ▼
  lector_documentos.py   ──► Extrae párrafos y tablas (deduplica celdas combinadas).
          │                  Bloqueo Fail-Closed ante PDF sin capa de texto o >10 MB.
          ▼
  [LLM vía MCP Client]   ──► Extrae atributos estructurados + cita textual literal.
          │
          ▼
     esquemas.py         ──► Validación estricta Pydantic v2 (extra="forbid", sin NaN/bool).
          │
          ▼
    auditor_masc.py      ──► 1. Cotejo determinista de subcadena en el texto fuente.
          │                     Si no coincide letra por letra -> CITA_NO_VERIFICADA.
          │                  2. Motor de 7 reglas sobre patologías MASC/arbitrales (CCom).
          ▼
    simulador_emv.py     ──► Cálculo exacto en Decimal (ROUND_HALF_UP) de EMV, VPN, BATNA,
                             ZOPA y matriz de sensibilidad multi-escenario.