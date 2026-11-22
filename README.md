# Auditor de Cláusulas MASC / Arbitrales y Simulador BATNA–EMV (`v0.3.1`)

Servidor local bajo el **Model Context Protocol (MCP)** que expone herramientas deterministas en Python (`Pydantic v2` + `decimal.Decimal`) para ser consumidas por un cliente MCP (como Claude Desktop). El servidor **no invoca ningún LLM internamente**: recibe datos estructurados extraídos por el cliente, verifica localización literal y coincidencia léxica por atributo, detecta marcadores de negación/exclusión, aplica reglas calibradas al **Título Cuarto del Libro Quinto del Código de Comercio (CCom)** y modela escenarios financieros BATNA/VPN/ZOPA.

---

## Arquitectura y Flujo de Datos

```text
[Archivo en ./expedientes/ o AUDITOR_MASC_ROOT_DIR]
          │
          ▼
  lector_documentos.py   ──► Frontera de directorio autorizado (ACCESO_DENEGADO fuera de raíz).
          │                  Instantánea única en memoria (hash SHA-256 y parseo sobre los
          │                  mismos bytes). Límites: 10 MiB, 200 págs PDF, 5,000 bloques,
          │                  500,000 caracteres.
          │                  DOCX: encabezados, cuerpo en orden estricto (párrafos, tablas y
          │                  subtablas, deduplicación horizontal/vertical) y pies de página.
          │                  PDF: cobertura por página (EXTRACCION_PARCIAL ante páginas sin
          │                  texto, texto escaso o imágenes /Image embebidas).
          ▼
    [Cliente MCP / LLM]  ──► Extrae atributos con estado ('presente', 'ausente_tras_revision',
          │                  'no_determinado', 'no_aplicable') y 'cita_literal' por campo.
          ▼
     esquemas.py         ──► Validación Pydantic v2: extra="forbid", StrictBool, StrictInt,
          │                  rechazo de espacios vacíos, NaN/Inf, escala excesiva y > 10^14 MXN.
          ▼
    auditor_masc.py      ──► 1. Localización con mapa de índices al texto original y prohibición
          │                     de unir bloques sobre lagunas físicas de páginas.
          │                  2. Distinción entre COINCIDENCIA_LEXICA, SIN_RESPALDO,
          │                     RESPALDO_NO_CORROBORADO y COINCIDENCIA_CON_NEGACION_O_EXCLUSION
          │                     (priorizando siempre REQUIERE_REVISION).
          │                  3. Reglas jurídicas P1–P7 (incluyendo P1R cautelares y P5B).
          ▼
    simulador_emv.py     ──► Cálculo en Decimal (50 dígitos intermedios) con identidad exacta
                             cuando J == T, transferencia simétrica de costas reembolsables
                             y redondeo conservador al centavo.
```

---

## Herramientas Expuestas (`servidor_mcp.py`)

| Herramienta MCP | Entrada Tipada | Controles Principales |
| :--- | :--- | :--- |
| `leer_documento_contrato` | `ruta_archivo: str` | Restringido a `expedientes/` o `AUDITOR_MASC_ROOT_DIR`. Reporta cobertura parcial en PDFs mixtos o con imágenes y marca el contenido como datos no confiables. |
| `auditar_clausula_masc` | `ClausulaExtraida` + fuente | Exige cita por atributo. Bloquea aprobación limpia ante negaciones (`E3`), falta de respaldo (`E1`/`E2`) o cobertura parcial, devolviendo `REQUIERE_REVISION`. |
| `simular_batna_emv` | `ParametrosLitigioEMV` | Transferencia simétrica de costas reembolsables, identidad exacta cuando `duracion_juicio_meses == duracion_masc_meses`, opción de desistir (`actor_puede_desistir_sin_costo` + `demandado_conoce_retiro_actor`) y redondeo conservador al centavo. |
| `simular_matriz_sensibilidad_emv` | `ParametrosSensibilidadEMV` | Evalúa hasta 10 probabilidades de éxito en una corrida (default: `0.35`, `0.50`, `0.65`, `0.80`) propagando cualquier error de validación o aritmético. |

---

## Reglas Jurídicas (`auditor_masc.py`)

Las reglas arbitrales (`P4`–`P7`) solo se ejecutan cuando `mecanismo_final == "arbitraje"` presenta coincidencia léxica sin negaciones; una cláusula escalonada hacia `jurisdiccion_estatal` no dispara falsos positivos arbitrales.

| Código | Severidad | Condición Evaluada | Fundamento Referencial |
| :--- | :--- | :--- | :--- |
| `P1_JURISDICCION_CONTRADICTORIA` | `CRITICA` | Arbitraje + mención judicial sin términos de cautelares o auxilio (exige revisión humana). | Arts. 1424, párrs. 1–2; 1425; 1464, fracc. I, CCom |
| `P1R_MENCION_JUDICIAL_POSIBLE_CAUTELAR_O_AUXILIO` | `MEDIA` | Arbitraje + mención judicial con términos compatibles con medidas cautelares o auxilio. | Art. 1425, párr. único, CCom |
| `P2_MECANISMO_AMBIGUO` | `CRITICA` | `mecanismo_final == "ambiguo"` (exige revisión humana del consentimiento y anexos). | Arts. 1416, fracc. I; 1423, CCom; art. 4, fracc. III, LGMASC |
| `P3_ESCALAMIENTO_SIN_PLAZO` | `ALTA` | Etapa previa obligatoria sin plazo en días ni condición objetiva de cierre. | Riesgo de ejecutabilidad; art. 1424, párr. 1, CCom (o art. 78 CCom en vía judicial) |
| `P4_OMISION_PACTO_SEDE_ARBITRAL` | `MEDIA` | Ausencia tras revisión de pacto sobre sede arbitral (admite regla supletoria legal). | Art. 1436, párrs. 1–2, CCom |
| `P5_SIN_INSTITUCION_NI_REGLAMENTO` | `MEDIA` | Ausencia simultánea de institución administradora y reglamento (cuando no es `ad_hoc`). | Arts. 1417, fracc. II; 1435, párrs. 1–2, CCom |
| `P5B_IDENTIDAD_INSTITUCIONAL_PENDIENTE_COTEJO` | `MEDIA` | Denominación institucional fuera de la lista exacta de referencia interna. | Verificación operativa para descartar inejecutabilidad (art. 1424, párr. 1, CCom) |
| `P5C_AD_HOC_REPORTADO_COMO_INSTITUCION` | `BAJA` | Modalidad `ad hoc` ingresada en el campo de institución administradora. | Art. 1435, párrs. 1–2, CCom |
| `P6_NUMERO_PAR_ARBITROS` | `MEDIA` | Tribunal arbitral par (válido legalmente, pero con riesgo operativo de empate sobre el fondo). | Arts. 1426 y 1446, CCom |
| `P7_OMISION_PACTO_IDIOMA_O_LEY_FONDO` | `BAJA` | Ausencia tras revisión de pacto expreso sobre idioma o normas de fondo. | Arts. 1438, párr. 1; 1445, párrs. 1–2, CCom |

---

## Convención Financiera (`simulador_emv.py`)

* **Capitalización/descuento relativo exacto:** Los flujos del fallo y de costas se trasladan desde `duracion_juicio_meses` ($J$) a `duracion_masc_meses` ($T$) mediante $(1 + r)^{(T - J)/12}$ con 50 dígitos intermedios en `Decimal`, garantizando identidad exacta `1` cuando $J = T$. Los costos propios de litigio y MASC se imputan en $t = 0$.
* **Costas reembolsables vs. costos externos:** `costas_reembolsables_entre_partes_mxn` se carga al perdedor y se acredita al vencedor (transferencia simétrica); `costos_externos_perdedor_mxn` modela gastos irrecuperables a terceros.
* **Consistencia ante retiro del actor:** Si `actor_puede_desistir_sin_costo=True` y el VPN de litigar del actor es negativo, su BATNA efectivo es `0.00`. Si además `demandado_conoce_retiro_actor=True`, el costo sin convenio del demandado también cae a `0.00`.
* **Redondeo conservador al centavo:** El piso del actor redondea hacia arriba (`ROUND_CEILING`, con cota mínima `$0.01` salvo que `permitir_convenio_cero=True`) y el techo del demandado hacia abajo (`ROUND_FLOOR`).

---

## Alcance y Limitaciones Explícitas

* **Corroboración léxica vs. verdad jurídica:** El estado `COINCIDENCIA_LEXICA` acredita que los términos del atributo aparecen en la cita localizada sin marcadores de negación/exclusión detectados, pero no sustituye la interpretación jurídica humana sobre el alcance o validez de la cláusula.
* **Cobertura documental:** No ejecuta OCR en PDFs escaneados; en `.docx` no procesa notas al pie, comentarios ni cuadros de texto flotantes (si detecta cuadros de texto en cuerpo, encabezados o pies, reporta `EXTRACCION_PARCIAL`).
* **Materias fuera de alcance:** No evalúa arbitrabilidad objetiva de la materia, facultades de representación de firmantes, cláusulas asimétricas ni vigencia de reglamentos institucionales.

---

## Instalación y Pruebas Locales

Probado en Windows (Python 3.14, `mcp` 2.3.0, `pydantic` 2.14.3, `python-docx` 1.2.0, `pypdf` 6.21.4) y en GitHub Actions Ubuntu (Python 3.12):

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