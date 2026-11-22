# Auditor de Cláusulas MASC / Arbitrales y Simulador BATNA–EMV (`v0.3`)

Servidor local bajo el **Model Context Protocol (MCP)** que expone herramientas deterministas en Python (`Pydantic v2` + `decimal.Decimal`) para ser consumidas por un cliente MCP (como Claude Desktop). El servidor **no invoca ningún LLM internamente**: recibe datos estructurados extraídos por el cliente, verifica evidencia documental por atributo, aplica reglas calibradas al **Título Cuarto del Libro Quinto del Código de Comercio (CCom)** y modela escenarios financieros BATNA/VPN/ZOPA.

---

## Arquitectura y Flujo de Datos

```text
[Archivo en ./expedientes/ o AUDITOR_MASC_ROOT_DIR]
          │
          ▼
  lector_documentos.py   ──► Frontera de directorio autorizado (ACCESO_DENEGADO fuera de raíz).
          │                  Límites: 10 MiB en disco, 200 páginas PDF, 500,000 caracteres.
          │                  DOCX: encabezados, cuerpo en orden (párrafos, tablas y subtablas,
          │                  deduplicación horizontal/vertical) y pies de página.
          │                  PDF: cobertura por página (EXTRACCION_PARCIAL si hay páginas sin texto).
          ▼
    [Cliente MCP / LLM]  ──► Extrae atributos con estado ('presente', 'ausente_tras_revision',
          │                  'no_determinado', 'no_aplicable') y 'cita_literal' por campo.
          ▼
     esquemas.py         ──► Validación Pydantic v2: extra="forbid", StrictBool, StrictInt,
          │                  rechazo de espacios vacíos, NaN/Inf y montos > 10^14 MXN.
          ▼
    auditor_masc.py      ──► 1. Localización de cita (literal o normalizando espacios/NFC) con
          │                     índice de bloque, coordenadas y huella SHA-256.
          │                  2. Corroboración léxica por atributo (marca SIN_RESPALDO o
          │                     RESPALDO_NO_CORROBORADO -> REQUIERE_REVISION).
          │                  3. Reglas jurídicas P1–P7 separando etapa previa de mecanismo final.
          ▼
    simulador_emv.py     ──► Cálculo independiente en Decimal con transferencia simétrica de
                             costas reembolsables y redondeo conservador al centavo.
```

---

## Herramientas Expuestas (`servidor_mcp.py`)

| Herramienta MCP | Entrada Tipada | Controles Principales |
| :--- | :--- | :--- |
| `leer_documento_contrato` | `ruta_archivo: str` | Restringido al directorio autorizado (`expedientes/` o `AUDITOR_MASC_ROOT_DIR`). Reporta cobertura parcial en PDFs mixtos y marca el texto como datos no confiables. |
| `auditar_clausula_masc` | `ClausulaExtraida` + fuente | Exige evidencia por atributo. Diferencia `archivo_autorizado` de `texto_directo_no_verificado_en_disco`. Si falta respaldo o cobertura, devuelve `REQUIERE_REVISION`. |
| `simular_batna_emv` | `ParametrosLitigioEMV` | Transferencia simétrica de costas reembolsables entre partes, opción de desistir (`actor_puede_desistir_sin_costo`), redondeo conservador al centavo y causa explícita de `SIN_ZOPA`. |
| `simular_matriz_sensibilidad_emv` | `ParametrosSensibilidadEMV` | Evalúa hasta 10 probabilidades de éxito en una corrida (default: `0.35`, `0.50`, `0.65`, `0.80`) reutilizando el núcleo de `simular_batna_emv`. |

---

## Reglas Jurídicas (`auditor_masc.py`)

Las reglas arbitrales (`P4`–`P7`) solo se ejecutan cuando `mecanismo_final == "arbitraje"` está corroborado; una cláusula escalonada hacia `jurisdiccion_estatal` no dispara falsos positivos arbitrales.

| Código | Severidad | Condición Evaluada | Fundamento Referencial |
| :--- | :--- | :--- | :--- |
| `P1_JURISDICCION_CONTRADICTORIA` | `CRITICA` | Arbitraje + sumisión simultánea corroborada a tribunales locales sobre el fondo. | Arts. 1424, párrs. 1–2; 1425; 1464, fracc. I, CCom |
| `P2_MECANISMO_AMBIGUO` | `CRITICA` | `mecanismo_final == "ambiguo"` (sin certeza de foro vinculante). | Arts. 1416, fracc. I; 1423, CCom; art. 4, fracc. III, LGMASC |
| `P3_ESCALAMIENTO_SIN_PLAZO` | `ALTA` | Etapa previa obligatoria sin plazo en días ni condición objetiva de cierre. | Riesgo de ejecutabilidad; art. 1424, párr. 1, CCom |
| `P5B_INSTITUCION_NO_CORROBORADA` | `ALTA` | Institución arbitral extraída fuera del catálogo verificado (dispara `REQUIERE_REVISION`). | Art. 1424, párr. 1, CCom |
| `P4_OMISION_PACTO_SEDE_ARBITRAL` | `MEDIA` | Ausencia tras revisión de pacto sobre sede arbitral (admite regla supletoria legal; no clasifica como patología invalidante). | Art. 1436, párrs. 1–2, CCom |
| `P5_SIN_INSTITUCION_NI_REGLAMENTO` | `MEDIA` | Ausencia simultánea de institución administradora y de reglamento arbitral. | Arts. 1417, fracc. II; 1435, párrs. 1–2, CCom |
| `P6_NUMERO_PAR_ARBITROS` | `MEDIA` | Tribunal arbitral par (válido legalmente, pero con riesgo operativo de empate). | Arts. 1426 y 1446, CCom |
| `P7_OMISION_PACTO_IDIOMA_O_LEY_FONDO` | `BAJA` | Ausencia tras revisión de pacto expreso sobre idioma o normas de fondo. | Arts. 1438, párr. 1; 1445, párrs. 1–2, CCom |

---

## Convención Financiera (`simulador_emv.py`)

* **Descuento temporal:** Los flujos del fallo y de costas se descuentan desde `duracion_juicio_meses` mediante $(1 + r)^{t/12}$ en `Decimal` (precisión finita para exponentes fraccionarios). Los costos propios de litigio y MASC se imputan a valor presente ($t = 0$).
* **Costas reembolsables vs. costos externos:** `costas_reembolsables_entre_partes_mxn` se carga al perdedor y se acredita al vencedor (transferencia simétrica que no fabrica ZOPA artificial); `costos_externos_perdedor_mxn` modela gastos irrecuperables a terceros.
* **Redondeo conservador al centavo:** Antes de declarar `ZOPA_VIABLE`, el piso del actor redondea hacia arriba (`ROUND_CEILING`) y el techo del demandado hacia abajo (`ROUND_FLOOR`). Si no existe un importe pagable en centavos que satisfaga a ambos, el motor devuelve `SIN_ZOPA` con `causa_sin_zopa`.
* **Supuestos declarados:** Probabilidad compartida entre actor y demandado, cobro íntegro al ganar, neutralidad al riesgo y alternativa única de litigar (salvo que `actor_puede_desistir_sin_costo=True`).

---

## Alcance y Limitaciones Explícitas

* **Cobertura documental:** No ejecuta OCR en PDFs escaneados; en `.docx` no procesa notas al pie, comentarios ni cuadros de texto flotantes (si detecta cuadros de texto, reporta `EXTRACCION_PARCIAL`).
* **Corroboración léxica:** Comprueba presencia de términos y alias dentro de la cita localizada, pero no sustituye la interpretación jurídica humana sobre el sentido, excepciones o alcance de una cláusula.
* **Materias fuera de alcance:** No evalúa arbitrabilidad objetiva de la materia, facultades de representación de los firmantes, cláusulas asimétricas ni vigencia de reglamentos institucionales.

---

## Instalación y Pruebas Locales

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe test_suite.py
```

Configuración en `claude_desktop_config.json` (opcionalmente fijando `AUDITOR_MASC_ROOT_DIR` en `env`):

```json
{
  "mcpServers": {
    "auditor-masc-emv": {
      "command": "C:\\RUTA\\A\\auditor-masc-emv-mcp\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\RUTA\\A\\auditor-masc-emv-mcp\\servidor_mcp.py"
      ]
    }
  }
}
```