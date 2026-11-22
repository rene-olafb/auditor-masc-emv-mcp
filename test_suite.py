import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from docx import Document

from lector_documentos import (
    extraer_documento,
    localizar_cita_en_bloques,
    verificar_cita_en_texto,
)
from auditor_masc import auditar_clausula_masc
from simulador_emv import (
    simular_escenario_batna_emv,
    simular_matriz_sensibilidad_emv,
)


def _crear_pdf_valido(
    paginas_texto: list[str],
    ruta: Path,
    con_imagen_en_pagina_1: bool = False,
) -> None:
    """Genera un PDF 1.4 válido con texto, páginas vacías u objeto /Image embebido."""
    objetos: list[bytes] = []
    n_pags = len(paginas_texto)
    ids_paginas = [4 + i * 2 + 1 for i in range(n_pags)]
    kids = " ".join(f"{pid} 0 R" for pid in ids_paginas)
    id_img = 4 + n_pags * 2

    objetos.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objetos.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pags} >>".encode("ascii"))
    objetos.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for i, texto in enumerate(paginas_texto):
        c_id = 4 + i * 2
        if texto:
            esc = texto.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream = f"BT /F1 12 Tf 50 700 Td ({esc}) Tj ET".encode("latin-1", errors="ignore")
        else:
            stream = b""
        objetos.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream"
        )
        xobj_str = f" /XObject << /Im1 {id_img} 0 R >>" if (i == 0 and con_imagen_en_pagina_1) else ""
        objetos.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >>{xobj_str} >> /Contents {c_id} 0 R >>"
            ).encode("ascii")
        )

    if con_imagen_en_pagina_1:
        objetos.append(
            b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
            b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n\x00\nendstream"
        )

    salida = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objetos, start=1):
        offsets.append(len(salida))
        salida.extend(f"{idx} 0 obj\n".encode("ascii"))
        salida.extend(obj)
        salida.extend(b"\nendobj\n")

    xref_pos = len(salida)
    salida.extend(f"xref\n0 {len(objetos) + 1}\n".encode("ascii"))
    salida.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        salida.extend(f"{off:010d} 00000 n \n".encode("ascii"))

    salida.extend(
        f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    ruta.write_bytes(bytes(salida))


class TestAuditorMASCyEMVv031(unittest.TestCase):
    """Suite de regresión v0.3.1: sandbox, DOCX/PDF complejos, negaciones, identidad temporal EMV y MCP stdio."""

    # --- BLOQUE 1: SEGURIDAD E INGESTA DOCUMENTAL ---

    def test_01_sandbox_bloquea_path_traversal_y_prefijos(self):
        """Rechaza rutas fuera de la raíz, directorios hermanos con prefijo parecido y symlinks externos."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            raiz = base / "expedientes"
            raiz.mkdir()
            hermano = base / "expedientes_privados"
            hermano.mkdir()

            secreto1 = base / "secreto.txt"
            secreto1.write_text("CONFIDENCIAL 1", encoding="utf-8")
            secreto2 = hermano / "secreto2.txt"
            secreto2.write_text("CONFIDENCIAL 2", encoding="utf-8")

            self.assertEqual(extraer_documento("../secreto.txt", str(raiz))["estado"], "ACCESO_DENEGADO")
            self.assertEqual(extraer_documento(str(secreto2), str(raiz))["estado"], "ACCESO_DENEGADO")
            self.assertEqual(
                auditar_clausula_masc(
                    datos_clausula={
                        "mecanismo_final": "arbitraje",
                        "cita_textual_clausula": "CONFIDENCIAL 1 TEXTO LARGO",
                    },
                    ruta_archivo=str(secreto1),
                    directorio_autorizado=str(raiz),
                )["estado"],
                "ACCESO_DENEGADO",
            )

    def test_02_fail_closed_archivos_invalidos_y_vacio(self):
        """Bloquea archivos inexistentes, extensiones prohibidas y archivos de 0 bytes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(extraer_documento("no_existe.pdf", tmpdir)["estado"], "ERROR_ARCHIVO")
            exe = Path(tmpdir) / "x.exe"
            exe.write_bytes(b"MZ")
            self.assertEqual(extraer_documento("x.exe", tmpdir)["estado"], "FUERA_DE_ALCANCE")
            vacio = Path(tmpdir) / "vacio.txt"
            vacio.write_text("", encoding="utf-8")
            self.assertEqual(extraer_documento("vacio.txt", tmpdir)["estado"], "ERROR_ARCHIVO")

    def test_03_docx_orden_estricto_en_celda_con_subtabla_y_fusiones(self):
        """Preserva ANTES -> Subtabla -> DESPUÉS, encabezado/pie y deduplica fusiones."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ruta = Path(tmpdir) / "complejo.docx"
            doc = Document()
            doc.sections[0].header.paragraphs[0].text = "ENCABEZADO 2026"
            doc.sections[0].footer.paragraphs[0].text = "PIE 2026"

            tabla = doc.add_table(rows=2, cols=2)
            tabla.cell(0, 0).merge(tabla.cell(0, 1))
            tabla.cell(0, 0).text = "FUSION HORIZONTAL"

            fila2 = tabla.add_row()
            tabla.cell(1, 0).merge(fila2.cells[0])
            tabla.cell(1, 0).text = "FUSION VERTICAL"

            celda_mixta = tabla.cell(1, 1)
            celda_mixta.paragraphs[0].text = "TEXTO ANTES DE SUBTABLA"
            sub = celda_mixta.add_table(rows=1, cols=1)
            sub.cell(0, 0).text = "CONTENIDO SUBTABLA"
            celda_mixta.add_paragraph("TEXTO DESPUES DE SUBTABLA")
            fila2.cells[1].text = "CELDA FINAL"

            doc.save(ruta)
            res = extraer_documento("complejo.docx", tmpdir)
            self.assertEqual(res["estado"], "OK")
            textos = [b["texto"] for b in res["bloques_numerados"]]
            todo = "\n".join(textos)

            self.assertEqual(todo.count("FUSION HORIZONTAL"), 1)
            self.assertEqual(todo.count("FUSION VERTICAL"), 1)
            i_antes = next(i for i, t in enumerate(textos) if "TEXTO ANTES DE SUBTABLA" in t)
            i_sub = next(i for i, t in enumerate(textos) if "CONTENIDO SUBTABLA" in t)
            i_desp = next(i for i, t in enumerate(textos) if "TEXTO DESPUES DE SUBTABLA" in t)
            self.assertLess(i_antes, i_sub)
            self.assertLess(i_sub, i_desp)

    def test_04_pdf_mixto_y_pdf_con_imagen_reportan_extraccion_parcial(self):
        """Reporta EXTRACCION_PARCIAL si hay página sin texto o si una página textual contiene /Image."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ruta_mixto = Path(tmpdir) / "mixto.pdf"
            _crear_pdf_valido(["PORTADA CON TEXTO SUFICIENTE", ""], ruta_mixto)
            r1 = extraer_documento("mixto.pdf", tmpdir)
            self.assertEqual(r1["estado"], "EXTRACCION_PARCIAL")
            self.assertEqual(r1["cobertura"]["paginas_sin_texto"], [2])

            ruta_img = Path(tmpdir) / "hibrido_imagen.pdf"
            _crear_pdf_valido(["CARATULA CON MAS DE VEINTE CARACTERES"], ruta_img, con_imagen_en_pagina_1=True)
            r2 = extraer_documento("hibrido_imagen.pdf", tmpdir)
            self.assertEqual(r2["estado"], "EXTRACCION_PARCIAL")
            self.assertEqual(r2["cobertura"]["paginas_con_imagenes_requieren_revision"], [1])

    def test_05_pdf_sin_texto_falla_cerrado(self):
        """PDF compuesto solo por páginas sin texto devuelve PDF_SIN_TEXTO_EXTRAIBLE."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ruta = Path(tmpdir) / "escaneado.pdf"
            _crear_pdf_valido(["", ""], ruta)
            res = extraer_documento("escaneado.pdf", tmpdir)
            self.assertEqual(res["estado"], "PDF_SIN_TEXTO_EXTRAIBLE")

    def test_06_coordenadas_originales_y_prohibicion_de_unir_sobre_laguna_pdf(self):
        """Los índices recuperan el fragmento original con espacios y prohíben saltar páginas ilegibles."""
        bloques = [
            {"indice": 1, "secuencia_fisica": 1, "tipo": "pagina_pdf", "origen": "Página 1", "texto": "A     B   C     D"},
            {"indice": 2, "secuencia_fisica": 3, "tipo": "pagina_pdf", "origen": "Página 3", "texto": "E F"},
        ]
        loc = localizar_cita_en_bloques("C D", bloques)
        self.assertTrue(loc["verificada"])
        self.assertEqual(loc["fragmento_original"], "C     D")
        self.assertEqual(bloques[0]["texto"][loc["inicio_char"]:loc["fin_char"]], "C     D")

        # Página 2 faltante: prohíbe unir fin de P1 con inicio de P3
        loc_laguna = localizar_cita_en_bloques("D E", bloques)
        self.assertFalse(loc_laguna["verificada"])

    # --- BLOQUE 2: EVIDENCIA POR ATRIBUTO, NEGACIONES Y REGLAS JURÍDICAS ---

    def test_07_bloquea_negaciones_y_exclusiones_como_hecho_confirmado(self):
        """Contraejemplo crítico v0.3: una cláusula que niega arbitraje o sede exige revisión humana."""
        txt = "Las partes no se someterán a arbitraje. No se pacta sede en Guadalajara."
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "arbitraje",
                "cita_textual_clausula": txt,
                "sede_arbitraje": {
                    "estado": "presente",
                    "valor": "Guadalajara",
                    "cita_literal": "No se pacta sede en Guadalajara.",
                },
            },
            texto_documento=txt,
        )
        self.assertEqual(res["estado"], "REQUIERE_REVISION")
        self.assertTrue(res["requiere_revision_humana"])
        codigos_int = {a["codigo"] for a in res["alertas_integridad_evidencia"]}
        self.assertIn("E3_NEGACION_O_EXCLUSION_EN_CITA", codigos_int)

    def test_08_condicional_benigno_de_escalamiento_no_dispara_negacion(self):
        """'de no llegar a un acuerdo' no bloquea una cláusula escalonada válida."""
        txt = (
            "Las partes negociarán durante 15 días; de no llegar a un acuerdo, "
            "se someterán a arbitraje ante el CAM en Guadalajara, español, Código de Comercio, tres árbitros."
        )
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "arbitraje",
                "cita_textual_clausula": txt,
                "etapa_previa": {
                    "tipo": "negociacion",
                    "obligatoria": {"estado": "presente", "valor": True, "cita_literal": "negociarán durante 15 días"},
                    "plazo_dias": {"estado": "presente", "valor": 15, "cita_literal": "15 días"},
                    "condicion_cierre_objetiva": {"estado": "ausente_tras_revision"},
                },
                "institucion_administradora": {"estado": "presente", "valor": "CAM", "cita_literal": "ante el CAM"},
                "reglamento_arbitral": {"estado": "ausente_tras_revision"},
                "sede_arbitraje": {"estado": "presente", "valor": "Guadalajara", "cita_literal": "en Guadalajara"},
                "idioma": {"estado": "presente", "valor": "español", "cita_literal": "español"},
                "ley_aplicable_fondo": {"estado": "presente", "valor": "Código de Comercio", "cita_literal": "Código de Comercio"},
                "numero_arbitros": {"estado": "presente", "valor": 3, "cita_literal": "tres árbitros"},
                "somete_tambien_a_tribunales_locales": {"estado": "ausente_tras_revision"},
            },
            texto_documento=txt,
        )
        self.assertEqual(res["estado"], "CLAUSULA_SIN_ALERTAS_AUTOMATICAS")

    def test_09_detecta_atributos_sin_respaldo_y_numero_fuera_de_clausula(self):
        """Rechaza idioma/ley sin respaldo y rechaza 'una factura' fuera de cláusula como árbitro único."""
        doc = "CLÁUSULA. Arbitraje en el CAM con sede en Zapopan.\nANEXO. Se pagará una factura mensual."
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "arbitraje",
                "cita_textual_clausula": "Arbitraje en el CAM con sede en Zapopan.",
                "institucion_administradora": {"estado": "presente", "valor": "CAM", "cita_literal": "en el CAM"},
                "sede_arbitraje": {"estado": "presente", "valor": "Zapopan", "cita_literal": "sede en Zapopan"},
                "idioma": {"estado": "presente", "valor": "Español", "cita_literal": "Arbitraje en el CAM con sede en Zapopan."},
                "numero_arbitros": {"estado": "presente", "valor": 1, "cita_literal": "una factura"},
            },
            texto_documento=doc,
        )
        self.assertEqual(res["estado"], "REQUIERE_REVISION")
        atributos = {a["atributo"] for a in res["alertas_integridad_evidencia"]}
        self.assertEqual(atributos, {"idioma", "numero_arbitros"})

    def test_10_p1_distingue_medidas_cautelares_de_sumision_contradictoria(self):
        """Medidas cautelares (art. 1425 CCom) emiten P1R (MEDIA), no P1 crítica."""
        txt_caut = "Las controversias se resolverán por arbitraje ante el CAM, salvo medidas cautelares ante jueces locales."
        r_caut = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "arbitraje",
                "cita_textual_clausula": txt_caut,
                "cita_mecanismo_final": "por arbitraje ante el CAM",
                "institucion_administradora": {"estado": "presente", "valor": "CAM", "cita_literal": "ante el CAM"},
                "reglamento_arbitral": {"estado": "ausente_tras_revision"},
                "sede_arbitraje": {"estado": "ausente_tras_revision"},
                "idioma": {"estado": "ausente_tras_revision"},
                "ley_aplicable_fondo": {"estado": "ausente_tras_revision"},
                "numero_arbitros": {"estado": "ausente_tras_revision"},
                "somete_tambien_a_tribunales_locales": {
                    "estado": "presente",
                    "valor": True,
                    "cita_literal": "medidas cautelares ante jueces locales",
                },
            },
            texto_documento=txt_caut,
        )
        self.assertEqual(r_caut["estado"], "REQUIERE_REVISION")
        self.assertEqual(r_caut["hallazgos"][0]["codigo"], "P1R_MENCION_JUDICIAL_POSIBLE_CAUTELAR_O_AUXILIO")

    def test_11_p2_ambiguo_siempre_exige_revision_humana(self):
        """mecanismo_final='ambiguo' activa P2 y mantiene requiere_revision_humana=True."""
        txt = "Las partes podrán resolver sus diferencias por la vía que estimen más conveniente."
        res = auditar_clausula_masc(
            datos_clausula={"mecanismo_final": "ambiguo", "cita_textual_clausula": txt},
            texto_documento=txt,
        )
        self.assertEqual(res["estado"], "PATOLOGIA_DETECTADA")
        self.assertTrue(res["requiere_revision_humana"])
        self.assertEqual(res["hallazgos"][0]["codigo"], "P2_MECANISMO_AMBIGUO")

    def test_12_p3_etapa_previa_sin_plazo_ni_cierre_y_numeros_espanol(self):
        """Detecta P3 cuando falta plazo y cierre; corrobora 'catorce días' como 14."""
        txt_sin = "Antes de acudir a juicio, las partes deberán agotar una mediación previa."
        r_sin = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "jurisdiccion_estatal",
                "cita_textual_clausula": txt_sin,
                "etapa_previa": {
                    "tipo": "mediacion",
                    "obligatoria": {"estado": "presente", "valor": True, "cita_literal": "deberán agotar una mediación previa"},
                    "plazo_dias": {"estado": "ausente_tras_revision"},
                    "condicion_cierre_objetiva": {"estado": "ausente_tras_revision"},
                },
            },
            texto_documento=txt_sin,
        )
        self.assertEqual(r_sin["estado"], "PATOLOGIA_DETECTADA")
        self.assertTrue(r_sin["requiere_revision_humana"])

        txt_14 = "Las partes negociarán durante catorce días antes de acudir a los jueces de Guadalajara."
        r_14 = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "jurisdiccion_estatal",
                "cita_textual_clausula": txt_14,
                "etapa_previa": {
                    "tipo": "negociacion",
                    "obligatoria": {"estado": "presente", "valor": True, "cita_literal": "negociarán durante catorce días"},
                    "plazo_dias": {"estado": "presente", "valor": 14, "cita_literal": "catorce días"},
                    "condicion_cierre_objetiva": {"estado": "ausente_tras_revision"},
                },
            },
            texto_documento=txt_14,
        )
        self.assertEqual(r_14["estado"], "CLAUSULA_SIN_ALERTAS_AUTOMATICAS")

    def test_13_ad_hoc_modalidad_y_catalogo_exacto_instituciones(self):
        """ad_hoc se modela como modalidad; 'CAM de la Luna' no pasa por subcadena como 'CAM'."""
        txt_adhoc = (
            "Arbitraje ad hoc bajo reglamento UNCITRAL, sede en CDMX, español, Código de Comercio, un árbitro."
        )
        r_adhoc = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "arbitraje",
                "modalidad_arbitraje": "ad_hoc",
                "cita_textual_clausula": txt_adhoc,
                "institucion_administradora": {"estado": "ausente_tras_revision"},
                "reglamento_arbitral": {"estado": "presente", "valor": "UNCITRAL", "cita_literal": "reglamento UNCITRAL"},
                "sede_arbitraje": {"estado": "presente", "valor": "Ciudad de México", "cita_literal": "sede en CDMX"},
                "idioma": {"estado": "presente", "valor": "español", "cita_literal": "español"},
                "ley_aplicable_fondo": {"estado": "presente", "valor": "Código de Comercio", "cita_literal": "Código de Comercio"},
                "numero_arbitros": {"estado": "presente", "valor": 1, "cita_literal": "un árbitro"},
                "somete_tambien_a_tribunales_locales": {"estado": "ausente_tras_revision"},
            },
            texto_documento=txt_adhoc,
        )
        self.assertEqual(r_adhoc["estado"], "CLAUSULA_SIN_ALERTAS_AUTOMATICAS")

        txt_luna = "Arbitraje ante CAM de la Luna, sede en Monterrey, español, Código de Comercio, un árbitro."
        r_luna = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "arbitraje",
                "cita_textual_clausula": txt_luna,
                "institucion_administradora": {"estado": "presente", "valor": "CAM de la Luna", "cita_literal": "CAM de la Luna"},
                "reglamento_arbitral": {"estado": "ausente_tras_revision"},
                "sede_arbitraje": {"estado": "presente", "valor": "Monterrey", "cita_literal": "sede en Monterrey"},
                "idioma": {"estado": "presente", "valor": "español", "cita_literal": "español"},
                "ley_aplicable_fondo": {"estado": "presente", "valor": "Código de Comercio", "cita_literal": "Código de Comercio"},
                "numero_arbitros": {"estado": "presente", "valor": 1, "cita_literal": "un árbitro"},
                "somete_tambien_a_tribunales_locales": {"estado": "ausente_tras_revision"},
            },
            texto_documento=txt_luna,
        )
        self.assertEqual(r_luna["estado"], "REQUIERE_REVISION")
        self.assertEqual(r_luna["hallazgos"][0]["codigo"], "P5B_IDENTIDAD_INSTITUCIONAL_PENDIENTE_COTEJO")

    def test_14_p4_sede_supletoria_y_p6_numero_par(self):
        """Omisión de sede (P4) y dos árbitros (P6) producen OBSERVACIONES_DETECTADAS (MEDIA)."""
        txt = "Arbitraje ante el CAM en idioma español, bajo Código de Comercio, con dos árbitros."
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "arbitraje",
                "cita_textual_clausula": txt,
                "institucion_administradora": {"estado": "presente", "valor": "CAM", "cita_literal": "ante el CAM"},
                "reglamento_arbitral": {"estado": "ausente_tras_revision"},
                "sede_arbitraje": {"estado": "ausente_tras_revision"},
                "idioma": {"estado": "presente", "valor": "español", "cita_literal": "idioma español"},
                "ley_aplicable_fondo": {"estado": "presente", "valor": "Código de Comercio", "cita_literal": "Código de Comercio"},
                "numero_arbitros": {"estado": "presente", "valor": 2, "cita_literal": "dos árbitros"},
                "somete_tambien_a_tribunales_locales": {"estado": "ausente_tras_revision"},
            },
            texto_documento=txt,
        )
        self.assertEqual(res["estado"], "OBSERVACIONES_DETECTADAS")
        self.assertEqual(
            {h["codigo"] for h in res["hallazgos"]},
            {"P4_OMISION_PACTO_SEDE_ARBITRAL", "P6_NUMERO_PAR_ARBITROS"},
        )

    def test_15_prioridad_requiere_revision_sobre_patologia_si_hay_falla_evidencia(self):
        """Si coexisten alerta de integridad/cobertura y patología, prevalece REQUIERE_REVISION."""
        txt = "Arbitraje en Guadalajara y también ante jueces del fuero común."
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "arbitraje",
                "cita_textual_clausula": txt,
                "idioma": {"estado": "presente", "valor": "Inglés", "cita_literal": txt},  # sin respaldo
                "somete_tambien_a_tribunales_locales": {
                    "estado": "presente",
                    "valor": True,
                    "cita_literal": "ante jueces del fuero común",
                },
            },
            texto_documento=txt,
        )
        self.assertEqual(res["estado"], "REQUIERE_REVISION")
        self.assertTrue(res["requiere_revision_humana"])

    # --- BLOQUE 3: MODELO FINANCIERO EMV / VPN / BATNA / ZOPA ---

    def test_16_simulador_caso_base_redondeo_conservador_centavos(self):
        """Caso base $1M: piso 445716.17 (CEILING) y techo 667470.47 (FLOOR)."""
        res = simular_escenario_batna_emv({
            "monto_reclamado_mxn": "1000000",
            "probabilidad_exito_actor": "0.65",
            "costos_litigio_actor_mxn": "120000",
            "costos_litigio_demandado_mxn": "150000",
            "duracion_juicio_meses": 24,
            "tasa_descuento_anual": "0.10",
            "costos_masc_actor_mxn": "25000",
            "costos_masc_demandado_mxn": "25000",
        })
        self.assertEqual(res["estado"], "ZOPA_VIABLE")
        self.assertEqual(res["negociacion_masc"]["rango_acuerdo_pagable_mxn"]["piso_actor"], "445716.17")
        self.assertEqual(res["negociacion_masc"]["rango_acuerdo_pagable_mxn"]["techo_demandado"], "667470.47")

    def test_17_identidad_algebraica_cuando_fechas_coinciden_j_igual_t(self):
        """Contraejemplo financiero v0.3: con M=100, p=1, r=0.20, J=T=24, devuelve ZOPA 100.00-100.00."""
        for monto in ("100", "110"):
            res = simular_escenario_batna_emv({
                "monto_reclamado_mxn": monto,
                "probabilidad_exito_actor": "1",
                "costos_litigio_actor_mxn": "0",
                "costos_litigio_demandado_mxn": "0",
                "duracion_juicio_meses": 24,
                "duracion_masc_meses": 24,
                "tasa_descuento_anual": "0.20",
                "costos_masc_actor_mxn": "0",
                "costos_masc_demandado_mxn": "0",
            })
            self.assertEqual(res["estado"], "ZOPA_VIABLE")
            esperado = f"{monto}.00"
            self.assertEqual(res["negociacion_masc"]["rango_acuerdo_pagable_mxn"]["piso_actor"], esperado)
            self.assertEqual(res["negociacion_masc"]["rango_acuerdo_pagable_mxn"]["techo_demandado"], esperado)

    def test_18_costas_reembolsables_transferencia_simetrica(self):
        """Con p=1, M=100 y costas reembolsables K=20, ambos coinciden en 120.00; con p=0, techo es None."""
        res_p1 = simular_escenario_batna_emv({
            "monto_reclamado_mxn": "100",
            "probabilidad_exito_actor": "1",
            "costos_litigio_actor_mxn": "0",
            "costos_litigio_demandado_mxn": "0",
            "duracion_juicio_meses": 12,
            "tasa_descuento_anual": "0",
            "costos_masc_actor_mxn": "0",
            "costos_masc_demandado_mxn": "0",
            "costas_reembolsables_entre_partes_mxn": "20",
        })
        self.assertEqual(res_p1["negociacion_masc"]["rango_acuerdo_pagable_mxn"]["piso_actor"], "120.00")
        self.assertEqual(res_p1["negociacion_masc"]["rango_acuerdo_pagable_mxn"]["techo_demandado"], "120.00")

        res_p0 = simular_escenario_batna_emv({
            "monto_reclamado_mxn": "100",
            "probabilidad_exito_actor": "0",
            "costos_litigio_actor_mxn": "0",
            "costos_litigio_demandado_mxn": "0",
            "duracion_juicio_meses": 12,
            "tasa_descuento_anual": "0",
            "costos_masc_actor_mxn": "0",
            "costos_masc_demandado_mxn": "0",
            "costas_reembolsables_entre_partes_mxn": "20",
        })
        self.assertEqual(res_p0["estado"], "SIN_ZOPA")
        self.assertIsNone(res_p0["demandado"]["maximo_pagable_ofertable_masc_mxn"])

    def test_19_medio_centavo_sin_monto_pagable_en_centavos(self):
        """M=$0.01 con p=0.5 (exacto $0.005) rechaza viabilidad porque no existe pago en centavos."""
        res = simular_escenario_batna_emv({
            "monto_reclamado_mxn": "0.01",
            "probabilidad_exito_actor": "0.5",
            "costos_litigio_actor_mxn": "0",
            "costos_litigio_demandado_mxn": "0",
            "duracion_juicio_meses": 12,
            "tasa_descuento_anual": "0",
            "costos_masc_actor_mxn": "0",
            "costos_masc_demandado_mxn": "0",
        })
        self.assertEqual(res["estado"], "SIN_ZOPA")
        self.assertEqual(res["causa_sin_zopa"], "sin_monto_pagable_en_centavos")

    def test_20_piso_operativo_un_centavo_cuando_convenio_cero_desactivado(self):
        """Con permitir_convenio_cero=False, piso matemático 0 y techo 10 produce rango 0.01-10.00."""
        res = simular_escenario_batna_emv({
            "monto_reclamado_mxn": "100",
            "probabilidad_exito_actor": "0",
            "costos_litigio_actor_mxn": "0",
            "costos_litigio_demandado_mxn": "10",
            "duracion_juicio_meses": 12,
            "tasa_descuento_anual": "0",
            "costos_masc_actor_mxn": "0",
            "costos_masc_demandado_mxn": "0",
            "permitir_convenio_cero": False,
        })
        self.assertEqual(res["estado"], "ZOPA_VIABLE")
        self.assertEqual(res["negociacion_masc"]["rango_acuerdo_pagable_mxn"]["piso_actor"], "0.01")
        self.assertEqual(res["negociacion_masc"]["rango_acuerdo_pagable_mxn"]["techo_demandado"], "10.00")

    def test_21_consistencia_retiro_actor_conocido_vs_ignorado_por_demandado(self):
        """Cuando el actor se retira y el demandado lo sabe, el costo sin acuerdo del demandado cae a 0."""
        base = {
            "monto_reclamado_mxn": "100",
            "probabilidad_exito_actor": "0",
            "costos_litigio_actor_mxn": "100",
            "costos_litigio_demandado_mxn": "20",
            "duracion_juicio_meses": 12,
            "tasa_descuento_anual": "0",
            "costos_masc_actor_mxn": "5",
            "costos_masc_demandado_mxn": "0",
            "actor_puede_desistir_sin_costo": True,
        }
        r_ignora = simular_escenario_batna_emv({**base, "demandado_conoce_retiro_actor": False})
        self.assertEqual(r_ignora["estado"], "ZOPA_VIABLE")
        self.assertEqual(r_ignora["negociacion_masc"]["rango_acuerdo_pagable_mxn"]["piso_actor"], "5.00")
        self.assertEqual(r_ignora["negociacion_masc"]["rango_acuerdo_pagable_mxn"]["techo_demandado"], "20.00")

        r_conoce = simular_escenario_batna_emv({**base, "demandado_conoce_retiro_actor": True})
        self.assertEqual(r_conoce["estado"], "SIN_ZOPA")
        self.assertEqual(r_conoce["causa_sin_zopa"], "alternativa_desistir_supera_oferta_demandado")

    def test_22_validacion_estricta_rechaza_coerciones_escala_y_1e30(self):
        """Rechaza 'yes', '2', espacios, 1E30 y escala decimal excesiva."""
        self.assertEqual(
            auditar_clausula_masc(
                datos_clausula={
                    "mecanismo_final": "arbitraje",
                    "cita_textual_clausula": "Cláusula arbitral válida de prueba.",
                    "somete_tambien_a_tribunales_locales": {"estado": "presente", "valor": "yes"},
                },
                texto_documento="Cláusula arbitral válida de prueba.",
            )["estado"],
            "ERROR_VALIDACION",
        )
        self.assertEqual(
            simular_escenario_batna_emv({
                "monto_reclamado_mxn": "1E30",
                "probabilidad_exito_actor": "0.5",
                "costos_litigio_actor_mxn": "0",
                "costos_litigio_demandado_mxn": "0",
                "duracion_juicio_meses": 12,
                "tasa_descuento_anual": "0",
                "costos_masc_actor_mxn": "0",
                "costos_masc_demandado_mxn": "0",
            })["estado"],
            "ERROR_VALIDACION",
        )

    def test_23_matriz_sensibilidad_emv_escenarios_default(self):
        """Evalúa los 4 escenarios por defecto y verifica consistencia con el motor individual."""
        res = simular_matriz_sensibilidad_emv({
            "monto_reclamado_mxn": "1000000",
            "costos_litigio_actor_mxn": "120000",
            "costos_litigio_demandado_mxn": "150000",
            "duracion_juicio_meses": 24,
            "tasa_descuento_anual": "0.10",
            "costos_masc_actor_mxn": "25000",
            "costos_masc_demandado_mxn": "25000",
        })
        self.assertEqual(res["estado"], "OK")
        self.assertEqual(res["resumen"]["total_escenarios"], 4)
        fila_65 = res["matriz_escenarios"][2]
        self.assertEqual(fila_65["piso_actor_masc_mxn"], "445716.17")
        self.assertEqual(fila_65["techo_demandado_masc_mxn"], "667470.47")

    # --- BLOQUE 4: INTEGRACIÓN REAL DEL SERVIDOR MCP POR STDIO JSON-RPC ---

    def test_24_mcp_stdio_subproceso_initialize_list_y_call(self):
        """Abre un subproceso real del servidor MCP y ejecuta initialize -> tools/list -> tools/call por stdio."""
        ruta_servidor = Path(__file__).resolve().parent / "servidor_mcp.py"
        proc = subprocess.Popen(
            [sys.executable, str(ruta_servidor)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            def _enviar(obj: dict) -> None:
                assert proc.stdin is not None
                proc.stdin.write(json.dumps(obj) + "\n")
                proc.stdin.flush()

            def _leer() -> dict:
                assert proc.stdout is not None
                linea = proc.stdout.readline()
                self.assertTrue(linea, "El servidor MCP cerró stdout sin responder.")
                return json.loads(linea)

            # 1. initialize
            _enviar({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test-suite", "version": "0.3.1"},
                },
            })
            r_init = _leer()
            self.assertEqual(r_init["id"], 1)
            self.assertEqual(r_init["result"]["serverInfo"]["version"], "0.3.1")

            # 2. notifications/initialized
            _enviar({"jsonrpc": "2.0", "method": "notifications/initialized"})

            # 3. tools/list
            _enviar({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            r_tools = _leer()
            herramientas = {t["name"]: t["inputSchema"] for t in r_tools["result"]["tools"]}
            self.assertEqual(
                set(herramientas.keys()),
                {
                    "leer_documento_contrato",
                    "auditar_clausula_masc",
                    "simular_batna_emv",
                    "simular_matriz_sensibilidad_emv",
                },
            )
            self.assertNotIn("directorio_autorizado", herramientas["leer_documento_contrato"].get("properties", {}))
            self.assertIn("$defs", herramientas["auditar_clausula_masc"])

            # 4. tools/call válida (simular_batna_emv)
            _enviar({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "simular_batna_emv",
                    "arguments": {
                        "parametros": {
                            "monto_reclamado_mxn": "100",
                            "probabilidad_exito_actor": "1",
                            "costos_litigio_actor_mxn": "0",
                            "costos_litigio_demandado_mxn": "0",
                            "duracion_juicio_meses": 24,
                            "duracion_masc_meses": 24,
                            "tasa_descuento_anual": "0.20",
                            "costos_masc_actor_mxn": "0",
                            "costos_masc_demandado_mxn": "0",
                        }
                    },
                },
            })
            r_call_ok = _leer()
            self.assertFalse(r_call_ok["result"].get("isError", False))
            payload_ok = json.loads(r_call_ok["result"]["content"][0]["text"])
            self.assertEqual(payload_ok["estado"], "ZOPA_VIABLE")
            self.assertEqual(payload_ok["negociacion_masc"]["rango_acuerdo_pagable_mxn"]["piso_actor"], "100.00")

            # 5. tools/call denegada (leer_documento_contrato fuera de sandbox)
            _enviar({
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "leer_documento_contrato",
                    "arguments": {"ruta_archivo": "../servidor_mcp.py"},
                },
            })
            r_call_den = _leer()
            self.assertFalse(r_call_den["result"].get("isError", False))
            payload_den = json.loads(r_call_den["result"]["content"][0]["text"])
            self.assertEqual(payload_den["estado"], "ACCESO_DENEGADO")
        finally:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)