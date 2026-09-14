import os
import tempfile
import unittest

from cheap_worker_core import Config, bulk_read
from tests.helpers import BackendFalso


class TestCache(unittest.TestCase):
    """Releer el mismo archivo sin tocarlo no debe costar otra inferencia."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cache = os.path.join(self.dir.name, "cache")
        self.archivo = self._write("a.py", "def f():\n    return 1\n")

    def _cfg(self, **extra):
        env = {"SHUNT_CACHE_DIR": self.cache}
        env.update(extra)
        return Config.from_env(env)

    def _write(self, name, text):
        ruta = os.path.join(self.dir.name, name)
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(text)
        return ruta

    def test_la_segunda_lectura_no_llama_al_modelo(self):
        cfg = self._cfg()
        backend = BackendFalso(["- f(): devuelve 1"])
        primera = bulk_read(cfg, "que hace?", [self.archivo], backend=backend)
        self.assertEqual(len(backend.llamadas), 1)

        # El doble se queda sin respuestas: si volviera a llamar, reventaria.
        segunda = bulk_read(cfg, "que hace?", [self.archivo], backend=backend)
        self.assertEqual(segunda, primera)
        self.assertEqual(len(backend.llamadas), 1)

    def test_cambiar_el_archivo_invalida_la_entrada(self):
        cfg = self._cfg()
        bulk_read(cfg, "q", [self.archivo], backend=BackendFalso(["viejo"]))
        self._write("a.py", "def f():\n    return 2\n")
        nuevo = bulk_read(cfg, "q", [self.archivo], backend=BackendFalso(["nuevo"]))
        self.assertEqual(nuevo, "nuevo")

    def test_cambiar_la_pregunta_invalida_la_entrada(self):
        cfg = self._cfg()
        bulk_read(cfg, "que hace?", [self.archivo], backend=BackendFalso(["uno"]))
        otra = bulk_read(cfg, "que importa?", [self.archivo], backend=BackendFalso(["dos"]))
        self.assertEqual(otra, "dos")

    def test_cambiar_de_modelo_invalida_la_entrada(self):
        bulk_read(self._cfg(SHUNT_MODEL_BULK="a:3b"), "q", [self.archivo],
                  backend=BackendFalso(["del 3b"]))
        otro = bulk_read(self._cfg(SHUNT_MODEL_BULK="b:7b"), "q", [self.archivo],
                         backend=BackendFalso(["del 7b"]))
        self.assertEqual(otro, "del 7b")

    def test_cambiar_el_techo_de_salida_invalida_la_entrada(self):
        bulk_read(self._cfg(SHUNT_MAX_OUTPUT_BULK="512"), "q", [self.archivo],
                  backend=BackendFalso(["corto"]))
        otro = bulk_read(self._cfg(SHUNT_MAX_OUTPUT_BULK="1024"), "q", [self.archivo],
                         backend=BackendFalso(["largo"]))
        self.assertEqual(otro, "largo")

    def test_con_la_cache_apagada_siempre_se_llama_al_modelo(self):
        cfg = self._cfg(SHUNT_CACHE_MAX="0")
        bulk_read(cfg, "q", [self.archivo], backend=BackendFalso(["uno"]))
        segunda = bulk_read(cfg, "q", [self.archivo], backend=BackendFalso(["dos"]))
        self.assertEqual(segunda, "dos")
        self.assertFalse(os.path.isdir(self.cache))

    def test_la_cache_no_crece_sin_techo(self):
        cfg = self._cfg(SHUNT_CACHE_MAX="3")
        for i in range(6):
            bulk_read(cfg, "pregunta %d" % i, [self.archivo], backend=BackendFalso(["r%d" % i]))
        entradas = [n for n in os.listdir(self.cache) if n.endswith(".txt")]
        self.assertLessEqual(len(entradas), 3)

    def test_un_directorio_de_cache_inservible_no_tumba_la_consulta(self):
        # La cache es una optimizacion: si falla, se recalcula y ya.
        cfg = self._cfg(SHUNT_CACHE_DIR=os.path.join(self.archivo, "no-cabe"))
        resultado = bulk_read(cfg, "q", [self.archivo], backend=BackendFalso(["vale"]))
        self.assertEqual(resultado, "vale")

    def test_los_archivos_que_faltan_entran_en_la_clave(self):
        cfg = self._cfg()
        fantasma = os.path.join(self.dir.name, "fantasma.py")
        con = bulk_read(cfg, "q", [self.archivo, fantasma], backend=BackendFalso(["con"]))
        sin = bulk_read(cfg, "q", [self.archivo], backend=BackendFalso(["sin"]))
        self.assertIn("fantasma.py", con)
        self.assertEqual(sin, "sin")


if __name__ == "__main__":
    unittest.main()
