import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from docx import Document

import servidor_mcp
from lector_documentos import extraer_documento, verificar_cita_en_texto
from auditor_masc import auditar_clausula_masc
from simulador_emv import (
    simular_escenario_batna_emv,
    simular_matriz_sensibilidad_emv,
)


def _crear_pdf_valido(paginas_texto: list[str], ruta: Path) -> None:
    """Genera un archivo PDF 1.4 estructuralmente válido (soporta páginas con texto y páginas vacías)."""
    objetos: list[bytes] = []
    n_pags = len(paginas_texto)
    # IDs: 1=Catalog, 2=Pages, 3=Font, luego pares (contenido, pagina)
    ids_paginas = [4 + i * 2 + 1 for i in range(n_pags)]
    kids = " ".join(f"{pid} 0 R" for pid in ids_paginas)

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
        objetos.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {c_id} 0 R >>"
            ).encode("ascii")
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


class TestAuditorMASCyEMVv03(unittest.TestCase):
    """Suite de regresión v0.3: sandbox, DOCX complejo, PDFs mixtos, evidencia por atributo, EMV y MCP."""

    # --- BLOQUE 1: SEGURIDAD E INGESTA DOCUMENTAL ---

    def test_01_sandbox_bloquea_path_traversal_en_lector_y_auditor(self):
        """Rechaza rutas fuera del directorio autorizado antes de leer disco."""
        with tempfile.TemporaryDirectory() as tmpdir:
            raiz = Path(tmpdir) / "expedientes"
            raiz.mkdir()
            secreto = Path(tmpdir) / "secreto.txt"
            secreto.write_text("DATO CONFIDENCIAL FUERA DE SANDBOX", encoding="utf-8")

            res_lec = extraer_documento("../secreto.txt", directorio_autorizado=str(raiz))
            self.assertEqual(res_lec["estado"], "ACCESO_DENEGADO")

            res_aud = auditar_clausula_masc(
                datos_clausula={
                    "mecanismo_final": "arbitraje",
                    "cita_textual_clausula": "DATO CONFIDENCIAL FUERA DE SANDBOX",
                },
                ruta_archivo=str(secreto),
                directorio_autorizado=str(raiz),
            )
            self.assertEqual(res_aud["estado"], "ACCESO_DENEGADO")

    def test_02_fail_closed_archivos_invalidos_y_vacio(self):
        """Bloquea archivos inexistentes, extensiones no permitidas y archivos de 0 bytes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            res_inex = extraer_documento("no_existe.pdf", directorio_autorizado=tmpdir)
            self.assertEqual(res_inex["estado"], "ERROR_ARCHIVO")

            ruta_exe = Path(tmpdir) / "malicioso.exe"
            ruta_exe.write_bytes(b"MZ")
            res_ext = extraer_documento("malicioso.exe", directorio_autorizado=tmpdir)
            self.assertEqual(res_ext["estado"], "FUERA_DE_ALCANCE")

            ruta_vacia = Path(tmpdir) / "vacio.txt"
            ruta_vacia.write_text("", encoding="utf-8")
            res_vacio = extraer_documento("vacio.txt", directorio_autorizado=tmpdir)
            self.assertEqual(res_vacio["estado"], "ERROR_ARCHIVO")

    def test_03_docx_orden_encabezado_pie_fusiones_y_tabla_anidada(self):
        """Preserva orden del cuerpo, extrae encabezado/pie y subtabla, y deduplica fusiones horizontal/vertical."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ruta = Path(tmpdir) / "complejo.docx"
            doc = Document()
            doc.sections[0].header.paragraphs[0].text = "ENCABEZADO CONTRATO 2026"
            doc.sections[0].footer.paragraphs[0].text = "PIE DE PAGINA CONFIDENCIAL"
            doc.add_paragraph("PRIMER PARRAFO ANTES DE TABLA")

            tabla = doc.add_table(rows=2, cols=2)
            # Fusión horizontal en fila 0
            tabla.cell(0, 0).merge(tabla.cell(0, 1))
            tabla.cell(0, 0).text = "TITULO FUSIONADO HORIZONTAL"
            # Fusión vertical en columna 0 (fila 1 con nueva fila 2)
            fila2 = tabla.add_row()
            tabla.cell(1, 0).merge(fila2.cells[0])
            tabla.cell(1, 0).text = "CELDA VERTICAL UNICA"
            tabla.cell(1, 1).text = "Valor Fila 1"
            fila2.cells[1].text = "Valor Fila 2"
            # Tabla anidada dentro de celda (1, 1)
            sub = tabla.cell(1, 1).add_table(rows=1, cols=1)
            sub.cell(0, 0).text = "CLAUSULA EN TABLA ANIDADA"

            doc.add_paragraph("ULTIMO PARRAFO DESPUES DE TABLA")
            doc.save(ruta)

            res = extraer_documento("complejo.docx", directorio_autorizado=tmpdir)
            self.assertEqual(res["estado"], "OK")
            self.assertTrue(res["cobertura"]["orden_preservado"])
            textos = [b["texto"] for b in res["bloques_numerados"]]
            todo = "\n".join(textos)

            self.assertIn("ENCABEZADO CONTRATO 2026", todo)
            self.assertIn("PIE DE PAGINA CONFIDENCIAL", todo)
            self.assertIn("CLAUSULA EN TABLA ANIDADA", todo)
            self.assertEqual(todo.count("TITULO FUSIONADO HORIZONTAL"), 1)
            self.assertEqual(todo.count("CELDA VERTICAL UNICA"), 1)
            # Verificar orden real: primer párrafo antes de tabla y último párrafo después
            idx_primero = next(i for i, t in enumerate(textos) if "PRIMER PARRAFO" in t)
            idx_tabla = next(i for i, t in enumerate(textos) if "TITULO FUSIONADO" in t)
            idx_ultimo = next(i for i, t in enumerate(textos) if "ULTIMO PARRAFO" in t)
            self.assertLess(idx_primero, idx_tabla)
            self.assertLess(idx_tabla, idx_ultimo)

    def test_04_pdf_valido_mixto_reporta_extraccion_parcial(self):
        """Un PDF válido con página 1 de texto y página 2 sin capa de texto devuelve EXTRACCION_PARCIAL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ruta = Path(tmpdir) / "mixto.pdf"
            _crear_pdf_valido(
                ["CONTRATO MERCANTIL PORTADA CON TEXTO SUFICIENTE", ""],
                ruta,
            )
            res = extraer_documento("mixto.pdf", directorio_autorizado=tmpdir)
            self.assertEqual(res["estado"], "EXTRACCION_PARCIAL")
            self.assertFalse(res["cobertura"]["completa"])
            self.assertEqual(res["cobertura"]["paginas_con_texto"], [1])
            self.assertEqual(res["cobertura"]["paginas_sin_texto"], [2])

    def test_05_pdf_valido_sin_texto_falla_cerrado(self):
        """Un PDF válido compuesto solo por páginas sin texto devuelve PDF_SIN_TEXTO_EXTRAIBLE."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ruta = Path(tmpdir) / "escaneado.pdf"
            _crear_pdf_valido(["", ""], ruta)
            res = extraer_documento("escaneado.pdf", directorio_autorizado=tmpdir)
            self.assertEqual(res["estado"], "PDF_SIN_TEXTO_EXTRAIBLE")
            self.assertEqual(res["cobertura"]["paginas_sin_texto"], [1, 2])

    def test_06_normalizacion_espacios_y_cita_no_verificada(self):
        """Tolera saltos de línea pero bloquea citas alteradas con CITA_NO_VERIFICADA."""
        doc = "DECIMA.\nToda   controversia se resolvera\nmediante arbitraje en Guadalajara."
        self.assertTrue(
            verificar_cita_en_texto("Toda controversia se resolvera mediante arbitraje en Guadalajara.", doc)
        )
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "arbitraje",
                "cita_textual_clausula": "Toda controversia se resolvera mediante arbitraje en Zapopan.",
            },
            texto_documento=doc,
        )
        self.assertEqual(res["estado"], "CITA_NO_VERIFICADA")

    # --- BLOQUE 2: EVIDENCIA POR ATRIBUTO Y REGLAS JURÍDICAS ---

    def test_07_detecta_atributos_sin_respaldo_caso_antiguo_test15(self):
        """Bloqueante principal v0.2: detecta idioma, ley y número de árbitros inyectados sin respaldo."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ruta = Path(tmpdir) / "contrato_tabla.docx"
            doc = Document()
            tabla = doc.add_table(rows=1, cols=2)
            tabla.cell(0, 0).text = "Solución de controversias"
            cita = "Toda disputa se resolverá por arbitraje institucional en el CAM con sede en Zapopan."
            tabla.cell(0, 1).text = cita
            doc.save(ruta)

            res = auditar_clausula_masc(
                datos_clausula={
                    "mecanismo_final": "arbitraje",
                    "cita_textual_clausula": cita,
                    "institucion_administradora": {"estado": "presente", "valor": "CAM", "cita_literal": cita},
                    "sede_arbitraje": {"estado": "presente", "valor": "Zapopan", "cita_literal": cita},
                    "idioma": {"estado": "presente", "valor": "Español", "cita_literal": cita},
                    "ley_aplicable_fondo": {"estado": "presente", "valor": "Código de Comercio", "cita_literal": cita},
                    "numero_arbitros": {"estado": "presente", "valor": 1, "cita_literal": cita},
                    "reglamento_arbitral": {"estado": "ausente_tras_revision"},
                    "somete_tambien_a_tribunales_locales": {"estado": "ausente_tras_revision"},
                },
                ruta_archivo="contrato_tabla.docx",
                directorio_autorizado=tmpdir,
            )
            self.assertEqual(res["estado"], "REQUIERE_REVISION")
            self.assertTrue(res["requiere_revision_humana"])
            atributos_alertados = {a["atributo"] for a in res["alertas_integridad_evidencia"]}
            self.assertEqual(atributos_alertados, {"idioma", "ley_aplicable_fondo", "numero_arbitros"})

    def test_08_frase_negociacion_no_pasa_como_arbitraje_sano(self):
        """Una frase que solo habla de negociar no puede validarse como arbitraje."""
        txt = "Las partes se reunirán de buena fe para negociar cualquier diferencia comercial."
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "arbitraje",
                "cita_textual_clausula": txt,
                "sede_arbitraje": {"estado": "presente", "valor": "Guadalajara", "cita_literal": txt},
            },
            texto_documento=txt,
        )
        self.assertEqual(res["estado"], "REQUIERE_REVISION")
        atributos = {a["atributo"] for a in res["alertas_integridad_evidencia"]}
        self.assertIn("mecanismo_final", atributos)
        self.assertIn("sede_arbitraje", atributos)

    def test_09_patologia_p1_jurisdiccion_contradictoria(self):
        """Detecta P1 crítica cuando se corrobora sumisión simultánea a arbitraje y jueces locales."""
        txt = "DÉCIMA. Toda controversia se resolverá mediante arbitraje ante el CAM y ante los jueces del fuero común."
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "arbitraje",
                "cita_textual_clausula": txt,
                "institucion_administradora": {"estado": "presente", "valor": "CAM", "cita_literal": txt},
                "reglamento_arbitral": {"estado": "ausente_tras_revision"},
                "sede_arbitraje": {"estado": "ausente_tras_revision"},
                "idioma": {"estado": "ausente_tras_revision"},
                "ley_aplicable_fondo": {"estado": "ausente_tras_revision"},
                "numero_arbitros": {"estado": "ausente_tras_revision"},
                "somete_tambien_a_tribunales_locales": {
                    "estado": "presente",
                    "valor": True,
                    "cita_literal": "ante los jueces del fuero común",
                },
            },
            texto_documento=txt,
        )
        self.assertEqual(res["estado"], "PATOLOGIA_DETECTADA")
        self.assertEqual(res["severidad_maxima"], "CRITICA")
        codigos = {h["codigo"] for h in res["hallazgos"]}
        self.assertIn("P1_JURISDICCION_CONTRADICTORIA", codigos)

    def test_10_patologia_p2_mecanismo_ambiguo(self):
        """Prueba dedicada a P2: mecanismo_final='ambiguo' dispara severidad CRITICA."""
        txt = "Las partes podrán resolver sus diferencias por la vía que estimen más conveniente."
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "ambiguo",
                "cita_textual_clausula": txt,
            },
            texto_documento=txt,
        )
        self.assertEqual(res["estado"], "PATOLOGIA_DETECTADA")
        self.assertEqual(res["hallazgos"][0]["codigo"], "P2_MECANISMO_AMBIGUO")

    def test_11_patologia_p3_etapa_previa_sin_plazo_ni_cierre(self):
        """Detecta P3 cuando la etapa previa obligatoria carece de plazo y de condición objetiva de cierre."""
        txt = "Antes de acudir a los jueces de Guadalajara, las partes deberán agotar una mediación previa."
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "jurisdiccion_estatal",
                "cita_textual_clausula": txt,
                "etapa_previa": {
                    "tipo": "mediacion",
                    "obligatoria": {"estado": "presente", "valor": True, "cita_literal": "deberán agotar una mediación previa"},
                    "plazo_dias": {"estado": "ausente_tras_revision"},
                    "condicion_cierre_objetiva": {"estado": "ausente_tras_revision"},
                },
            },
            texto_documento=txt,
        )
        self.assertEqual(res["estado"], "PATOLOGIA_DETECTADA")
        self.assertEqual(res["hallazgos"][0]["codigo"], "P3_ESCALAMIENTO_SIN_PLAZO")

    def test_12_escalonada_hacia_juicio_estatal_no_dispara_reglas_arbitrales(self):
        """Una negociación con plazo seguida de juicio estatal no activa P4–P7."""
        txt = "Las partes negociarán durante 15 días; de no convenir, se someterán a los jueces de Zapopan."
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "jurisdiccion_estatal",
                "cita_textual_clausula": txt,
                "etapa_previa": {
                    "tipo": "negociacion",
                    "obligatoria": {"estado": "presente", "valor": True, "cita_literal": "negociarán durante 15 días"},
                    "plazo_dias": {"estado": "presente", "valor": 15, "cita_literal": "15 días"},
                    "condicion_cierre_objetiva": {"estado": "ausente_tras_revision"},
                },
            },
            texto_documento=txt,
        )
        self.assertEqual(res["estado"], "CLAUSULA_SIN_ALERTAS_AUTOMATICAS")
        self.assertEqual(res["total_hallazgos"], 0)

    def test_13_ad_hoc_sin_sede_es_observacion_supletoria_no_patologia(self):
        """Arbitraje ad hoc con reglamento UNCITRAL y sin sede recibe observación MEDIA (P4), no patología."""
        txt = (
            "Toda controversia se resolverá mediante arbitraje ad hoc bajo el reglamento UNCITRAL, "
            "en idioma español, conforme al Código de Comercio, ante tres árbitros."
        )
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "arbitraje",
                "cita_textual_clausula": txt,
                "institucion_administradora": {"estado": "ausente_tras_revision"},
                "reglamento_arbitral": {"estado": "presente", "valor": "UNCITRAL", "cita_literal": "reglamento UNCITRAL"},
                "sede_arbitraje": {"estado": "ausente_tras_revision"},
                "idioma": {"estado": "presente", "valor": "español", "cita_literal": "idioma español"},
                "ley_aplicable_fondo": {"estado": "presente", "valor": "Código de Comercio", "cita_literal": "Código de Comercio"},
                "numero_arbitros": {"estado": "presente", "valor": 3, "cita_literal": "tres árbitros"},
                "somete_tambien_a_tribunales_locales": {"estado": "ausente_tras_revision"},
            },
            texto_documento=txt,
        )
        self.assertEqual(res["estado"], "OBSERVACIONES_DETECTADAS")
        self.assertEqual(res["severidad_maxima"], "MEDIA")
        self.assertEqual([h["codigo"] for h in res["hallazgos"]], ["P4_OMISION_PACTO_SEDE_ARBITRAL"])

    def test_14_institucion_desconocida_dispara_p5b_y_revision_humana(self):
        """Una institución arbitral no corroborada en catálogo activa P5B y bloquea aprobación limpia."""
        txt = (
            "Toda disputa se someterá a arbitraje ante el Centro Imaginario de Arbitraje del Norte, "
            "con sede en Monterrey, idioma español, Código de Comercio y un árbitro."
        )
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "arbitraje",
                "cita_textual_clausula": txt,
                "institucion_administradora": {
                    "estado": "presente",
                    "valor": "Centro Imaginario de Arbitraje del Norte",
                    "cita_literal": "Centro Imaginario de Arbitraje del Norte",
                },
                "reglamento_arbitral": {"estado": "ausente_tras_revision"},
                "sede_arbitraje": {"estado": "presente", "valor": "Monterrey", "cita_literal": "sede en Monterrey"},
                "idioma": {"estado": "presente", "valor": "español", "cita_literal": "idioma español"},
                "ley_aplicable_fondo": {"estado": "presente", "valor": "Código de Comercio", "cita_literal": "Código de Comercio"},
                "numero_arbitros": {"estado": "presente", "valor": 1, "cita_literal": "un árbitro"},
                "somete_tambien_a_tribunales_locales": {"estado": "ausente_tras_revision"},
            },
            texto_documento=txt,
        )
        self.assertEqual(res["estado"], "REQUIERE_REVISION")
        self.assertEqual(res["hallazgos"][0]["codigo"], "P5B_INSTITUCION_NO_CORROBORADA")

    def test_15_clausula_completa_verificada_sin_alertas(self):
        """Una cláusula con todos los atributos corroborados en el texto emite CLAUSULA_SIN_ALERTAS_AUTOMATICAS."""
        txt = (
            "CLÁUSULA DÉCIMA. Las partes agotarán una negociación de 15 días; de persistir el conflicto, "
            "se someterán a arbitraje ante el CAM, con sede en Guadalajara, en idioma español, "
            "bajo el Código de Comercio, ante tres árbitros."
        )
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "arbitraje",
                "cita_textual_clausula": txt,
                "etapa_previa": {
                    "tipo": "negociacion",
                    "obligatoria": {"estado": "presente", "valor": True, "cita_literal": "agotarán una negociación de 15 días"},
                    "plazo_dias": {"estado": "presente", "valor": 15, "cita_literal": "15 días"},
                    "condicion_cierre_objetiva": {"estado": "ausente_tras_revision"},
                },
                "institucion_administradora": {"estado": "presente", "valor": "CAM", "cita_literal": "ante el CAM"},
                "reglamento_arbitral": {"estado": "ausente_tras_revision"},
                "sede_arbitraje": {"estado": "presente", "valor": "Guadalajara", "cita_literal": "sede en Guadalajara"},
                "idioma": {"estado": "presente", "valor": "español", "cita_literal": "idioma español"},
                "ley_aplicable_fondo": {"estado": "presente", "valor": "Código de Comercio", "cita_literal": "Código de Comercio"},
                "numero_arbitros": {"estado": "presente", "valor": 3, "cita_literal": "tres árbitros"},
                "somete_tambien_a_tribunales_locales": {"estado": "ausente_tras_revision"},
            },
            texto_documento=txt,
        )
        self.assertEqual(res["estado"], "CLAUSULA_SIN_ALERTAS_AUTOMATICAS")
        self.assertFalse(res["requiere_revision_humana"])
        self.assertEqual(res["total_hallazgos"], 0)

    # --- BLOQUE 3: MODELO FINANCIERO EMV / VPN / BATNA / ZOPA ---

    def test_16_simulador_caso_base_redondeo_conservador_centavos(self):
        """El piso redondea hacia arriba (CEILING) y el techo hacia abajo (FLOOR): 445716.17 a 667470.47."""
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

    def test_17_costas_reembolsables_transferencia_simetrica_sin_zopa_artificial(self):
        """Con p=1, M=100 y costas reembolsables K=20 (sin otros costos), ambos coinciden en 120.00."""
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
        self.assertEqual(res_p1["estado"], "ZOPA_VIABLE")
        self.assertEqual(res_p1["negociacion_masc"]["rango_acuerdo_pagable_mxn"]["piso_actor"], "120.00")
        self.assertEqual(res_p1["negociacion_masc"]["rango_acuerdo_pagable_mxn"]["techo_demandado"], "120.00")

        # Con p=0, el demandado recupera K=20 -> ningún pago no negativo lo satisface
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
        self.assertEqual(res_p0["causa_sin_zopa"], "ningun_pago_no_negativo_satisface_al_demandado")
        self.assertIsNone(res_p0["demandado"]["maximo_pagable_ofertable_masc_mxn"])

    def test_18_medio_centavo_sin_monto_pagable_en_centavos(self):
        """M=$0.01 con p=0.5 (valor exacto $0.005) rechaza viabilidad porque piso=$0.01 > techo=$0.00."""
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

    def test_19_costos_masc_superan_ahorro_litigio_y_convenio_cero(self):
        """Distingue causa por costos MASC altos y comportamiento explícito de permitir_convenio_cero."""
        res_costos = simular_escenario_batna_emv({
            "monto_reclamado_mxn": "1000000",
            "probabilidad_exito_actor": "0.90",
            "costos_litigio_actor_mxn": "20000",
            "costos_litigio_demandado_mxn": "20000",
            "duracion_juicio_meses": 12,
            "tasa_descuento_anual": "0.05",
            "costos_masc_actor_mxn": "50000",
            "costos_masc_demandado_mxn": "50000",
        })
        self.assertEqual(res_costos["estado"], "SIN_ZOPA")
        self.assertEqual(res_costos["causa_sin_zopa"], "costos_masc_superan_ahorro_litigio")

        base_cero = {
            "monto_reclamado_mxn": "100",
            "probabilidad_exito_actor": "0",
            "costos_litigio_actor_mxn": "0",
            "costos_litigio_demandado_mxn": "0",
            "duracion_juicio_meses": 12,
            "tasa_descuento_anual": "0",
            "costos_masc_actor_mxn": "0",
            "costos_masc_demandado_mxn": "0",
        }
        self.assertEqual(simular_escenario_batna_emv(base_cero)["causa_sin_zopa"], "acuerdo_solo_en_cero_sin_pago")
        res_cero_ok = simular_escenario_batna_emv({**base_cero, "permitir_convenio_cero": True})
        self.assertEqual(res_cero_ok["estado"], "ZOPA_VIABLE")
        self.assertEqual(res_cero_ok["negociacion_masc"]["rango_acuerdo_pagable_mxn"]["piso_actor"], "0.00")

    def test_20_alternativa_desistir_cambia_piso_racional_del_actor(self):
        """Si el actor puede desistir a costo 0 y negociar cuesta 10, su piso es 10.00 (no 0.00)."""
        res = simular_escenario_batna_emv({
            "monto_reclamado_mxn": "100",
            "probabilidad_exito_actor": "0",
            "costos_litigio_actor_mxn": "50",
            "costos_litigio_demandado_mxn": "50",
            "duracion_juicio_meses": 12,
            "tasa_descuento_anual": "0",
            "costos_masc_actor_mxn": "10",
            "costos_masc_demandado_mxn": "10",
            "actor_puede_desistir_sin_costo": True,
        })
        self.assertEqual(res["actor"]["batna_efectivo_vpn_mxn"], "0.00")
        self.assertEqual(res["actor"]["minimo_pagable_aceptable_masc_mxn"], "10.00")

    def test_21_validacion_estricta_rechaza_coerciones_espacios_y_1e30(self):
        """Rechaza 'yes' como bool, '2' como int, cadenas de espacios, 1E30 y probabilidades fuera de [0,1]."""
        # Booleano estricto en esquemas
        res_bool_str = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "arbitraje",
                "cita_textual_clausula": "Cláusula arbitral válida de prueba.",
                "somete_tambien_a_tribunales_locales": {"estado": "presente", "valor": "yes"},
            },
            texto_documento="Cláusula arbitral válida de prueba.",
        )
        self.assertEqual(res_bool_str["estado"], "ERROR_VALIDACION")

        # Entero estricto y cadena en blanco
        res_int_str = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "arbitraje",
                "cita_textual_clausula": "Cláusula arbitral válida de prueba.",
                "numero_arbitros": {"estado": "presente", "valor": "2"},
            },
            texto_documento="Cláusula arbitral válida de prueba.",
        )
        self.assertEqual(res_int_str["estado"], "ERROR_VALIDACION")

        res_espacios = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_final": "arbitraje",
                "cita_textual_clausula": "Cláusula arbitral válida de prueba.",
                "idioma": {"estado": "presente", "valor": "   "},
            },
            texto_documento="Cláusula arbitral válida de prueba.",
        )
        self.assertEqual(res_espacios["estado"], "ERROR_VALIDACION")

        # 1E30 bloqueado por cota MAX_MONTO_MXN
        res_1e30 = simular_escenario_batna_emv({
            "monto_reclamado_mxn": "1E30",
            "probabilidad_exito_actor": "0.5",
            "costos_litigio_actor_mxn": "0",
            "costos_litigio_demandado_mxn": "0",
            "duracion_juicio_meses": 12,
            "tasa_descuento_anual": "0",
            "costos_masc_actor_mxn": "0",
            "costos_masc_demandado_mxn": "0",
        })
        self.assertEqual(res_1e30["estado"], "ERROR_VALIDACION")

        # Probabilidad fuera de rango [0, 1] en matriz
        res_prob = simular_matriz_sensibilidad_emv({
            "monto_reclamado_mxn": "100000",
            "probabilidades_exito_actor": ["1.2"],
            "costos_litigio_actor_mxn": "10000",
            "costos_litigio_demandado_mxn": "10000",
            "duracion_juicio_meses": 12,
            "tasa_descuento_anual": "0.10",
            "costos_masc_actor_mxn": "2000",
            "costos_masc_demandado_mxn": "2000",
        })
        self.assertEqual(res_prob["estado"], "ERROR_VALIDACION")

    def test_22_matriz_sensibilidad_emv_escenarios_default(self):
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

    # --- BLOQUE 4: INTEGRACIÓN REAL DEL SERVIDOR MCP ---

    def test_23_mcp_list_tools_publica_esquemas_tipados_sin_directorio_autorizado(self):
        """Verifica tools/list real en MCPServer: 4 herramientas tipadas sin exponer directorio_autorizado."""
        tools = asyncio.run(servidor_mcp.mcp.list_tools())
        mapa = {t.name: t.input_schema for t in tools}

        self.assertEqual(
            set(mapa.keys()),
            {
                "leer_documento_contrato",
                "auditar_clausula_masc",
                "simular_batna_emv",
                "simular_matriz_sensibilidad_emv",
            },
        )
        # Seguridad: el cliente MCP nunca puede sobrescribir la raíz autorizada
        self.assertNotIn("directorio_autorizado", mapa["leer_documento_contrato"].get("properties", {}))
        self.assertNotIn("directorio_autorizado", mapa["auditar_clausula_masc"].get("properties", {}))
        # Tipado real: debe publicar definiciones Pydantic ($defs) en el JSON Schema
        self.assertIn("$defs", mapa["auditar_clausula_masc"])
        self.assertIn("$defs", mapa["simular_batna_emv"])

    def test_24_mcp_call_tool_ejecucion_real_valida_y_denegada(self):
        """Ejecuta call_tool real a través de MCPServer para una llamada financiera válida y una lectura denegada."""
        res_ok = asyncio.run(
            servidor_mcp.mcp.call_tool(
                "simular_batna_emv",
                {
                    "parametros": {
                        "monto_reclamado_mxn": "1000000",
                        "probabilidad_exito_actor": "0.65",
                        "costos_litigio_actor_mxn": "120000",
                        "costos_litigio_demandado_mxn": "150000",
                        "duracion_juicio_meses": 24,
                        "tasa_descuento_anual": "0.10",
                        "costos_masc_actor_mxn": "25000",
                        "costos_masc_demandado_mxn": "25000",
                    }
                },
            )
        )
        texto_ok = str(res_ok)
        self.assertIn("ZOPA_VIABLE", texto_ok)
        self.assertIn("667470.47", texto_ok)

        res_denegado = asyncio.run(
            servidor_mcp.mcp.call_tool(
                "leer_documento_contrato",
                {"ruta_archivo": "../servidor_mcp.py"},
            )
        )
        self.assertIn("ACCESO_DENEGADO", str(res_denegado))


if __name__ == "__main__":
    unittest.main(verbosity=2)