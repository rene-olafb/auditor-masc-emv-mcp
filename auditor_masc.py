import re
import unicodedata
from typing import Optional
from pydantic import ValidationError
from esquemas import (
    ClausulaExtraida,
    DatoBooleano,
    DatoNumeroArbitros,
    DatoPlazoDias,
    DatoTexto,
)
from lector_documentos import (
    MAX_CARACTERES_EXTRAIDOS,
    calcular_sha256_texto,
    extraer_documento,
    localizar_cita_en_bloques,
)

STOPWORDS_ES = {
    "de", "del", "la", "las", "el", "los", "en", "por", "con", "para", "un", "una", "y", "o", "al"
}

UNIDADES_ES = {
    1: ("uno", "un", "una", "unico", "unica"),
    2: ("dos",),
    3: ("tres",),
    4: ("cuatro",),
    5: ("cinco",),
    6: ("seis",),
    7: ("siete",),
    8: ("ocho",),
    9: ("nueve",),
    10: ("diez",),
    11: ("once",),
    12: ("doce",),
    13: ("trece",),
    14: ("catorce",),
    15: ("quince",),
    16: ("dieciseis",),
    17: ("diecisiete",),
    18: ("dieciocho",),
    19: ("diecinueve",),
    20: ("veinte",),
    21: ("veintiuno", "veintiun", "veintiuna"),
    22: ("veintidos",),
    23: ("veintitres",),
    24: ("veinticuatro",),
    25: ("veinticinco",),
    26: ("veintiseis",),
    27: ("veintisiete",),
    28: ("veintiocho",),
    29: ("veintinueve",),
}

DECENAS_ES = {
    30: "treinta",
    40: "cuarenta",
    50: "cincuenta",
    60: "sesenta",
    70: "setenta",
    80: "ochenta",
    90: "noventa",
}

ALIAS_TEXTO = {
    "ciudad de mexico": ("ciudad de mexico", "cdmx", "distrito federal"),
    "espanol": ("espanol", "castellano"),
    "ingles": ("ingles", "english"),
    "codigo de comercio": ("codigo de comercio", "ccom"),
    "cam": ("cam", "centro de arbitraje de mexico"),
    "canaco": ("canaco", "camara nacional de comercio", "camara nacional de comercio de la ciudad de mexico"),
    "icc": ("icc", "cci", "camara de comercio internacional", "international chamber of commerce"),
    "icdr": ("icdr", "aaa", "american arbitration association"),
    "uncitral": ("uncitral", "cnudmi"),
}

# Coincidencia canónica exacta (sin ad_hoc ni coincidencia por subcadena)
INSTITUCIONES_CANONICAS_REFERENCIA = {
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
    "american arbitration association",
    "lcia",
    "london court of international arbitration",
    "ciac",
    "comision interamericana de arbitraje comercial",
    "cemca",
}

PALABRAS_CLAVE_MECANISMO = {
    "arbitraje": ("arbitr", "laudo"),
    "jurisdiccion_estatal": (
        "juez", "jueces", "tribunal", "tribunales", "juzgado", "fuero",
        "judicial", "juicio", "litigio", "demanda",
    ),
    "mediacion": ("mediacion", "mediador"),
    "conciliacion": ("conciliacion", "conciliador"),
    "negociacion": ("negociacion", "negociar", "buena fe", "amistosa"),
}

PALABRAS_CLAVE_ETAPA_TIPO = {
    "negociacion": ("negociacion", "negociar", "negociaran", "buena fe", "amistosa", "directa"),
    "mediacion": ("mediacion", "mediador"),
    "conciliacion": ("conciliacion", "conciliador"),
    "otro": (),
}

PALABRAS_CLAVE_OBLIGATORIEDAD = (
    "debera", "deberan", "obligatori", "antes de", "previo", "previamente",
    "agotar", "agotaran", "condicion", "requisito", "necesari", "inexcusable",
    "negociaran", "mediaran", "conciliaran", "someteran", "tendran que",
)

PALABRAS_CLAVE_TRIBUNALES_LOCALES = (
    "juez", "jueces", "tribunal", "tribunales", "juzgado", "juzgados", "fuero", "jurisdiccion", "judicial"
)

PALABRAS_CLAVE_CAUTELARES_O_AUXILIO = (
    "cautelar", "cautelares", "provisional", "provisionales", "precautori",
    "conservatori", "auxilio judicial", "apoyo judicial", "nulidad del laudo", "ejecucion del laudo",
)

PALABRAS_CLAVE_ANCLA_ARBITROS = (
    "arbitro", "arbitros", "tribunal arbitral", "panel arbitral", "unico", "unica", "miembro", "miembros"
)

PATRON_CONDICIONAL_ESCALAMIENTO_BENIGNO = re.compile(
    r"\b(?:de|en caso de|si|cuando)\s+no\s+(?:llegar|convenir|resolver\w*|alcanzar|existir|lograr|arribar|subsistir)\b"
)

PATRONES_NEGACION_EXCLUSION = (
    re.compile(r"\bno\b"),
    re.compile(r"\bni\b"),
    re.compile(r"\bsin\b"),
    re.compile(r"\bsalvo\b"),
    re.compile(r"\bexcepto\b"),
    re.compile(r"\bexcluy\w*"),
    re.compile(r"\bexclusi\w*"),
    re.compile(r"\brenunci\w*"),
    re.compile(r"\bnunca\b"),
    re.compile(r"\bjamas\b"),
    re.compile(r"\binaplicable\b"),
    re.compile(r"\bimprocedente\b"),
)

ORDEN_SEVERIDAD = {"CRITICA": 4, "ALTA": 3, "MEDIA": 2, "BAJA": 1}


def _normalizar_lexico(texto: str) -> str:
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sin_acentos).strip()


def _variantes_numero_espanol(valor: int) -> tuple[str, ...]:
    variantes = [str(valor)]
    if valor in UNIDADES_ES:
        variantes.extend(UNIDADES_ES[valor])
    elif valor in DECENAS_ES:
        variantes.append(DECENAS_ES[valor])
    elif 31 <= valor <= 99:
        decena = (valor // 10) * 10
        unidad = valor % 10
        if decena in DECENAS_ES and unidad in UNIDADES_ES:
            for u in UNIDADES_ES[unidad]:
                if u not in {"unico", "unica"}:
                    variantes.append(f"{DECENAS_ES[decena]} y {u}")
    elif valor in {100, 120, 180, 365}:
        especiales = {100: "cien", 120: "ciento veinte", 180: "ciento ochenta", 365: "trescientos sesenta y cinco"}
        variantes.append(especiales[valor])
    return tuple(variantes)


def _detectar_negacion_o_exclusion(cita: str) -> list[str]:
    """Detecta marcadores de negación/exclusión excluyendo fórmulas condicionales benignas de escalamiento."""
    cita_norm = _normalizar_lexico(cita)
    cita_sin_condicional = PATRON_CONDICIONAL_ESCALAMIENTO_BENIGNO.sub(" ", cita_norm)
    hallados = []
    for pat in PATRONES_NEGACION_EXCLUSION:
        m = pat.search(cita_sin_condicional)
        if m:
            hallados.append(m.group(0))
    return hallados


def _corroborar_texto_en_cita(valor: str, cita: str) -> bool:
    val_norm = _normalizar_lexico(valor)
    cita_norm = _normalizar_lexico(cita)

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
    candidatos = _variantes_numero_espanol(valor)
    return any(re.search(rf"\b{re.escape(c)}\b", cita_norm) for c in candidatos)


def _institucion_en_catalogo_referencia(nombre: str) -> bool:
    """Coincidencia exacta normalizada (evita falsos positivos como 'CAM de la Luna')."""
    norm = _normalizar_lexico(nombre)
    return norm in INSTITUCIONES_CANONICAS_REFERENCIA


def _auditar_atributo(
    nombre: str,
    dato: DatoTexto | DatoPlazoDias | DatoNumeroArbitros | DatoBooleano,
    bloques: list[dict],
    cita_clausula_norm: str,
    palabras_clave_bool: Optional[tuple[str, ...]] = None,
    requiere_ancla_arbitros: bool = False,
    permitir_no_aplicable: bool = True,
) -> dict:
    """
    Separa localización de fragmento, coincidencia léxica y alertas por negación/exclusión.
    Nunca reporta 'VERIFICADO' como verdad jurídica; usa 'COINCIDENCIA_LEXICA'.
    """
    if dato.estado == "no_aplicable" and not permitir_no_aplicable:
        return {
            "atributo": nombre,
            "estado_declarado": "no_aplicable",
            "estado_evidencia": "NO_APLICABLE_IMPROCEDENTE",
            "valor": None,
            "motivo": f"El atributo '{nombre}' no puede marcarse 'no_aplicable' para el mecanismo final declarado.",
            "ubicacion": None,
        }

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

    # Verificar si la cita del atributo contiene negaciones o exclusiones
    negaciones = _detectar_negacion_o_exclusion(dato.cita_literal)
    if negaciones:
        return {
            "atributo": nombre,
            "estado_declarado": "presente",
            "estado_evidencia": "COINCIDENCIA_CON_NEGACION_O_EXCLUSION",
            "valor": dato.valor,
            "motivo": (
                f"La cita localizada contiene marcadores de negación/exclusión ({', '.join(negaciones)}); "
                "el motor no puede confirmar sentido afirmativo sin revisión humana."
            ),
            "ubicacion": loc,
        }

    corroborado = True
    if isinstance(dato, DatoTexto) and dato.valor is not None:
        corroborado = _corroborar_texto_en_cita(dato.valor, dato.cita_literal)
    elif isinstance(dato, (DatoPlazoDias, DatoNumeroArbitros)) and dato.valor is not None:
        corroborado = _corroborar_entero_en_cita(dato.valor, dato.cita_literal)
        if corroborado and requiere_ancla_arbitros:
            cita_norm = _normalizar_lexico(dato.cita_literal)
            en_clausula = cita_norm in cita_clausula_norm
            tiene_ancla = any(k in cita_norm for k in PALABRAS_CLAVE_ANCLA_ARBITROS)
            if not (en_clausula and tiene_ancla):
                return {
                    "atributo": nombre,
                    "estado_declarado": "presente",
                    "estado_evidencia": "RESPALDO_NO_CORROBORADO",
                    "valor": dato.valor,
                    "motivo": (
                        "La cita numérica no aparece dentro de la cláusula arbitral con referencia "
                        "expresa a árbitros o tribunal arbitral."
                    ),
                    "ubicacion": loc,
                }
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
                f"La cita existe en el documento, pero no contiene coincidencia léxica "
                f"para el valor afirmado ({dato.valor!r})."
            ),
            "ubicacion": loc,
        }

    return {
        "atributo": nombre,
        "estado_declarado": "presente",
        "estado_evidencia": "COINCIDENCIA_LEXICA",
        "valor": dato.valor,
        "ubicacion": loc,
    }


def auditar_clausula_masc(
    datos_clausula: dict,
    ruta_archivo: Optional[str] = None,
    texto_documento: Optional[str] = None,
    directorio_autorizado: Optional[str] = None,
) -> dict:
    """
    Audita una cláusula MASC o arbitral separando localización de cita, coincidencia léxica,
    detección de negaciones/exclusiones y reglas jurídicas calibradas al Código de Comercio.
    """
    # 1. Validación estricta de esquema Pydantic v2
    try:
        clausula = ClausulaExtraida(**datos_clausula)
    except ValidationError as exc:
        return {
            "estado": "ERROR_VALIDACION",
            "mensaje": "Los datos extraídos de la cláusula no cumplen el esquema estructural v0.3.1.",
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
            {
                "indice": i,
                "secuencia_fisica": i,
                "tipo": "texto_directo",
                "origen": f"Bloque {i}",
                "texto": l,
            }
            for i, l in enumerate(lineas, start=1)
        ]
        origen_fuente = "texto_directo_no_verificado_en_disco"
        nombre_documento = "texto_directo_en_memoria"
        huella_sha256 = calcular_sha256_texto(texto_documento)
        cobertura_documental = {
            "completa": True,
            "advertencia": "Texto suministrado directamente por el cliente.",
        }
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

    cita_clausula_norm = _normalizar_lexico(clausula.cita_textual_clausula)

    # 4. Auditoría de evidencia por atributo (localización + coincidencia léxica + negaciones)
    auditoria_evidencia: dict[str, dict] = {}
    alertas_integridad: list[dict] = []

    cita_mec = clausula.cita_mecanismo_final or clausula.cita_textual_clausula
    loc_mec = localizar_cita_en_bloques(cita_mec, bloques)
    negaciones_mec = _detectar_negacion_o_exclusion(cita_mec)

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
    elif negaciones_mec and clausula.mecanismo_final != "ambiguo":
        auditoria_evidencia["mecanismo_final"] = {
            "estado_evidencia": "COINCIDENCIA_CON_NEGACION_O_EXCLUSION",
            "valor": clausula.mecanismo_final,
            "motivo": (
                f"La cita del mecanismo final contiene negaciones o exclusiones ({', '.join(negaciones_mec)}); "
                "no puede afirmarse sometimiento positivo sin revisión humana."
            ),
            "ubicacion": loc_mec,
        }
        alertas_integridad.append({
            "codigo": "E3_NEGACION_O_EXCLUSION_EN_CITA",
            "atributo": "mecanismo_final",
            "detalle": f"Negación o exclusión detectada en la cita del mecanismo final ({', '.join(negaciones_mec)}).",
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
                "detalle": f"No se encontró coincidencia léxica para mecanismo_final='{clausula.mecanismo_final}'.",
            })
        else:
            auditoria_evidencia["mecanismo_final"] = {
                "estado_evidencia": "COINCIDENCIA_LEXICA",
                "valor": clausula.mecanismo_final,
                "ubicacion": loc_mec,
            }
    else:
        # Caso mecanismo_final == 'ambiguo': nunca dispensa revisión humana
        cita_mec_norm = _normalizar_lexico(cita_mec)
        tiene_arbitraje = any(k in cita_mec_norm for k in PALABRAS_CLAVE_MECANISMO["arbitraje"])
        tiene_judicial = any(k in cita_mec_norm for k in PALABRAS_CLAVE_MECANISMO["jurisdiccion_estatal"])
        if tiene_arbitraje ^ tiene_judicial:
            auditoria_evidencia["mecanismo_final"] = {
                "estado_evidencia": "AMBIGUEDAD_DECLARADA_CON_INDICADOR_UNICO",
                "valor": "ambiguo",
                "motivo": (
                    "El extractor clasificó la cláusula como 'ambiguo', pero la cita contiene "
                    "indicadores léxicos de un solo foro; requiere revisión humana."
                ),
                "ubicacion": loc_mec,
            }
            alertas_integridad.append({
                "codigo": "E4_AMBIGUEDAD_DECLARADA_REQUIERE_COTEJO",
                "atributo": "mecanismo_final",
                "detalle": "La etiqueta 'ambiguo' coexiste con indicadores léxicos de un solo foro.",
            })
        else:
            auditoria_evidencia["mecanismo_final"] = {
                "estado_evidencia": "DECLARADO_AMBIGUO",
                "valor": "ambiguo",
                "ubicacion": loc_mec,
            }

    # Corroborar etapa_previa.tipo si existe etapa previa
    if clausula.etapa_previa is not None:
        cita_tipo_ep = (
            clausula.etapa_previa.cita_tipo
            or clausula.etapa_previa.obligatoria.cita_literal
            or clausula.cita_textual_clausula
        )
        loc_tipo_ep = localizar_cita_en_bloques(cita_tipo_ep, bloques)
        claves_ep = PALABRAS_CLAVE_ETAPA_TIPO[clausula.etapa_previa.tipo]
        if not loc_tipo_ep["verificada"]:
            auditoria_evidencia["etapa_previa.tipo"] = {
                "estado_evidencia": "SIN_RESPALDO",
                "valor": clausula.etapa_previa.tipo,
                "motivo": "La cita de etapa_previa.tipo no fue localizada en el documento.",
            }
            alertas_integridad.append({
                "codigo": "E1_ATRIBUTO_SIN_RESPALDO",
                "atributo": "etapa_previa.tipo",
                "detalle": "No se localizó respaldo de etapa_previa.tipo en el documento.",
            })
        elif claves_ep and not any(k in _normalizar_lexico(cita_tipo_ep) for k in claves_ep):
            auditoria_evidencia["etapa_previa.tipo"] = {
                "estado_evidencia": "RESPALDO_NO_CORROBORADO",
                "valor": clausula.etapa_previa.tipo,
                "motivo": f"La cita no contiene indicadores léxicos para etapa_previa.tipo='{clausula.etapa_previa.tipo}'.",
                "ubicacion": loc_tipo_ep,
            }
            alertas_integridad.append({
                "codigo": "E2_RESPALDO_NO_CORROBORADO",
                "atributo": "etapa_previa.tipo",
                "detalle": f"Sin coincidencia léxica para etapa_previa.tipo='{clausula.etapa_previa.tipo}'.",
            })
        else:
            auditoria_evidencia["etapa_previa.tipo"] = {
                "estado_evidencia": "COINCIDENCIA_LEXICA",
                "valor": clausula.etapa_previa.tipo,
                "ubicacion": loc_tipo_ep,
            }

    es_arbitral_declarado = clausula.mecanismo_final == "arbitraje"
    campos_a_auditar = [
        ("institucion_administradora", clausula.institucion_administradora, None, False, not es_arbitral_declarado),
        ("reglamento_arbitral", clausula.reglamento_arbitral, None, False, not es_arbitral_declarado),
        ("sede_arbitraje", clausula.sede_arbitraje, None, False, not es_arbitral_declarado),
        ("idioma", clausula.idioma, None, False, not es_arbitral_declarado),
        ("ley_aplicable_fondo", clausula.ley_aplicable_fondo, None, False, True),
        ("numero_arbitros", clausula.numero_arbitros, None, True, not es_arbitral_declarado),
        (
            "somete_tambien_a_tribunales_locales",
            clausula.somete_tambien_a_tribunales_locales,
            PALABRAS_CLAVE_TRIBUNALES_LOCALES,
            False,
            not es_arbitral_declarado,
        ),
    ]
    if clausula.etapa_previa is not None:
        campos_a_auditar.extend([
            (
                "etapa_previa.obligatoria",
                clausula.etapa_previa.obligatoria,
                PALABRAS_CLAVE_OBLIGATORIEDAD,
                False,
                False,
            ),
            ("etapa_previa.plazo_dias", clausula.etapa_previa.plazo_dias, None, False, False),
            (
                "etapa_previa.condicion_cierre_objetiva",
                clausula.etapa_previa.condicion_cierre_objetiva,
                None,
                False,
                False,
            ),
        ])

    for nombre, dato, kw_bool, ancla_arb, perm_na in campos_a_auditar:
        ev = _auditar_atributo(
            nombre=nombre,
            dato=dato,
            bloques=bloques,
            cita_clausula_norm=cita_clausula_norm,
            palabras_clave_bool=kw_bool,
            requiere_ancla_arbitros=ancla_arb,
            permitir_no_aplicable=perm_na,
        )
        auditoria_evidencia[nombre] = ev
        if ev["estado_evidencia"] == "SIN_RESPALDO":
            alertas_integridad.append({
                "codigo": "E1_ATRIBUTO_SIN_RESPALDO",
                "atributo": nombre,
                "detalle": ev["motivo"],
            })
        elif ev["estado_evidencia"] == "RESPALDO_NO_CORROBORADO":
            alertas_integridad.append({
                "codigo": "E2_RESPALDO_NO_CORROBORADO",
                "atributo": nombre,
                "detalle": ev["motivo"],
            })
        elif ev["estado_evidencia"] == "COINCIDENCIA_CON_NEGACION_O_EXCLUSION":
            alertas_integridad.append({
                "codigo": "E3_NEGACION_O_EXCLUSION_EN_CITA",
                "atributo": nombre,
                "detalle": ev["motivo"],
            })
        elif ev["estado_evidencia"] == "NO_APLICABLE_IMPROCEDENTE":
            alertas_integridad.append({
                "codigo": "E5_NO_APLICABLE_IMPROCEDENTE",
                "atributo": nombre,
                "detalle": ev["motivo"],
            })

    # 5. Motor determinista de reglas jurídicas
    hallazgos: list[dict] = []
    campos_pendientes_revision: list[str] = []
    es_arbitral_corroborado = (
        clausula.mecanismo_final == "arbitraje"
        and auditoria_evidencia["mecanismo_final"]["estado_evidencia"] == "COINCIDENCIA_LEXICA"
    )

    # Regla P1 / P1R: Sumisión simultánea a tribunales locales vs. cautelares/auxilio judicial (art. 1425 CCom)
    ev_trib = auditoria_evidencia["somete_tambien_a_tribunales_locales"]
    if es_arbitral_corroborado and ev_trib["estado_evidencia"] == "COINCIDENCIA_LEXICA" and ev_trib["valor"] is True:
        cita_trib_norm = _normalizar_lexico(clausula.somete_tambien_a_tribunales_locales.cita_literal or "")
        es_posible_cautelar = any(k in cita_trib_norm for k in PALABRAS_CLAVE_CAUTELARES_O_AUXILIO)
        if es_posible_cautelar:
            hallazgos.append({
                "codigo": "P1R_MENCION_JUDICIAL_POSIBLE_CAUTELAR_O_AUXILIO",
                "severidad": "MEDIA",
                "titulo": "Mención judicial con términos de medidas cautelares o auxilio (requiere revisión)",
                "detalle": (
                    "La cita judicial incluye términos compatibles con medidas cautelares, "
                    "apoyo judicial o nulidad/ejecución del laudo. No se clasifica automáticamente "
                    "como sumisión contradictoria sobre el fondo, pero requiere confirmación humana."
                ),
                "fundamento": "Art. 1425, párr. único, del Código de Comercio (compatibilidad de medidas cautelares).",
            })
        else:
            hallazgos.append({
                "codigo": "P1_JURISDICCION_CONTRADICTORIA",
                "severidad": "CRITICA",
                "titulo": "Posible sumisión simultánea a arbitraje y a tribunales estatales",
                "detalle": (
                    "Se localizó mención de sometimiento a jueces locales junto al acuerdo arbitral "
                    "sin indicadores de limitarse a medidas cautelares. Requiere revisión humana para "
                    "confirmar si ambas disposiciones cubren el mismo fondo."
                ),
                "fundamento": (
                    "Arts. 1424, párrs. 1 y 2 (remisión a petición de parte), 1425, párr. único, "
                    "y 1464, fracc. I, del Código de Comercio."
                ),
            })

    # Regla P2: Mecanismo final ambiguo (siempre exige revisión humana)
    if clausula.mecanismo_final == "ambiguo":
        hallazgos.append({
            "codigo": "P2_MECANISMO_AMBIGUO",
            "severidad": "CRITICA",
            "titulo": "Indefinición declarada del mecanismo vinculante de solución de controversias",
            "detalle": (
                "Requiere revisión humana del consentimiento y de posibles anexos incorporados "
                "por referencia para determinar si existe sometimiento válido a arbitraje o foro judicial."
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

        for nombre_ep, ev_ep in (
            ("etapa_previa.obligatoria", ev_ob),
            ("etapa_previa.plazo_dias", ev_plazo),
            ("etapa_previa.condicion_cierre_objetiva", ev_cierre),
        ):
            if ev_ep["estado_evidencia"] == "NO_DETERMINADO":
                campos_pendientes_revision.append(nombre_ep)

        if (
            ev_ob["estado_evidencia"] == "COINCIDENCIA_LEXICA"
            and ev_ob["valor"] is True
            and ev_plazo["estado_evidencia"] == "AUSENTE_TRAS_REVISION"
            and ev_cierre["estado_evidencia"] == "AUSENTE_TRAS_REVISION"
        ):
            fundamento_p3 = (
                "Riesgo práctico de ejecutabilidad y dilación procesal en cláusula escalonada; "
                "remisión arbitral sujeta al art. 1424, párr. 1, del Código de Comercio."
                if es_arbitral_corroborado
                else "Riesgo práctico de ejecutabilidad y dilación procesal en condición contractual previa "
                     "(principio de obligatoriedad contractual, art. 78 del Código de Comercio)."
            )
            hallazgos.append({
                "codigo": "P3_ESCALAMIENTO_SIN_PLAZO",
                "severidad": "ALTA",
                "titulo": "Etapa previa obligatoria sin plazo máximo ni condición objetiva de cierre",
                "detalle": (
                    "Se localizó exigencia de etapa previa sin término expreso en días ni condición "
                    "objetiva de cierre (p. ej. constancia o negativa escrita), lo que abre riesgo "
                    "práctico de dilación procesal. Requiere revisión humana."
                ),
                "fundamento": fundamento_p3,
            })

    # Reglas arbitrales (P4–P7): solo aplican si mecanismo_final == 'arbitraje' con coincidencia léxica
    if es_arbitral_corroborado:
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

        # P4: Omisión de pacto de sede (supletoria legal, severidad MEDIA)
        if ev_sede["estado_evidencia"] == "AUSENTE_TRAS_REVISION":
            hallazgos.append({
                "codigo": "P4_OMISION_PACTO_SEDE_ARBITRAL",
                "severidad": "MEDIA",
                "titulo": "Omisión de pacto expreso sobre sede jurídica del arbitraje",
                "detalle": (
                    "No invalida el acuerdo arbitral, pero eleva costo e incertidumbre procesal: "
                    "a falta de pacto, el tribunal arbitral determinará el lugar del arbitraje."
                ),
                "fundamento": "Art. 1436, párrs. 1 y 2, del Código de Comercio.",
            })

        # P5 / P5B: Institución y reglamento (desacoplando modalidad ad_hoc)
        es_ad_hoc = clausula.modalidad_arbitraje == "ad_hoc"
        if (
            ev_inst["estado_evidencia"] == "AUSENTE_TRAS_REVISION"
            and ev_regl["estado_evidencia"] == "AUSENTE_TRAS_REVISION"
            and not es_ad_hoc
        ):
            hallazgos.append({
                "codigo": "P5_SIN_INSTITUCION_NI_REGLAMENTO",
                "severidad": "MEDIA",
                "titulo": "Omisión de institución administradora y de reglamento procedimental",
                "detalle": (
                    "No se identificó arbitraje institucional ni reglamento procedimental; "
                    "en defecto de acuerdo, el tribunal dirigirá el procedimiento conforme a la ley."
                ),
                "fundamento": "Arts. 1417, fracc. II, y 1435, párrs. 1 y 2, del Código de Comercio.",
            })
        elif ev_inst["estado_evidencia"] == "COINCIDENCIA_LEXICA" and ev_inst["valor"]:
            if _normalizar_lexico(ev_inst["valor"]) in {"ad hoc", "ad_hoc"}:
                hallazgos.append({
                    "codigo": "P5C_AD_HOC_REPORTADO_COMO_INSTITUCION",
                    "severidad": "BAJA",
                    "titulo": "Modalidad 'ad hoc' reportada en campo de institución administradora",
                    "detalle": "El arbitraje ad hoc es una modalidad procedimental, no una institución administradora.",
                    "fundamento": "Art. 1435, párrs. 1 y 2, del Código de Comercio.",
                })
            elif not _institucion_en_catalogo_referencia(ev_inst["valor"]):
                hallazgos.append({
                    "codigo": "P5B_IDENTIDAD_INSTITUCIONAL_PENDIENTE_COTEJO",
                    "severidad": "MEDIA",
                    "titulo": f"Identidad institucional pendiente de cotejo ('{ev_inst['valor']}')",
                    "detalle": (
                        "La denominación no coincide de forma exacta con la lista de referencia interna. "
                        "Debe corroborarse externamente su existencia jurídica y reglamento vigente "
                        "(no implica patología automática)."
                    ),
                    "fundamento": "Verificación operativa para descartar inejecutabilidad (art. 1424, párr. 1, CCom).",
                })

        # P6: Número par de árbitros (riesgo operativo de empate)
        if ev_num["estado_evidencia"] == "COINCIDENCIA_LEXICA" and ev_num["valor"] % 2 == 0:
            hallazgos.append({
                "codigo": "P6_NUMERO_PAR_ARBITROS",
                "severidad": "MEDIA",
                "titulo": f"Tribunal arbitral integrado por número par ({ev_num['valor']} árbitros)",
                "detalle": (
                    "La ley permite pactar libremente el número de árbitros, pero un tribunal par "
                    "introduce riesgo operativo de empate sobre el fondo; debe revisarse el mecanismo "
                    "de toma de decisiones y el reglamento aplicable."
                ),
                "fundamento": "Arts. 1426, párr. único, y 1446, párr. único, del Código de Comercio.",
            })

        # P7: Omisión de pacto expreso sobre idioma o ley aplicable al fondo
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

    # 6. Clasificación final prudente: fallas de integridad/cobertura SIEMPRE priorizan REQUIERE_REVISION
    severidad_maxima = None
    if hallazgos:
        severidad_maxima = max(
            (h["severidad"] for h in hallazgos),
            key=lambda s: ORDEN_SEVERIDAD[s],
        )

    codigos_hallazgos = {h["codigo"] for h in hallazgos}
    hay_alerta_interpretativa_critica = bool(
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
        or hay_alerta_interpretativa_critica
        or "P5B_IDENTIDAD_INSTITUCIONAL_PENDIENTE_COTEJO" in codigos_hallazgos
        or "P1R_MENCION_JUDICIAL_POSIBLE_CAUTELAR_O_AUXILIO" in codigos_hallazgos
    )

    if alertas_integridad or cobertura_incompleta:
        estado_final = "REQUIERE_REVISION"
    elif hay_alerta_interpretativa_critica:
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
            "Distingue localización de fragmento y coincidencia léxica básica; no equivale a confirmación jurídica definitiva sin revisión humana.",
            "Las declaraciones 'ausente_tras_revision' y 'no_aplicable' reflejan la revisión reportada por el extractor sobre la cobertura documental indicada.",
            "No evalúa arbitrabilidad objetiva de la materia, facultades de representación de firmantes, cláusulas asimétricas ni vigencia de reglamentos institucionales.",
        ],
    }