import os
import tempfile
import unittest
from unittest import mock

import cheap_worker_core
from cheap_worker_core import Config, bulk_read
from tests.helpers import BackendFalso

CITA = "return precio * cantidad"


def respuesta(etiqueta):
    """Respuesta del modelo que pasa la verificación contra el archivo de prueba."""
    return f"- {etiqueta}\n  > {CITA}"


class TestCache(unittest.TestCase):
    """Releer el mismo archivo sin tocarlo no debe costar otra inferencia."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cache = os.path.join(self.dir.name, "cache")
        self.archivo = self._write("a.py", "def calcular_total(precio, cantidad):\n    return precio * cantidad\n")

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
        backend = BackendFalso([respuesta("devuelve el total")])
        primera = bulk_read(cfg, "que hace?", [self.archivo], backend=backend)
        self.assertEqual(len(backend.llamadas), 1)

        # El doble se queda sin respuestas: si volviera a llamar, reventaria.
        segunda = bulk_read(cfg, "que hace?", [self.archivo], backend=backend)
        self.assertEqual(segunda, primera)
        self.assertEqual(len(backend.llamadas), 1)

    def test_cambiar_el_archivo_invalida_la_entrada(self):
        cfg = self._cfg()
        bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("viejo")]))
        self._write("a.py", "def calcular_total(precio, cantidad):\n    return precio * cantidad + 1\n")
        nuevo = bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("nuevo")]))
        self.assertIn("- nuevo", nuevo)

    def test_cambiar_la_pregunta_invalida_la_entrada(self):
        cfg = self._cfg()
        bulk_read(cfg, "que hace?", [self.archivo], backend=BackendFalso([respuesta("uno")]))
        otra = bulk_read(cfg, "que importa?", [self.archivo], backend=BackendFalso([respuesta("dos")]))
        self.assertIn("- dos", otra)

    def test_cambiar_de_modelo_invalida_la_entrada(self):
        bulk_read(self._cfg(SHUNT_MODEL_BULK="a:3b"), "q", [self.archivo],
                  backend=BackendFalso([respuesta("del modelo a")]))
        otro = bulk_read(self._cfg(SHUNT_MODEL_BULK="b:7b"), "q", [self.archivo],
                         backend=BackendFalso([respuesta("del modelo b")]))
        self.assertIn("- del modelo b", otro)

    def test_cambiar_el_techo_de_salida_invalida_la_entrada(self):
        bulk_read(self._cfg(SHUNT_MAX_OUTPUT_BULK="512"), "q", [self.archivo],
                  backend=BackendFalso([respuesta("corto")]))
        otro = bulk_read(self._cfg(SHUNT_MAX_OUTPUT_BULK="1024"), "q", [self.archivo],
                         backend=BackendFalso([respuesta("largo")]))
        self.assertIn("- largo", otro)

    def test_cambiar_el_formato_de_respuesta_invalida_la_entrada(self):
        # Los resúmenes guardados antes de verificar citas no deben reutilizarse.
        cfg = self._cfg()
        bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("antiguo")]))
        with mock.patch.object(cheap_worker_core, "FORMATO_RESPUESTA", "otro-formato"):
            nuevo = bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("actual")]))
        self.assertIn("- actual", nuevo)

    def test_cambiar_la_variante_de_prompt_invalida_la_entrada(self):
        cfg = self._cfg()
        bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("antiguo")]))
        original = cheap_worker_core._mensajes_bulk
        delega = lambda pregunta, texto_bloque, variante: original(pregunta, texto_bloque, "pregunta_al_final")
        with mock.patch.object(cheap_worker_core, "VARIANTE_PROMPT", "otra-variante"), \
                mock.patch.object(cheap_worker_core, "_mensajes_bulk", delega):
            nuevo = bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("actual")]))
        self.assertIn("- actual", nuevo)

    def test_con_la_cache_apagada_siempre_se_llama_al_modelo(self):
        cfg = self._cfg(SHUNT_CACHE_MAX="0")
        bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("uno")]))
        segunda = bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("dos")]))
        self.assertIn("- dos", segunda)
        self.assertFalse(os.path.isdir(self.cache))

    def test_la_cache_no_crece_sin_techo(self):
        cfg = self._cfg(SHUNT_CACHE_MAX="3")
        etiquetas = "abcdef"
        for i in range(6):
            bulk_read(cfg, "pregunta %d" % i, [self.archivo], backend=BackendFalso([respuesta("r" + etiquetas[i])]))
        entradas = [n for n in os.listdir(self.cache) if n.endswith(".txt")]
        self.assertLessEqual(len(entradas), 3)

    def test_un_directorio_de_cache_inservible_no_tumba_la_consulta(self):
        # La cache es una optimizacion: si falla, se recalcula y ya.
        cfg = self._cfg(SHUNT_CACHE_DIR=os.path.join(self.archivo, "no-cabe"))
        resultado = bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("vale")]))
        self.assertIn("- vale", resultado)

    def test_una_respuesta_sin_formato_no_se_cachea(self):
        cfg = self._cfg()
        bulk_read(cfg, "q", [self.archivo], backend=BackendFalso(["esto es prosa, sin viñetas ni cita"]))
        segunda = bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("valida")]))
        self.assertIn("- valida", segunda)

    def test_los_archivos_que_faltan_entran_en_la_clave(self):
        cfg = self._cfg()
        fantasma = os.path.join(self.dir.name, "fantasma.py")
        con = bulk_read(cfg, "q", [self.archivo, fantasma], backend=BackendFalso([respuesta("con")]))
        sin = bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("sin")]))
        self.assertIn("fantasma.py", con)
        self.assertIn("- sin", sin)


if __name__ == "__main__":
    unittest.main()
