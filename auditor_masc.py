import re
import unicodedata
from typing import Optional
from pydantic import ValidationError
from esquemas import ClausulaExtraida, DatoBooleano, DatoEntero, DatoTexto
from lector_documentos import (
    MAX_CARACTERES_EXTRAIDOS,
    calcular_sha256_texto,
    extraer_documento,
    localizar_cita_en_bloques,
)

STOPWORDS_ES = {
    "de", "del", "la", "las", "el", "los", "en", "por", "con", "para", "un", "una", "y", "o", "al"
}

PALABRAS_NUMERO = {
    1: ("1", "un", "uno", "una", "unico", "unica"),
    2: ("2", "dos"),
    3: ("3", "tres"),
    4: ("4", "cuatro"),
    5: ("5", "cinco"),
    6: ("6", "seis"),
    7: ("7", "siete"),
    8: ("8", "ocho"),
    9: ("9", "nueve"),
    10: ("10", "diez"),
    15: ("15", "quince"),
    20: ("20", "veinte"),
    30: ("30", "treinta"),
    45: ("45", "cuarenta y cinco"),
    60: ("60", "sesenta"),
    90: ("90", "noventa"),
}

ALIAS_TEXTO = {
    "ciudad de mexico": ("ciudad de mexico", "cdmx", "distrito federal"),
    "espanol": ("espanol", "castellano"),
    "ingles": ("ingles", "english"),
    "codigo de comercio": ("codigo de comercio", "ccom"),
    "cam": ("cam", "centro de arbitraje de mexico"),
    "canaco": ("canaco", "camara nacional de comercio"),
    "icc": ("icc", "cci", "camara de comercio internacional", "international chamber of commerce"),
    "icdr": ("icdr", "aaa", "american arbitration association"),
    "uncitral": ("uncitral", "cnudmi"),
}

INSTITUCIONES_ARBITRALES_CONOCIDAS = {
    "cam",
    "centro de arbitraje de mexico",
    "canaco",
    "camara nacional de comercio de la ciudad de mexico",
    "camara nacional de comercio",
    "icc",
    "cci",
    "camara de comercio internacional",
    "international chamber of commerce",
    "icdr",
    "aaa",
    "lcia",
    "ciac",
    "cemca",
    "ad_hoc",
    "ad hoc",
}

PALABRAS_CLAVE_MECANISMO = {
    "arbitraje": ("arbitr", "laudo"),
    "jurisdiccion_estatal": ("juez", "jueces", "tribunal", "tribunales", "juzgado", "fuero", "judicial"),
    "mediacion": ("mediacion", "mediador"),
    "conciliacion": ("conciliacion", "conciliador"),
    "negociacion": ("negociacion", "negociar", "buena fe", "amistosa"),
}

PALABRAS_CLAVE_TRIBUNALES_LOCALES = (
    "juez", "jueces", "tribunal", "tribunales", "juzgado", "juzgados", "fuero", "jurisdiccion", "judicial"
)

ORDEN_SEVERIDAD = {"CRITICA": 4, "ALTA": 3, "MEDIA": 2, "BAJA": 1}


def _normalizar_lexico(texto: str) -> str:
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sin_acentos).strip()


def _corroborar_texto_en_cita(valor: str, cita: str) -> bool:
    val_norm = _normalizar_lexico(valor)
    cita_norm = _normalizar_lexico(cita)

    # Revisar alias conocidos
    for clave, variantes in ALIAS_TEXTO.items():
        if val_norm == clave or val_norm in variantes:
            if any(re.search(rf"\b{re.escape(v)}\b", cita_norm) for v in variantes):
                return True

    tokens = [
        t for t in re.findall(r"[a-z0-9]+", val_norm)
        if len(t) >= 2 and t not in STOPWORDS_ES
    ]
    if not tokens:
        return val_norm in cita_norm
    return all(re.search(rf"\b{re.escape(t)}\b", cita_norm) for t in tokens)


def _corroborar_entero_en_cita(valor: int, cita: str) -> bool:
    cita_norm = _normalizar_lexico(cita)
    candidatos = PALABRAS_NUMERO.get(valor, (str(valor),))
    return any(re.search(rf"\b{re.escape(c)}\b", cita_norm) for c in candidatos)


def _auditar_atributo(
    nombre: str,
    dato: DatoTexto | DatoEntero | DatoBooleano,
    bloques: list[dict],
    palabras_clave_bool: Optional[tuple[str, ...]] = None,
) -> dict:
    """Verifica existencia de cita en el documento fuente y corroboración léxica del valor."""
    if dato.estado != "presente":
        return {
            "atributo": nombre,
            "estado_declarado": dato.estado,
            "estado_evidencia": dato.estado.upper(),
            "valor": None,
            "ubicacion": None,
        }

    if not dato.cita_literal:
        return {
            "atributo": nombre,
            "estado_declarado": "presente",
            "estado_evidencia": "SIN_RESPALDO",
            "valor": dato.valor,
            "motivo": "Atributo marcado como 'presente' sin proporcionar 'cita_literal'.",
            "ubicacion": None,
        }

    loc = localizar_cita_en_bloques(dato.cita_literal, bloques)
    if not loc["verificada"]:
        return {
            "atributo": nombre,
            "estado_declarado": "presente",
            "estado_evidencia": "SIN_RESPALDO",
            "valor": dato.valor,
            "motivo": "La 'cita_literal' del atributo no existe dentro del documento fuente.",
            "ubicacion": loc,
        }

    corroborado = True
    if isinstance(dato, DatoTexto) and dato.valor is not None:
        corroborado = _corroborar_texto_en_cita(dato.valor, dato.cita_literal)
    elif isinstance(dato, DatoEntero) and dato.valor is not None:
        corroborado = _corroborar_entero_en_cita(dato.valor, dato.cita_literal)
    elif isinstance(dato, DatoBooleano) and dato.valor is True and palabras_clave_bool:
        cita_norm = _normalizar_lexico(dato.cita_literal)
        corroborado = any(k in cita_norm for k in palabras_clave_bool)

    if not corroborado:
        return {
            "atributo": nombre,
            "estado_declarado": "presente",
            "estado_evidencia": "RESPALDO_NO_CORROBORADO",
            "valor": dato.valor,
            "motivo": (
                f"La cita existe en el documento, pero no contiene respaldo léxico "
                f"corroborable para el valor afirmado ({dato.valor!r})."
            ),
            "ubicacion": loc,
        }

    return {
        "atributo": nombre,
        "estado_declarado": "presente",
        "estado_evidencia": "VERIFICADO",
        "valor": dato.valor,
        "ubicacion": loc,
    }


def _institucion_es_conocida(nombre: str) -> bool:
    norm = _normalizar_lexico(nombre)
    if norm in INSTITUCIONES_ARBITRALES_CONOCIDAS:
        return True
    return any(re.search(rf"\b{re.escape(inst)}\b", norm) for inst in INSTITUCIONES_ARBITRALES_CONOCIDAS)


def auditar_clausula_masc(
    datos_clausula: dict,
    ruta_archivo: Optional[str] = None,
    texto_documento: Optional[str] = None,
    directorio_autorizado: Optional[str] = None,
) -> dict:
    """
    Audita una cláusula MASC o arbitral con evidencia granular por atributo,
    verificación de cobertura documental y reglas calibradas al Título Cuarto
    del Libro Quinto del Código de Comercio (CCom).
    """
    # 1. Validación estricta de esquema Pydantic v2
    try:
        clausula = ClausulaExtraida(**datos_clausula)
    except ValidationError as exc:
        return {
            "estado": "ERROR_VALIDACION",
            "mensaje": "Los datos extraídos de la cláusula no cumplen el esquema estructural v0.3.",
            "errores": exc.errors(include_url=False),
        }

    # 2. Obtención de bloques del documento fuente (Fail-Closed)
    if ruta_archivo:
        res_doc = extraer_documento(
            ruta_archivo=ruta_archivo,
            directorio_autorizado=directorio_autorizado,
        )
        if res_doc["estado"] not in {"OK", "EXTRACCION_PARCIAL"}:
            return res_doc
        bloques = res_doc["bloques_numerados"]
        origen_fuente = "archivo_autorizado"
        nombre_documento = res_doc["archivo"]
        huella_sha256 = res_doc["huella_sha256"]
        cobertura_documental = res_doc["cobertura"]
    elif texto_documento and texto_documento.strip():
        if len(texto_documento) > MAX_CARACTERES_EXTRAIDOS:
            return {
                "estado": "FUERA_DE_ALCANCE",
                "mensaje": (
                    f"El texto directo supera el límite operativo de "
                    f"{MAX_CARACTERES_EXTRAIDOS} caracteres."
                ),
            }
        lineas = [l.strip() for l in texto_documento.splitlines() if l.strip()]
        bloques = [
            {"indice": i, "tipo": "texto_directo", "origen": f"Bloque {i}", "texto": l}
            for i, l in enumerate(lineas, start=1)
        ]
        origen_fuente = "texto_directo_no_verificado_en_disco"
        nombre_documento = "texto_directo_en_memoria"
        huella_sha256 = calcular_sha256_texto(texto_documento)
        cobertura_documental = {"completa": True, "advertencia": "Texto suministrado directamente por el cliente."}
    else:
        return {
            "estado": "FALTAN_DATOS",
            "mensaje": "Debe proporcionarse 'ruta_archivo' o 'texto_documento' para cotejar la evidencia.",
        }

    # 3. Verificación de la cita principal de la cláusula
    loc_clausula = localizar_cita_en_bloques(clausula.cita_textual_clausula, bloques)
    if not loc_clausula["verificada"]:
        return {
            "estado": "CITA_NO_VERIFICADA",
            "mensaje": "Bloqueo Fail-Closed: la cita principal de la cláusula no existe en el documento fuente.",
            "origen_fuente": origen_fuente,
            "documento": nombre_documento,
            "huella_sha256": huella_sha256,
            "cita_reportada": clausula.cita_textual_clausula,
        }

    # 4. Auditoría de evidencia por atributo (anti-alucinación granular)
    auditoria_evidencia: dict[str, dict] = {}
    alertas_integridad: list[dict] = []

    # Verificar respaldo léxico del mecanismo final
    cita_mec = clausula.cita_mecanismo_final or clausula.cita_textual_clausula
    loc_mec = localizar_cita_en_bloques(cita_mec, bloques)
    if not loc_mec["verificada"]:
        auditoria_evidencia["mecanismo_final"] = {
            "estado_evidencia": "SIN_RESPALDO",
            "valor": clausula.mecanismo_final,
            "motivo": "La 'cita_mecanismo_final' no existe en el documento fuente.",
        }
        alertas_integridad.append({
            "codigo": "E1_ATRIBUTO_SIN_RESPALDO",
            "atributo": "mecanismo_final",
            "detalle": "La cita que respalda el mecanismo final no fue localizada en el documento.",
        })
    elif clausula.mecanismo_final in PALABRAS_CLAVE_MECANISMO:
        cita_mec_norm = _normalizar_lexico(cita_mec)
        claves = PALABRAS_CLAVE_MECANISMO[clausula.mecanismo_final]
        if not any(k in cita_mec_norm for k in claves):
            auditoria_evidencia["mecanismo_final"] = {
                "estado_evidencia": "RESPALDO_NO_CORROBORADO",
                "valor": clausula.mecanismo_final,
                "motivo": (
                    f"La cita no contiene indicadores léxicos del mecanismo "
                    f"afirmado ('{clausula.mecanismo_final}')."
                ),
                "ubicacion": loc_mec,
            }
            alertas_integridad.append({
                "codigo": "E2_RESPALDO_NO_CORROBORADO",
                "atributo": "mecanismo_final",
                "detalle": (
                    f"No se corroboró respaldo léxico para mecanismo_final='{clausula.mecanismo_final}' "
                    "dentro de la cita aportada."
                ),
            })
        else:
            auditoria_evidencia["mecanismo_final"] = {
                "estado_evidencia": "VERIFICADO",
                "valor": clausula.mecanismo_final,
                "ubicacion": loc_mec,
            }
    else:
        auditoria_evidencia["mecanismo_final"] = {
            "estado_evidencia": "VERIFICADO",
            "valor": clausula.mecanismo_final,
            "ubicacion": loc_mec,
        }

    campos_a_auditar = [
        ("institucion_administradora", clausula.institucion_administradora, None),
        ("reglamento_arbitral", clausula.reglamento_arbitral, None),
        ("sede_arbitraje", clausula.sede_arbitraje, None),
        ("idioma", clausula.idioma, None),
        ("ley_aplicable_fondo", clausula.ley_aplicable_fondo, None),
        ("numero_arbitros", clausula.numero_arbitros, None),
        (
            "somete_tambien_a_tribunales_locales",
            clausula.somete_tambien_a_tribunales_locales,
            PALABRAS_CLAVE_TRIBUNALES_LOCALES,
        ),
    ]
    if clausula.etapa_previa is not None:
        campos_a_auditar.extend([
            ("etapa_previa.obligatoria", clausula.etapa_previa.obligatoria, None),
            ("etapa_previa.plazo_dias", clausula.etapa_previa.plazo_dias, None),
            ("etapa_previa.condicion_cierre_objetiva", clausula.etapa_previa.condicion_cierre_objetiva, None),
        ])

    for nombre, dato, kw_bool in campos_a_auditar:
        ev = _auditar_atributo(nombre, dato, bloques, palabras_clave_bool=kw_bool)
        auditoria_evidencia[nombre] = ev
        if ev["estado_evidencia"] in {"SIN_RESPALDO", "RESPALDO_NO_CORROBORADO"}:
            alertas_integridad.append({
                "codigo": (
                    "E1_ATRIBUTO_SIN_RESPALDO"
                    if ev["estado_evidencia"] == "SIN_RESPALDO"
                    else "E2_RESPALDO_NO_CORROBORADO"
                ),
                "atributo": nombre,
                "detalle": ev["motivo"],
            })

    # 5. Motor determinista de reglas jurídicas (solo sobre datos verificados o ausencias declaradas)
    hallazgos: list[dict] = []
    es_arbitral = (
        clausula.mecanismo_final == "arbitraje"
        and auditoria_evidencia["mecanismo_final"]["estado_evidencia"] == "VERIFICADO"
    )

    # Regla P1: Sumisión contradictoria simultánea a arbitraje y tribunales locales
    ev_trib = auditoria_evidencia["somete_tambien_a_tribunales_locales"]
    if es_arbitral and ev_trib["estado_evidencia"] == "VERIFICADO" and ev_trib["valor"] is True:
        hallazgos.append({
            "codigo": "P1_JURISDICCION_CONTRADICTORIA",
            "severidad": "CRITICA",
            "titulo": "Sumisión simultánea a arbitraje y a tribunales estatales sobre el fondo",
            "detalle": (
                "Se corroboró redacción que remite a arbitraje y simultáneamente reserva "
                "competencia de fondo a tribunales locales. Debe distinguirse si la mención "
                "judicial se limita a medidas cautelares (compatibles con el arbitraje)."
            ),
            "fundamento": (
                "Arts. 1424, párrs. 1 y 2 (remisión a petición de parte), 1425, párr. único "
                "(compatibilidad de medidas cautelares), y 1464, fracc. I, del Código de Comercio."
            ),
        })

    # Regla P2: Mecanismo final ambiguo
    if clausula.mecanismo_final == "ambiguo":
        hallazgos.append({
            "codigo": "P2_MECANISMO_AMBIGUO",
            "severidad": "CRITICA",
            "titulo": "Indefinición del mecanismo vinculante de solución de controversias",
            "detalle": (
                "La redacción no permite acreditar de manera inequívoca la voluntad de someterse "
                "a arbitraje o a jurisdicción estatal. En mediación (procedimiento voluntario "
                "asistido), la vinculatoriedad recae en el convenio alcanzado, no en la mera remisión."
            ),
            "fundamento": (
                "Arts. 1416, fracc. I, y 1423, párr. único, del Código de Comercio; "
                "art. 4, fracc. III, de la LGMASC (DOF 26-01-2024)."
            ),
        })

    # Regla P3: Etapa previa obligatoria sin plazo máximo ni condición objetiva de cierre
    if clausula.etapa_previa is not None:
        ev_ob = auditoria_evidencia["etapa_previa.obligatoria"]
        ev_plazo = auditoria_evidencia["etapa_previa.plazo_dias"]
        ev_cierre = auditoria_evidencia["etapa_previa.condicion_cierre_objetiva"]
        if (
            ev_ob["estado_evidencia"] == "VERIFICADO"
            and ev_ob["valor"] is True
            and ev_plazo["estado_evidencia"] == "AUSENTE_TRAS_REVISION"
            and ev_cierre["estado_evidencia"] == "AUSENTE_TRAS_REVISION"
        ):
            hallazgos.append({
                "codigo": "P3_ESCALAMIENTO_SIN_PLAZO",
                "severidad": "ALTA",
                "titulo": "Etapa previa obligatoria sin plazo máximo ni condición objetiva de cierre",
                "detalle": (
                    "Se exige agotar negociación o mediación previa sin fijar término en días "
                    "ni supuesto objetivo de conclusión (p. ej. negativa escrita o constancia de cierre), "
                    "lo que abre riesgo práctico de dilación sobre la procedencia del mecanismo final."
                ),
                "fundamento": (
                    "Riesgo práctico de ejecutabilidad de cláusula escalonada; remisión arbitral "
                    "sujeta al art. 1424, párr. 1, del Código de Comercio."
                ),
            })

    # Reglas arbitrales (P4–P7): solo aplican si el mecanismo final verificado es arbitraje
    campos_pendientes_revision = []
    if es_arbitral:
        ev_sede = auditoria_evidencia["sede_arbitraje"]
        ev_inst = auditoria_evidencia["institucion_administradora"]
        ev_regl = auditoria_evidencia["reglamento_arbitral"]
        ev_num = auditoria_evidencia["numero_arbitros"]
        ev_idi = auditoria_evidencia["idioma"]
        ev_ley = auditoria_evidencia["ley_aplicable_fondo"]

        for nombre_c, ev_c in (
            ("sede_arbitraje", ev_sede),
            ("institucion_administradora", ev_inst),
            ("reglamento_arbitral", ev_regl),
            ("numero_arbitros", ev_num),
            ("idioma", ev_idi),
            ("ley_aplicable_fondo", ev_ley),
            ("somete_tambien_a_tribunales_locales", ev_trib),
        ):
            if ev_c["estado_evidencia"] == "NO_DETERMINADO":
                campos_pendientes_revision.append(nombre_c)

        # Regla P4 (reclasificada a MEDIA): ausencia de pacto de sede (admite regla supletoria legal)
        if ev_sede["estado_evidencia"] == "AUSENTE_TRAS_REVISION":
            hallazgos.append({
                "codigo": "P4_OMISION_PACTO_SEDE_ARBITRAL",
                "severidad": "MEDIA",
                "titulo": "Omisión de pacto expreso sobre sede jurídica del arbitraje",
                "detalle": (
                    "No invalida el acuerdo arbitral, pero eleva costo e incertidumbre procesal: "
                    "a falta de pacto, el tribunal arbitral determinará el lugar del arbitraje "
                    "(distinto del lugar de audiencias o reuniones)."
                ),
                "fundamento": "Art. 1436, párrs. 1 y 2, del Código de Comercio.",
            })

        # Regla P5A / P5B: Institución administradora y reglamento
        if (
            ev_inst["estado_evidencia"] == "AUSENTE_TRAS_REVISION"
            and ev_regl["estado_evidencia"] == "AUSENTE_TRAS_REVISION"
        ):
            hallazgos.append({
                "codigo": "P5_SIN_INSTITUCION_NI_REGLAMENTO",
                "severidad": "MEDIA",
                "titulo": "Omisión de institución administradora y de reglamento procedimental",
                "detalle": (
                    "No se pactó arbitraje institucional ni reglamento ad hoc (p. ej. CNUDMI/UNCITRAL); "
                    "en defecto de acuerdo, el tribunal dirigirá el procedimiento conforme a la ley."
                ),
                "fundamento": "Arts. 1417, fracc. II, y 1435, párrs. 1 y 2, del Código de Comercio.",
            })
        elif ev_inst["estado_evidencia"] == "VERIFICADO" and ev_inst["valor"]:
            if not _institucion_es_conocida(ev_inst["valor"]):
                hallazgos.append({
                    "codigo": "P5B_INSTITUCION_NO_CORROBORADA",
                    "severidad": "ALTA",
                    "titulo": f"Institución arbitral no corroborada en catálogo ('{ev_inst['valor']}')",
                    "detalle": (
                        "La denominación institucional extraída no figura en el catálogo interno "
                        "verificado. Debe corroborarse su existencia jurídica y vigencia reglamentaria "
                        "para descartar una cláusula patológica por institución inexistente."
                    ),
                    "fundamento": "Art. 1424, párr. 1, del Código de Comercio (acuerdo ineficaz o de ejecución imposible).",
                })

        # Regla P6: Número par de árbitros (riesgo operativo, no nulidad)
        if ev_num["estado_evidencia"] == "VERIFICADO" and ev_num["valor"] % 2 == 0:
            hallazgos.append({
                "codigo": "P6_NUMERO_PAR_ARBITROS",
                "severidad": "MEDIA",
                "titulo": f"Tribunal arbitral integrado por número par ({ev_num['valor']} árbitros)",
                "detalle": (
                    "La ley permite pactar libremente el número de árbitros, pero un tribunal par "
                    "introduce riesgo operativo de empate deliberativo; conviene verificar regla de "
                    "desempate o designación de presidente."
                ),
                "fundamento": "Arts. 1426, párr. único, y 1446, párr. único, del Código de Comercio.",
            })

        # Regla P7: Omisión de pacto expreso sobre idioma o ley aplicable al fondo
        faltantes_p7 = []
        if ev_idi["estado_evidencia"] == "AUSENTE_TRAS_REVISION":
            faltantes_p7.append("idioma del procedimiento (art. 1438, párr. 1, CCom)")
        if ev_ley["estado_evidencia"] == "AUSENTE_TRAS_REVISION":
            faltantes_p7.append("normas aplicables al fondo del litigio (art. 1445, párrs. 1 y 2, CCom)")
        if faltantes_p7:
            hallazgos.append({
                "codigo": "P7_OMISION_PACTO_IDIOMA_O_LEY_FONDO",
                "severidad": "BAJA",
                "titulo": "Omisión de pacto expreso sobre idioma o derecho aplicable al fondo",
                "detalle": f"A falta de pacto, el tribunal arbitral determinará: {', '.join(faltantes_p7)}.",
                "fundamento": "Arts. 1438, párr. 1, y 1445, párrs. 1 y 2, del Código de Comercio.",
            })

    # 6. Clasificación final prudente y severidad máxima
    severidad_maxima = None
    if hallazgos:
        severidad_maxima = max(
            (h["severidad"] for h in hallazgos),
            key=lambda s: ORDEN_SEVERIDAD[s],
        )

    codigos_hallazgos = {h["codigo"] for h in hallazgos}
    hay_patologia_confirmada = bool(
        codigos_hallazgos & {
            "P1_JURISDICCION_CONTRADICTORIA",
            "P2_MECANISMO_AMBIGUO",
            "P3_ESCALAMIENTO_SIN_PLAZO",
        }
    )
    cobertura_incompleta = not cobertura_documental.get("completa", True)
    requiere_revision_humana = bool(
        alertas_integridad
        or campos_pendientes_revision
        or cobertura_incompleta
        or "P5B_INSTITUCION_NO_CORROBORADA" in codigos_hallazgos
    )

    if hay_patologia_confirmada:
        estado_final = "PATOLOGIA_DETECTADA"
    elif requiere_revision_humana:
        estado_final = "REQUIERE_REVISION"
    elif hallazgos:
        estado_final = "OBSERVACIONES_DETECTADAS"
    else:
        estado_final = "CLAUSULA_SIN_ALERTAS_AUTOMATICAS"

    return {
        "estado": estado_final,
        "severidad_maxima": severidad_maxima,
        "requiere_revision_humana": requiere_revision_humana,
        "origen_fuente": origen_fuente,
        "documento": nombre_documento,
        "huella_sha256": huella_sha256,
        "cobertura_documental": cobertura_documental,
        "ubicacion_clausula_principal": loc_clausula,
        "evidencia_por_atributo": auditoria_evidencia,
        "alertas_integridad_evidencia": alertas_integridad,
        "campos_pendientes_revision": campos_pendientes_revision,
        "total_hallazgos": len(hallazgos),
        "hallazgos": hallazgos,
        "alcance_y_limites": [
            "Corrobora determinísticamente ubicación de citas y presencia léxica básica por atributo, pero el sentido jurídico final requiere revisión humana.",
            "No evalúa arbitrabilidad objetiva de la materia, facultades de representación de firmantes, cláusulas asimétricas ni vigencia de reglamentos institucionales.",
        ],
    }