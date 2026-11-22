import inspect
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


class TestAuditorMASCyEMVv02(unittest.TestCase):
    """Suite de regresión v0.2: ingesta DOCX (párrafos y tablas), evidence spans, patologías y EMV/VPN."""

    def test_01_extraccion_docx_real_en_disco(self):
        """Lee un contrato .docx real y extrae párrafos numerados."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ruta = Path(tmpdir) / "contrato_prueba.docx"
            doc = Document()
            doc.add_paragraph("CONTRATO DE PRESTACIÓN DE SERVICIOS MERCANTILES")
            doc.add_paragraph(
                "CLÁUSULA DÉCIMA QUINTA. Toda controversia derivada de este contrato "
                "se resolverá mediante arbitraje administrado por el CAM con sede en Ciudad de México."
            )
            doc.save(ruta)

            res = extraer_documento(str(ruta))
            self.assertEqual(res["estado"], "OK")
            self.assertEqual(res["total_bloques"], 2)
            self.assertIn("CAM", res["texto_completo"])

    def test_02_fail_closed_archivos_invalidos_y_vacio(self):
        """Bloquea archivos inexistentes, extensiones prohibidas y archivos de 0 bytes."""
        res_inex = extraer_documento("no_existe_contrato_12345.pdf")
        self.assertEqual(res_inex["estado"], "ERROR_ARCHIVO")

        with tempfile.TemporaryDirectory() as tmpdir:
            ruta_exe = Path(tmpdir) / "malicioso.exe"
            ruta_exe.write_bytes(b"MZ")
            res_ext = extraer_documento(str(ruta_exe))
            self.assertEqual(res_ext["estado"], "FUERA_DE_ALCANCE")

            ruta_vacia = Path(tmpdir) / "vacio.txt"
            ruta_vacia.write_text("", encoding="utf-8")
            res_vacio = extraer_documento(str(ruta_vacia))
            self.assertEqual(res_vacio["estado"], "ERROR_ARCHIVO")

    def test_03_normalizacion_espacios_en_citas(self):
        """Tolera saltos de línea de PDF/DOCX pero rechaza palabras alteradas."""
        doc = "DÉCIMA.\nToda   controversia se resolverá\nmediante arbitraje en Guadalajara."
        cita_con_saltos = "Toda controversia se resolverá mediante arbitraje en Guadalajara."
        cita_alterada = "Toda controversia se resolverá mediante arbitraje en Zapopan."

        self.assertTrue(verificar_cita_en_texto(cita_con_saltos, doc))
        self.assertFalse(verificar_cita_en_texto(cita_alterada, doc))

    def test_04_bloqueo_cita_no_verificada(self):
        """Si el LLM inventa o altera una cita, el auditor bloquea con CITA_NO_VERIFICADA."""
        doc = "CLÁUSULA NOVENA. Las partes se someten a los tribunales de Zapopan, Jalisco."
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_principal": "arbitraje",
                "sede_arbitraje": "Guadalajara",
                "cita_textual_clausula": "Las partes se someten a arbitraje en Guadalajara, Jalisco.",
            },
            texto_documento=doc,
        )
        self.assertEqual(res["estado"], "CITA_NO_VERIFICADA")

    def test_05_patologia_critica_jurisdiccion_contradictoria_y_sin_sede(self):
        """Detecta P1 (sumisión contradictoria), P4 (sin sede) y P5 (sin institución)."""
        doc = (
            "DÉCIMA. Toda controversia se resolverá mediante arbitraje y también "
            "ante los jueces competentes del fuero común."
        )
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_principal": "arbitraje",
                "somete_tambien_a_tribunales_locales": True,
                "cita_textual_clausula": doc,
            },
            texto_documento=doc,
        )
        self.assertEqual(res["estado"], "PATOLOGIA_DETECTADA")
        codigos = {h["codigo"] for h in res["hallazgos"]}
        self.assertIn("P1_JURISDICCION_CONTRADICTORIA", codigos)
        self.assertIn("P4_AUSENCIA_SEDE_ARBITRAL", codigos)
        self.assertIn("P5_SIN_INSTITUCION_O_REGLAS", codigos)

    def test_06_patologia_alta_clausula_escalonada_sin_plazo(self):
        """Detecta P3 cuando se exige mediación previa sin plazo máximo en días."""
        doc = (
            "CLÁUSULA OCTAVA. Antes de iniciar cualquier arbitraje ante el CAM en CDMX, "
            "las partes deberán agotar un procedimiento de mediación."
        )
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_principal": "escalonada",
                "institucion_administradora": "CAM",
                "sede_arbitraje": "Ciudad de México",
                "idioma": "Español",
                "ley_aplicable_fondo": "Leyes federales de México",
                "numero_arbitros": 3,
                "tiene_etapa_previa_obligatoria": True,
                "plazo_etapa_previa_dias": None,
                "cita_textual_clausula": doc,
            },
            texto_documento=doc,
        )
        self.assertEqual(res["estado"], "PATOLOGIA_DETECTADA")
        self.assertEqual(res["hallazgos"][0]["codigo"], "P3_ESCALAMIENTO_SIN_PLAZO")

    def test_07_observaciones_menores_arbitros_pares_y_omisiones_supletorias(self):
        """Detecta P6 (2 árbitros) y P7 (sin idioma ni ley aplicable) sin marcar patología crítica."""
        doc = (
            "CLÁUSULA DÉCIMA. Las controversias se resolverán por arbitraje de dos árbitros "
            "administrado por la CANACO con sede en Guadalajara, Jalisco."
        )
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_principal": "arbitraje",
                "institucion_administradora": "CANACO",
                "sede_arbitraje": "Guadalajara, Jalisco",
                "numero_arbitros": 2,
                "cita_textual_clausula": doc,
            },
            texto_documento=doc,
        )
        self.assertEqual(res["estado"], "OBSERVACIONES_MENORES")
        codigos = {h["codigo"] for h in res["hallazgos"]}
        self.assertEqual(codigos, {"P6_NUMERO_PAR_ARBITROS", "P7_OMISION_IDIOMA_O_LEY_FONDO"})

    def test_08_clausula_sana_sin_alertas_automaticas(self):
        """Una cláusula completa y bien redactada emite CLAUSULA_SIN_ALERTAS_AUTOMATICAS."""
        doc = (
            "CLÁUSULA DÉCIMA SEGUNDA. Las partes agotarán una negociación directa de 15 días; "
            "de persistir el conflicto, se someterán a arbitraje de derecho ante el CAM, "
            "con sede en Guadalajara, Jalisco, en idioma español, bajo el Código de Comercio "
            "de México, ante tribunal de tres árbitros."
        )
        res = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_principal": "escalonada",
                "institucion_administradora": "CAM",
                "sede_arbitraje": "Guadalajara, Jalisco",
                "idioma": "Español",
                "ley_aplicable_fondo": "Código de Comercio de México",
                "numero_arbitros": 3,
                "tiene_etapa_previa_obligatoria": True,
                "plazo_etapa_previa_dias": 15,
                "somete_tambien_a_tribunales_locales": False,
                "cita_textual_clausula": doc,
            },
            texto_documento=doc,
        )
        self.assertEqual(res["estado"], "CLAUSULA_SIN_ALERTAS_AUTOMATICAS")
        self.assertEqual(res["total_hallazgos"], 0)

    def test_09_pydantic_rechaza_campos_extra_y_falta_de_fuente(self):
        """Rechaza inyección de campos no definidos (extra='forbid') y ausencia de texto fuente."""
        res_extra = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_principal": "arbitraje",
                "cita_textual_clausula": "Cláusula arbitral válida de prueba.",
                "campo_inventado": 123,
            },
            texto_documento="Cláusula arbitral válida de prueba.",
        )
        self.assertEqual(res_extra["estado"], "ERROR_VALIDACION")

        res_sin_doc = auditar_clausula_masc(
            datos_clausula={
                "mecanismo_principal": "arbitraje",
                "cita_textual_clausula": "Cláusula arbitral válida de prueba.",
            }
        )
        self.assertEqual(res_sin_doc["estado"], "FALTAN_DATOS")

    def test_10_simulador_emv_zopa_viable_exacto(self):
        """Verifica aritmética Decimal exacta del caso base con ZOPA viable."""
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
        self.assertTrue(res["negociacion_masc"]["existe_zopa"])
        self.assertEqual(res["negociacion_masc"]["rango_acuerdo_mxn"]["piso_actor"], "445716.17")
        self.assertEqual(res["negociacion_masc"]["rango_acuerdo_mxn"]["techo_demandado"], "667470.48")

    def test_11_simulador_emv_sin_zopa_brecha_expectativas(self):
        """Detecta ausencia de ZOPA cuando el piso del actor supera el techo del demandado."""
        res = simular_escenario_batna_emv({
            "monto_reclamado_mxn": "1000000",
            "probabilidad_exito_actor": "0.90",
            "costos_litigio_actor_mxn": "20000",
            "costos_litigio_demandado_mxn": "20000",
            "duracion_juicio_meses": 12,
            "tasa_descuento_anual": "0.05",
            "costos_masc_actor_mxn": "50000",
            "costos_masc_demandado_mxn": "50000",
        })
        self.assertEqual(res["estado"], "SIN_ZOPA_BRECHA_EXPECTATIVAS")
        self.assertFalse(res["negociacion_masc"]["existe_zopa"])
        self.assertIsNone(res["negociacion_masc"]["rango_acuerdo_mxn"])

    def test_12_simulador_emv_litigio_valor_esperado_negativo(self):
        """Cuando litigar tiene VPN negativo para el actor, el piso racional se acota a 0.00."""
        res = simular_escenario_batna_emv({
            "monto_reclamado_mxn": "100000",
            "probabilidad_exito_actor": "0.20",
            "costos_litigio_actor_mxn": "80000",
            "costos_litigio_demandado_mxn": "80000",
            "duracion_juicio_meses": 24,
            "tasa_descuento_anual": "0.10",
            "costos_masc_actor_mxn": "10000",
            "costos_masc_demandado_mxn": "10000",
            "riesgo_costas_perdedor_mxn": "30000",
        })
        self.assertTrue(res["actor"]["litigio_tiene_valor_esperado_negativo"])
        self.assertEqual(res["actor"]["minimo_racional_aceptable_masc_mxn"], "0.00")

    def test_13_simulador_emv_rechaza_booleanos_y_nan(self):
        """Valida que Pydantic bloquee booleanos y valores no finitos en montos y tasas."""
        res_bool = simular_escenario_batna_emv({
            "monto_reclamado_mxn": True,
            "probabilidad_exito_actor": "0.50",
            "costos_litigio_actor_mxn": "10000",
            "costos_litigio_demandado_mxn": "10000",
            "duracion_juicio_meses": 12,
            "tasa_descuento_anual": "0.10",
            "costos_masc_actor_mxn": "2000",
            "costos_masc_demandado_mxn": "2000",
        })
        self.assertEqual(res_bool["estado"], "ERROR_VALIDACION")

        res_nan = simular_escenario_batna_emv({
            "monto_reclamado_mxn": "100000",
            "probabilidad_exito_actor": "NaN",
            "costos_litigio_actor_mxn": "10000",
            "costos_litigio_demandado_mxn": "10000",
            "duracion_juicio_meses": 12,
            "tasa_descuento_anual": "0.10",
            "costos_masc_actor_mxn": "2000",
            "costos_masc_demandado_mxn": "2000",
        })
        self.assertEqual(res_nan["estado"], "ERROR_VALIDACION")

    def test_14_extraccion_tablas_docx_y_deduplicacion_celdas_combinadas(self):
        """Extrae cláusulas alojadas dentro de tablas .docx sin duplicar celdas combinadas."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ruta = Path(tmpdir) / "caratula_contrato.docx"
            doc = Document()
            doc.add_paragraph("CARÁTULA DE SERVICIOS")
            tabla = doc.add_table(rows=2, cols=2)
            tabla.cell(0, 0).merge(tabla.cell(0, 1))
            tabla.cell(0, 0).text = "CLÁUSULA ARBITRAL EN ANEXO"
            tabla.cell(1, 0).text = "Sede"
            tabla.cell(1, 1).text = "Arbitraje ante CAM en Guadalajara, Jalisco."
            doc.save(ruta)

            res = extraer_documento(str(ruta))
            self.assertEqual(res["estado"], "OK")
            self.assertEqual(res["total_bloques"], 3)
            self.assertEqual(res["texto_completo"].count("CLÁUSULA ARBITRAL EN ANEXO"), 1)
            self.assertIn("Arbitraje ante CAM en Guadalajara, Jalisco.", res["texto_completo"])

    def test_15_auditoria_directa_desde_ruta_docx_con_tabla(self):
        """Valida cita literal extraída desde una celda de tabla en un .docx real."""
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
                    "mecanismo_principal": "arbitraje",
                    "institucion_administradora": "CAM",
                    "sede_arbitraje": "Zapopan",
                    "idioma": "Español",
                    "ley_aplicable_fondo": "Código de Comercio",
                    "numero_arbitros": 1,
                    "cita_textual_clausula": cita,
                },
                ruta_archivo=str(ruta),
            )
            self.assertEqual(res["estado"], "CLAUSULA_SIN_ALERTAS_AUTOMATICAS")
            self.assertTrue(res["cita_verificada_en_fuente"])

    def test_16_matriz_sensibilidad_emv_escenarios_default(self):
        """Evalúa los 4 escenarios por defecto (0.35, 0.50, 0.65, 0.80) y verifica paridad exacta."""
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
        self.assertEqual(res["resumen"]["escenarios_con_zopa"], 4)
        fila_65 = res["matriz_escenarios"][2]
        self.assertEqual(fila_65["probabilidad_exito_actor"], "0.65")
        self.assertEqual(fila_65["piso_actor_masc_mxn"], "445716.17")
        self.assertEqual(fila_65["techo_demandado_masc_mxn"], "667470.48")

    def test_17_matriz_sensibilidad_emv_lista_personalizada_sin_zopa(self):
        """Evalúa una lista personalizada de probabilidades en escenario sin ZOPA."""
        res = simular_matriz_sensibilidad_emv({
            "monto_reclamado_mxn": "500000",
            "probabilidades_exito_actor": ["0.25", "0.75"],
            "costos_litigio_actor_mxn": "5000",
            "costos_litigio_demandado_mxn": "5000",
            "duracion_juicio_meses": 12,
            "tasa_descuento_anual": "0.05",
            "costos_masc_actor_mxn": "40000",
            "costos_masc_demandado_mxn": "40000",
        })
        self.assertEqual(res["estado"], "OK")
        self.assertEqual(res["resumen"]["total_escenarios"], 2)
        self.assertEqual(res["resumen"]["escenarios_sin_zopa"], 2)

    def test_18_matriz_sensibilidad_rechaza_probabilidades_invalidas(self):
        """Rechaza lista vacía o probabilidades fuera de [0, 1] o booleanas."""
        res_vacia = simular_matriz_sensibilidad_emv({
            "monto_reclamado_mxn": "100000",
            "probabilidades_exito_actor": [],
            "costos_litigio_actor_mxn": "10000",
            "costos_litigio_demandado_mxn": "10000",
            "duracion_juicio_meses": 12,
            "tasa_descuento_anual": "0.10",
            "costos_masc_actor_mxn": "2000",
            "costos_masc_demandado_mxn": "2000",
        })
        self.assertEqual(res_vacia["estado"], "ERROR_VALIDACION")

        res_bool = simular_matriz_sensibilidad_emv({
            "monto_reclamado_mxn": "100000",
            "probabilidades_exito_actor": [True],
            "costos_litigio_actor_mxn": "10000",
            "costos_litigio_demandado_mxn": "10000",
            "duracion_juicio_meses": 12,
            "tasa_descuento_anual": "0.10",
            "costos_masc_actor_mxn": "2000",
            "costos_masc_demandado_mxn": "2000",
        })
        self.assertEqual(res_bool["estado"], "ERROR_VALIDACION")

    def test_19_pdf_sin_texto_extraible_fail_closed(self):
        """Verifica que un archivo .pdf corrupto o sin capa de texto falle de forma controlada."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ruta_pdf = Path(tmpdir) / "corrupto.pdf"
            ruta_pdf.write_bytes(b"%PDF-1.4 contenido_invalido")
            res = extraer_documento(str(ruta_pdf))
            self.assertIn(res["estado"], {"ERROR_LECTURA", "PDF_SIN_TEXTO_EXTRAIBLE"})

    def test_20_paridad_esquema_mcp_y_nucleo(self):
        """Verifica que servidor_mcp.py exponga exactamente los mismos parámetros que el núcleo."""
        self.assertEqual(
            list(inspect.signature(extraer_documento).parameters.keys()),
            list(inspect.signature(servidor_mcp.leer_documento_contrato).parameters.keys()),
        )
        self.assertEqual(
            list(inspect.signature(auditar_clausula_masc).parameters.keys()),
            list(inspect.signature(servidor_mcp.auditar_clausula_masc).parameters.keys()),
        )
        self.assertEqual(
            list(inspect.signature(simular_escenario_batna_emv).parameters.keys()),
            list(inspect.signature(servidor_mcp.simular_batna_emv).parameters.keys()),
        )
        self.assertEqual(
            list(inspect.signature(simular_matriz_sensibilidad_emv).parameters.keys()),
            list(inspect.signature(servidor_mcp.simular_matriz_sensibilidad_emv).parameters.keys()),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)