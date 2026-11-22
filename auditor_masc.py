from typing import Optional
from pydantic import ValidationError
from esquemas import ClausulaExtraida
from lector_documentos import extraer_documento, verificar_cita_en_texto


def auditar_clausula_masc(
    datos_clausula: dict,
    ruta_archivo: Optional[str] = None,
    texto_documento: Optional[str] = None,
) -> dict:
    """
    Valida estructuralmente una cláusula extraída (Pydantic), verifica de forma
    determinista que la cita textual exista literalmente en el documento fuente y
    detecta patologías contractuales conforme al Título Cuarto del Libro Quinto
    del Código de Comercio (CCom).
    """
    # 1. Validación estricta del esquema de entrada
    try:
        clausula = ClausulaExtraida(**datos_clausula)
    except ValidationError as exc:
        return {
            "estado": "ERROR_VALIDACION",
            "mensaje": "Los datos extraídos de la cláusula no cumplen el esquema estructural.",
            "errores": exc.errors(include_url=False),
        }

    # 2. Obtención del texto fuente (Fail-Closed si falta o no es legible)
    if ruta_archivo:
        res_doc = extraer_documento(ruta_archivo)
        if res_doc["estado"] != "OK":
            return res_doc
        texto_fuente = res_doc["texto_completo"]
        origen_documento = res_doc["archivo"]
    elif texto_documento and texto_documento.strip():
        texto_fuente = texto_documento
        origen_documento = "texto_directo_en_memoria"
    else:
        return {
            "estado": "FALTAN_DATOS",
            "mensaje": "Debe proporcionarse 'ruta_archivo' o 'texto_documento' para cotejar la cita textual.",
        }

    # 3. Verificación determinista de evidence span (anti-alucinación)
    if not verificar_cita_en_texto(clausula.cita_textual_clausula, texto_fuente):
        return {
            "estado": "CITA_NO_VERIFICADA",
            "mensaje": "Bloqueo Fail-Closed: la cita textual reportada no coincide literalmente con el documento fuente.",
            "origen_documento": origen_documento,
            "cita_reportada": clausula.cita_textual_clausula,
        }

    # 4. Motor determinista de reglas sobre patologías MASC / arbitrales
    hallazgos = []
    es_arbitral = clausula.mecanismo_principal in {"arbitraje", "escalonada"}

    # Regla P1: Sumisión contradictoria (arbitraje + tribunales locales)
    if es_arbitral and clausula.somete_tambien_a_tribunales_locales:
        hallazgos.append({
            "codigo": "P1_JURISDICCION_CONTRADICTORIA",
            "severidad": "CRITICA",
            "titulo": "Cláusula patológica por sumisión simultánea a arbitraje y tribunales locales",
            "detalle": "El acuerdo somete controversias a arbitraje y a la vez reserva competencia de fondo a tribunales estatales, lo que genera riesgo de ineficacia o litigio paralelo sobre competencia.",
            "fundamento": "Art. 1424, párr. 1, del Código de Comercio (remisión obligatoria al arbitraje salvo acuerdo nulo, ineficaz o de ejecución imposible).",
        })

    # Regla P2: Mecanismo ambiguo o cláusula blanca inoperante
    if clausula.mecanismo_principal == "ambiguo":
        hallazgos.append({
            "codigo": "P2_MECANISMO_AMBIGUO",
            "severidad": "CRITICA",
            "titulo": "Indefinición del mecanismo de solución de controversias",
            "detalle": "La redacción no permite determinar de manera inequívoca si las partes pactaron arbitraje, mediación vinculante o jurisdicción estatal.",
            "fundamento": "Arts. 1423, párr. 1, y 1424, párr. 1, del Código de Comercio (certeza del acuerdo de sumisión).",
        })

    # Regla P3: Cláusula escalonada sin plazo máximo de agotamiento
    if (clausula.mecanismo_principal == "escalonada" or clausula.tiene_etapa_previa_obligatoria) and clausula.plazo_etapa_previa_dias is None:
        hallazgos.append({
            "codigo": "P3_ESCALAMIENTO_SIN_PLAZO",
            "severidad": "ALTA",
            "titulo": "Etapa previa obligatoria sin plazo máximo de vencimiento",
            "detalle": "Se exige negociación o mediación previa antes de acudir a arbitraje o juicio, pero no se fija un término máximo en días. Una parte dilatoria puede bloquear el acceso al procedimiento principal alegando incumplimiento de condición suspensiva.",
            "fundamento": "Art. 1424, párr. 1, del Código de Comercio; principios de ejecutabilidad de convenios MASC (LGMASC, DOF 26-01-2024; artículos específicos pendientes de cotejo primario).",
        })

    # Regla P4: Arbitraje sin sede designada
    if es_arbitral and not clausula.sede_arbitraje:
        hallazgos.append({
            "codigo": "P4_AUSENCIA_SEDE_ARBITRAL",
            "severidad": "ALTA",
            "titulo": "Omisión de sede jurídica del arbitraje",
            "detalle": "No se designó lugar del arbitraje. La sede determina la ley procesal arbitral (lex arbitri) y el juez competente para medidas cautelares y nulidad del laudo.",
            "fundamento": "Art. 1436, párr. 1, y art. 1457, fracc. I, inciso a) y fracc. II, del Código de Comercio.",
        })

    # Regla P5: Omisión de institución administradora o reglas procedimentales
    if es_arbitral and not clausula.institucion_administradora:
        hallazgos.append({
            "codigo": "P5_SIN_INSTITUCION_O_REGLAS",
            "severidad": "MEDIA",
            "titulo": "Omisión de institución administradora o reglamento arbitral",
            "detalle": "No se especifica si el arbitraje es institucional (ej. CAM, CANACO, ICC) o ad hoc bajo reglas determinadas, lo que obliga a aplicar el procedimiento supletorio legal.",
            "fundamento": "Arts. 1426, párr. 1, y 1435, párrs. 1 y 2, del Código de Comercio.",
        })

    # Regla P6: Número par de árbitros
    if es_arbitral and clausula.numero_arbitros is not None and clausula.numero_arbitros % 2 == 0:
        hallazgos.append({
            "codigo": "P6_NUMERO_PAR_ARBITROS",
            "severidad": "MEDIA",
            "titulo": f"Tribunal arbitral integrado por número par ({clausula.numero_arbitros} árbitros)",
            "detalle": "Un tribunal par carece de mayoría natural para emitir el laudo y eleva el riesgo de estancamiento deliberativo.",
            "fundamento": "Art. 1426, párr. 1, del Código de Comercio (libertad de configuración del tribunal).",
        })

    # Regla P7: Omisión de idioma o ley aplicable al fondo
    if es_arbitral and (not clausula.idioma or not clausula.ley_aplicable_fondo):
        faltantes_sec = []
        if not clausula.idioma:
            faltantes_sec.append("idioma del procedimiento (art. 1438, párr. 1, CCom)")
        if not clausula.ley_aplicable_fondo:
            faltantes_sec.append("derecho aplicable al fondo del litigio (art. 1445, párrs. 1 y 2, CCom)")
        hallazgos.append({
            "codigo": "P7_OMISION_IDIOMA_O_LEY_FONDO",
            "severidad": "BAJA",
            "titulo": "Omisión de reglas supletorias de idioma o ley sustantiva",
            "detalle": f"A falta de pacto expreso, el tribunal arbitral deberá determinar: {', '.join(faltantes_sec)}.",
            "fundamento": "Arts. 1438, párr. 1, y 1445, párrs. 1 y 2, del Código de Comercio.",
        })

    # 5. Clasificación final prudente
    severidades = {h["severidad"] for h in hallazgos}
    if "CRITICA" in severidades or "ALTA" in severidades:
        estado_final = "PATOLOGIA_DETECTADA"
    elif hallazgos:
        estado_final = "OBSERVACIONES_MENORES"
    else:
        estado_final = "CLAUSULA_SIN_ALERTAS_AUTOMATICAS"

    return {
        "estado": estado_final,
        "origen_documento": origen_documento,
        "cita_verificada_en_fuente": True,
        "cita_textual_verificada": clausula.cita_textual_clausula,
        "parametros_analizados": clausula.model_dump(exclude={"cita_textual_clausula"}),
        "total_hallazgos": len(hallazgos),
        "hallazgos": hallazgos,
        "alcance_y_limites": [
            "Verifica coincidencia literal de la cita en el documento y reglas estructurales bajo el Título Cuarto del Libro Quinto del Código de Comercio.",
            "No verifica facultades de representación de los firmantes, vigencia de reglamentos institucionales ni artículos específicos de la LGMASC no indexados.",
        ],
    }