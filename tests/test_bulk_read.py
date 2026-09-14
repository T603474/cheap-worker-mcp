import os
import tempfile
import unittest

from cheap_worker_core import BudgetError, Config, bulk_read
from tests.helpers import BackendFalso


class TestBulkRead(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cfg = Config.from_env({"SHUNT_MAX_CTX_TOKENS": "4096", "SHUNT_MAX_OUTPUT_BULK": "512",
                                    "SHUNT_RESERVE_EXTRA": "512"})

    def _write(self, name, text):
        path = os.path.join(self.dir.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_un_solo_bloque_hace_una_sola_llamada_y_no_reduce(self):
        path = self._write("a.py", "def f(): pass\n")
        backend = BackendFalso(["- f(): función, línea 1"])
        resultado = bulk_read(self.cfg, "¿Qué define?", [path], backend=backend)
        self.assertEqual(len(backend.llamadas), 1)
        self.assertEqual(resultado, "- f(): función, línea 1")

    def test_la_pregunta_y_el_payload_llegan_al_modelo(self):
        path = self._write("a.py", "def f(): pass\n")
        backend = BackendFalso(["ok"])
        bulk_read(self.cfg, "¿Qué define?", [path], backend=backend)
        user = backend.llamadas[0]["user"]
        self.assertIn("¿Qué define?", user)
        self.assertIn("def f(): pass", user)
        self.assertIn('<file path="', user)

    def test_usa_la_temperatura_bulk(self):
        path = self._write("a.py", "x\n")
        backend = BackendFalso(["ok"])
        bulk_read(self.cfg, "q", [path], backend=backend)
        self.assertEqual(backend.llamadas[0]["temperature"], 0.2)

    def test_varios_bloques_disparan_una_llamada_de_reduce(self):
        cfg = Config.from_env({"SHUNT_MAX_CTX_TOKENS": "600", "SHUNT_MAX_OUTPUT_BULK": "50",
                                "SHUNT_MAX_OUTPUT_CODE": "50", "SHUNT_RESERVE_EXTRA": "50"})
        a = self._write("a.py", "a" * 1600 + "\n")
        b = self._write("b.py", "b" * 1600 + "\n")
        backend = BackendFalso(["parcial A", "parcial B", "fusion"])
        resultado = bulk_read(cfg, "q", [a, b], backend=backend)
        self.assertEqual(len(backend.llamadas), 3)
        self.assertEqual(resultado, "fusion")
        reduce_user = backend.llamadas[-1]["user"]
        self.assertIn("parcial A", reduce_user)
        self.assertIn("parcial B", reduce_user)

    def test_los_archivos_que_faltan_se_anexan_a_la_respuesta(self):
        bueno = self._write("bueno.py", "x\n")
        malo = os.path.join(self.dir.name, "fantasma.py")
        backend = BackendFalso(["analisis"])
        resultado = bulk_read(self.cfg, "q", [bueno, malo], backend=backend)
        self.assertIn("analisis", resultado)
        self.assertIn("fantasma.py", resultado)
        self.assertIn("no encontrados", resultado.lower())

    def test_si_no_hay_ningun_archivo_legible_lanza_budgeterror(self):
        malo = os.path.join(self.dir.name, "fantasma.py")
        backend = BackendFalso([])
        with self.assertRaises(BudgetError):
            bulk_read(self.cfg, "q", [malo], backend=backend)

    def test_si_el_plegado_no_avanza_lanza_budgeterror(self):
        cfg = Config.from_env({"SHUNT_MAX_CTX_TOKENS": "600", "SHUNT_MAX_OUTPUT_BULK": "50",
                                "SHUNT_MAX_OUTPUT_CODE": "50", "SHUNT_RESERVE_EXTRA": "50"})
        a = self._write("a.py", "a" * 1600 + "\n")
        b = self._write("b.py", "b" * 1600 + "\n")
        # Cada parcial ocupa ~1000 tokens, muy por encima del presupuesto de 500,
        # así que ninguna pasada del plegado puede combinarlos.
        backend = BackendFalso(["a" * 4000, "b" * 4000])
        with self.assertRaises(BudgetError):
            bulk_read(cfg, "q", [a, b], backend=backend)
        # Los dos map se hicieron; ningún reduce llegó a lanzarse.
        self.assertEqual(len(backend.llamadas), 2)


if __name__ == "__main__":
    unittest.main()
