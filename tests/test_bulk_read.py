import os
import tempfile
import unittest
import zipfile

from cheap_worker_core import BudgetError, Config, SYSTEM_BULK, bulk_read
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


if __name__ == "__main__":
    unittest.main()
