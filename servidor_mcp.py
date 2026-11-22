from typing import Optional
from mcp.server.mcpserver import MCPServer
from lector_documentos import extraer_documento
from auditor_masc import auditar_clausula_masc as _auditar_clausula
from simulador_emv import (
    simular_escenario_batna_emv as _simular_emv,
    simular_matriz_sensibilidad_emv as _simular_matriz_emv,
)

mcp = MCPServer("AuditorMASCyEMV")


@mcp.tool()
def leer_documento_contrato(ruta_archivo: str) -> dict:
    """
    Extrae el texto estructurado por párrafos y tablas de un contrato en formato
    .txt, .docx o .pdf.
    REGLA CRÍTICA:
    - Si devuelve 'PDF_SIN_TEXTO_EXTRAIBLE', 'ERROR_ARCHIVO' o 'FUERA_DE_ALCANCE',
      DETENTE e informa al usuario. PROHIBIDO inventar el contenido del documento.
    """
    return extraer_documento(ruta_archivo=ruta_archivo)


@mcp.tool()
def auditar_clausula_masc(
    datos_clausula: dict,
    ruta_archivo: Optional[str] = None,
    texto_documento: Optional[str] = None,
) -> dict:
    """
    Valida estructuralmente (Pydantic) una cláusula MASC o arbitral extraída por el LLM,
    verifica letra por letra que 'cita_textual_clausula' exista en el documento fuente y
    detecta patologías contractuales bajo el Título Cuarto del Libro Quinto del Código
    de Comercio.

    Claves esperadas en 'datos_clausula':
    - mecanismo_principal (obligatorio): 'arbitraje' | 'mediacion' | 'conciliacion' |
      'negociacion' | 'escalonada' | 'jurisdiccion_estatal' | 'ambiguo'
    - cita_textual_clausula (obligatorio, min 15 chars): fragmento literal del contrato
    - institucion_administradora (opcional str): ej. 'CAM', 'CANACO', 'ICC', 'ad_hoc'
    - sede_arbitraje (opcional str): ciudad o país sede
    - idioma (opcional str)
    - ley_aplicable_fondo (opcional str)
    - numero_arbitros (opcional int, 1-9)
    - tiene_etapa_previa_obligatoria (bool, default False)
    - plazo_etapa_previa_dias (opcional int >= 1)
    - somete_tambien_a_tribunales_locales (bool, default False)

    REGLAS CRÍTICAS:
    1. Copia 'cita_textual_clausula' exactamente como aparece en el texto fuente.
    2. Si devuelve 'CITA_NO_VERIFICADA' o 'ERROR_VALIDACION', corrige la extracción
       leyendo el texto literal; nunca inventes palabras.
    """
    return _auditar_clausula(
        datos_clausula=datos_clausula,
        ruta_archivo=ruta_archivo,
        texto_documento=texto_documento,
    )


@mcp.tool()
def simular_batna_emv(parametros: dict) -> dict:
    """
    Modela determinísticamente en Decimal el Valor Monetario Esperado (EMV) y el Valor
    Presente Neto (VPN) de litigar frente a convenir en MASC, calculando el BATNA de
    actor y demandado y la Zona de Posible Acuerdo (ZOPA).

    Claves obligatorias en 'parametros' (enviar montos y tasas como texto decimal):
    - monto_reclamado_mxn (> 0)
    - probabilidad_exito_actor (0 a 1, ej. '0.65')
    - costos_litigio_actor_mxn (>= 0)
    - costos_litigio_demandado_mxn (>= 0)
    - duracion_juicio_meses (int 1 a 120)
    - tasa_descuento_anual (0 a 1, ej. '0.10')
    - costos_masc_actor_mxn (>= 0)
    - costos_masc_demandado_mxn (>= 0)
    Claves opcionales:
    - duracion_masc_meses (int 0 a 24, default 1)
    - riesgo_costas_perdedor_mxn (>= 0, default '0.00')
    """
    return _simular_emv(parametros=parametros)


@mcp.tool()
def simular_matriz_sensibilidad_emv(parametros: dict) -> dict:
    """
    Evalúa en una sola corrida múltiples probabilidades de éxito (por defecto
    ['0.35', '0.50', '0.65', '0.80']) para mostrar cómo se desplazan el BATNA y la ZOPA.
    Acepta las mismas claves base que 'simular_batna_emv' sustituyendo
    'probabilidad_exito_actor' por la lista opcional 'probabilidades_exito_actor'.
    """
    return _simular_matriz_emv(parametros=parametros)


if __name__ == "__main__":
    mcp.run()