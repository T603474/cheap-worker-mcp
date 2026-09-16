import os
import tempfile
import unittest
import zipfile

import cheap_worker_core
from cheap_worker_core import BudgetError, Config, InputError, SYSTEM_BULK, _mensajes_bulk, bulk_read
from tests.helpers import BackendFalso


class TestBulkRead(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cfg = Config.from_env({"SHUNT_MAX_CTX_TOKENS": "4096", "SHUNT_MAX_OUTPUT_BULK": "512",
                                    "SHUNT_RESERVE_EXTRA": "512", "SHUNT_CACHE_MAX": "0"})

    def _write(self, name, text):
        path = os.path.join(self.dir.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_una_afirmacion_verificada_llega_con_cita_y_ubicacion(self):
        path = self._write("a.md", "# Título\nLas leyes orgánicas requieren mayoría absoluta del Congreso.\n")
        backend = BackendFalso(["- Requieren mayoría absoluta\n  > requieren mayoría absoluta del Congreso"])
        resultado = bulk_read(self.cfg, "¿Qué mayoría?", [path], backend=backend)
        self.assertEqual(len(backend.llamadas), 1)
        self.assertEqual(resultado, (
            "- Requieren mayoría absoluta\n"
            "  > requieren mayoría absoluta del Congreso\n"
            f"  ({path}:línea 2)"
        ))

    def test_una_afirmacion_inventada_no_llega_a_la_respuesta(self):
        path = self._write("ficha.md", "| Semana | Horas |\n|---|---|\n| | |\n")
        backend = BackendFalso(["- La semana 1 son 4 horas\n  > semana 1 dedica 4 horas"])
        resultado = bulk_read(self.cfg, "¿Qué dice la tabla?", [path], backend=backend)
        self.assertNotIn("4 horas", resultado)
        self.assertTrue(resultado.startswith("No consta en los documentos."))
        self.assertIn("Descartada 1 afirmación", resultado)

    def test_respuesta_en_prosa_se_descarta_como_sin_formato_no_como_no_consta(self):
        path = self._write("a.md", "Las leyes orgánicas requieren mayoría absoluta del Congreso.\n")
        backend = BackendFalso(["Las leyes orgánicas requieren mayoría absoluta del Congreso, según el texto."])
        resultado = bulk_read(self.cfg, "¿Qué mayoría?", [path], backend=backend)
        self.assertEqual(resultado, (
            "No consta en los documentos.\n\n"
            "1 respuesta sin el formato pedido."
        ))

    def test_no_consta_del_modelo_llega_como_no_consta(self):
        path = self._write("a.md", "texto sin relación\n")
        resultado = bulk_read(self.cfg, "¿Plazo?", [path], backend=BackendFalso(["NO CONSTA"]))
        self.assertEqual(resultado, "No consta en los documentos.")

    def test_la_pregunta_y_el_payload_llegan_al_modelo(self):
        path = self._write("a.py", "def f(): pass\n")
        backend = BackendFalso(["NO CONSTA"])
        bulk_read(self.cfg, "¿Qué define?", [path], backend=backend)
        user = backend.llamadas[0]["user"]
        self.assertIn("¿Qué define?", user)
        self.assertIn("def f(): pass", user)
        self.assertIn('<file path="', user)

    def test_el_prompt_pide_citas_y_no_consta_y_no_supone_codigo(self):
        self.assertIn("NO CONSTA", SYSTEM_BULK)
        self.assertIn(">", SYSTEM_BULK)
        self.assertNotIn("code analyst", SYSTEM_BULK)

    def test_usa_la_temperatura_bulk(self):
        path = self._write("a.py", "x\n")
        backend = BackendFalso(["NO CONSTA"])
        bulk_read(self.cfg, "q", [path], backend=backend)
        self.assertEqual(backend.llamadas[0]["temperature"], 0.2)

    def test_varios_bloques_se_juntan_sin_llamar_al_modelo_para_fusionar(self):
        cfg = Config.from_env({"SHUNT_MAX_CTX_TOKENS": "600", "SHUNT_MAX_OUTPUT_BULK": "50",
                                "SHUNT_MAX_OUTPUT_CODE": "50", "SHUNT_RESERVE_EXTRA": "50",
                                "SHUNT_CACHE_MAX": "0"})
        relleno = "alfa beta gama delta\n" * 80
        a = self._write("a.md", "el primer archivo dice esto\n" + relleno)
        b = self._write("b.md", "el segundo archivo dice aquello\n" + relleno)
        backend = BackendFalso([
            "- Lo del primero\n  > el primer archivo dice esto",
            "- Lo del segundo\n  > el segundo archivo dice aquello",
        ])
        resultado = bulk_read(cfg, "q", [a, b], backend=backend)
        self.assertEqual(len(backend.llamadas), 2)
        self.assertLess(resultado.index("Lo del primero"), resultado.index("Lo del segundo"))

    def test_los_archivos_no_leidos_se_anexan_con_su_motivo(self):
        bueno = self._write("bueno.md", "contenido del archivo bueno\n")
        malo = os.path.join(self.dir.name, "fantasma.md")
        backend = BackendFalso(["- Bueno\n  > contenido del archivo bueno"])
        resultado = bulk_read(self.cfg, "q", [bueno, malo], backend=backend)
        self.assertIn("- Bueno", resultado)
        self.assertIn(f"Archivos no leídos:\n- {malo}: no existe", resultado)

    def test_si_no_hay_ningun_archivo_legible_lanza_budgeterror_con_motivo(self):
        malo = os.path.join(self.dir.name, "fantasma.py")
        with self.assertRaisesRegex(BudgetError, "no existe"):
            bulk_read(self.cfg, "q", [malo], backend=BackendFalso([]))

    def test_una_cita_ajena_a_la_pregunta_se_descarta(self):
        path = self._write("a.md", "Los vocales del consejo se renuevan por sorteo cada tres años\n")
        backend = BackendFalso([
            "- Los vocales del consejo se renuevan por sorteo cada tres años\n"
            "  > Los vocales del consejo se renuevan por sorteo cada tres años"
        ])
        resultado = bulk_read(self.cfg, "¿Qué mayoría exige la reforma del estatuto?", [path], backend=backend)
        self.assertTrue(resultado.startswith("No consta en los documentos."))
        self.assertIn("1 con cita ajena a la pregunta", resultado)

    def test_una_pregunta_que_no_deja_presupuesto_lanza_inputerror(self):
        # Con la config por defecto (ventana 4096, salida bulk 512, margen 512)
        # el presupuesto de archivos es 3072 tokens. Una pregunta de 5000
        # palabras ronda los 10000 tokens: no cabe ni ella sola.
        path = self._write("a.md", "contenido irrelevante\n")
        cfg = Config.from_env({"SHUNT_CACHE_MAX": "0"})
        pregunta_larga = "¿" + "palabra " * 5000 + "?"
        backend = BackendFalso([])
        with self.assertRaises(InputError) as ctx:
            bulk_read(cfg, pregunta_larga, [path], backend=backend)
        mensaje = str(ctx.exception)
        self.assertIn("pregunta", mensaje)
        self.assertIn("SHUNT_MAX_CTX_TOKENS", mensaje)
        self.assertEqual(backend.llamadas, [])

    def test_una_pregunta_larga_deja_menos_presupuesto_y_trocea_mas(self):
        # Ventana pequeña para que el efecto de la pregunta sobre el
        # presupuesto sea visible con archivos cortos. presupuesto bulk =
        # 1024 - 128 (salida) - 512 (margen) = 384 tokens.
        cfg = Config.from_env({"SHUNT_MAX_CTX_TOKENS": "1024", "SHUNT_MAX_OUTPUT_BULK": "128",
                                "SHUNT_MAX_OUTPUT_CODE": "128", "SHUNT_RESERVE_EXTRA": "512",
                                "SHUNT_CACHE_MAX": "0"})
        # El archivo envuelto en <file> pesa 309 tokens: cabe en 384 - 1 = 383
        # (con la pregunta corta, 1 token) pero no en 384 - 200 = 184 (con la
        # pregunta larga, 200 tokens), así que con la pregunta larga tiene que
        # trocearse en más de un bloque. Necesita varias líneas: una sola
        # línea que desborda el presupuesto se manda entera igualmente (caso
        # patológico de _split_lines_to_budget), sin producir más llamadas.
        linea = "el archivo dice esto y aquello sin mucha relevancia real."
        contenido = (linea + "\n") * 20
        path = self._write("a.md", contenido)
        pregunta_corta = "q"
        pregunta_larga = "palabra " * 100

        backend_corta = BackendFalso(["NO CONSTA"])
        bulk_read(cfg, pregunta_corta, [path], backend=backend_corta)

        backend_larga = BackendFalso(["NO CONSTA", "NO CONSTA", "NO CONSTA"])
        bulk_read(cfg, pregunta_larga, [path], backend=backend_larga)

        self.assertEqual(len(backend_corta.llamadas), 1)
        self.assertGreater(len(backend_larga.llamadas), len(backend_corta.llamadas))

    def test_lee_un_docx_y_ubica_por_parrafo(self):
        ruta = os.path.join(self.dir.name, "a.docx")
        xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               '<w:body><w:p><w:r><w:t>El plazo será de treinta días naturales</w:t></w:r></w:p>'
               '</w:body></w:document>')
        with zipfile.ZipFile(ruta, "w") as z:
            z.writestr("word/document.xml", xml.encode("utf-8"))
        backend = BackendFalso(["- Treinta días\n  > plazo será de treinta días"])
        resultado = bulk_read(self.cfg, "¿Plazo?", [ruta], backend=backend)
        self.assertIn(f"({ruta}:párrafo 1)", resultado)


class TestMensajesBulk(unittest.TestCase):
    PREGUNTA = "¿Cuántas veces se reúne el consejo?"
    TEXTO = '<file path="a.md">\nEl consejo se reúne tres veces.\n</file>\n'

    def test_la_pregunta_va_despues_del_documento(self):
        _, usuario = _mensajes_bulk(self.PREGUNTA, self.TEXTO, "pregunta_al_final")
        self.assertGreater(usuario.index(self.PREGUNTA), usuario.index(self.TEXTO))
        self.assertTrue(usuario.rstrip().endswith("Answer:"))

    def test_las_variantes_nuevas_llevan_ejemplo_y_no_consta(self):
        sistema, _ = _mensajes_bulk(self.PREGUNTA, self.TEXTO, "pregunta_al_final")
        self.assertIn("Example", sistema)
        self.assertIn("NO CONSTA", sistema)
        self.assertIn("La junta se reúne dos veces al año", sistema)

    def test_variante_desconocida(self):
        with self.assertRaises(ValueError):
            _mensajes_bulk(self.PREGUNTA, self.TEXTO, "actual")

    def test_el_ejemplo_usa_el_mismo_envoltorio_que_los_trozos_reales(self):
        sistema, _ = _mensajes_bulk(self.PREGUNTA, self.TEXTO, "pregunta_al_final")
        self.assertIn('<file path="ejemplo.md">', sistema)

    def test_no_hay_linea_en_blanco_entre_el_documento_y_la_pregunta(self):
        _, usuario = _mensajes_bulk(self.PREGUNTA, self.TEXTO, "pregunta_al_final")
        self.assertEqual(usuario.count("</file>\n\nQuestion:"), 1)
        self.assertNotIn("</file>\n\n\nQuestion:", usuario)

    def test_el_recordatorio_de_pregunta_al_final_describe_la_cita_indentada(self):
        _, usuario = _mensajes_bulk(self.PREGUNTA, self.TEXTO, "pregunta_al_final")
        self.assertIn("  > ", usuario)

    def test_bulk_read_usa_la_variante_activa(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "a.md")
            with open(ruta, "w", encoding="utf-8") as f:
                f.write("texto sin relación\n")
            cfg = Config.from_env({"SHUNT_CACHE_MAX": "0"})
            backend = BackendFalso(["NO CONSTA"])
            bulk_read(cfg, "¿Plazo?", [ruta], backend=backend)
            self.assertIs(backend.llamadas[0]["system"], cheap_worker_core.SYSTEM_BULK)
            self.assertTrue(backend.llamadas[0]["user"].rstrip().endswith("Answer:"))


if __name__ == "__main__":
    unittest.main()
