import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cargar_eval():
    spec = importlib.util.spec_from_file_location("eval_bulk_read", os.path.join(RAIZ, "eval-bulk-read.py"))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class TestAcierta(unittest.TestCase):
    def setUp(self):
        self.ev = cargar_eval()

    def test_el_dato_en_la_afirmacion_pero_no_en_la_cita_no_acierta(self):
        resultado = ("- El consejo tiene doce vocales\n"
                     "  > Las juntas podrán delegar en la comisión\n"
                     "  (a.md:línea 3)")
        caso = {"tipo": "dato", "esperado": ["doce vocales"]}
        self.assertFalse(self.ev.acierta(caso, resultado))

    def test_el_dato_en_la_cita_acierta(self):
        resultado = ("- El consejo tiene doce vocales\n"
                     "  > El consejo se compone de doce vocales\n"
                     "  (a.md:línea 3)")
        caso = {"tipo": "dato", "esperado": ["doce vocales"]}
        self.assertTrue(self.ev.acierta(caso, resultado))

    def test_la_cita_despues_de_los_descartes_no_cuenta(self):
        resultado = "No consta en los documentos.\n\nDescartada 1 afirmación: 1 sin cita.\n  > doce vocales"
        self.assertFalse(self.ev.acierta({"tipo": "dato", "esperado": ["doce vocales"]}, resultado))

    def test_sin_respuesta_no_cambia(self):
        self.assertTrue(self.ev.acierta({"tipo": "sin_respuesta", "esperado": []},
                                        "No consta en los documentos."))


class TestVariantes(unittest.TestCase):
    def setUp(self):
        self.ev = cargar_eval()

    def test_evaluar_fija_la_variante_durante_la_llamada(self):
        vistas = []

        def falso(cfg, pregunta, rutas, backend=None):
            vistas.append(self.ev.core.VARIANTE_PROMPT)
            return "No consta en los documentos."

        caso = {"archivo": "x.md", "pregunta": "¿?", "tipo": "sin_respuesta", "esperado": []}
        with mock.patch.object(self.ev.core, "bulk_read", falso), \
                mock.patch.object(self.ev.core, "VARIANTE_PROMPT", "pregunta_al_final"):
            filas = self.ev.evaluar("modelo:3b", [caso], None, "otra")
        self.assertEqual(vistas, ["otra"])
        self.assertTrue(filas[0]["ok"])

    def test_main_recorre_modelos_por_variantes(self):
        llamadas = []

        def falso(modelo, casos, salida, variante):
            llamadas.append((modelo, variante))
            return []

        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "p.json")
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump([], f)
            with mock.patch.object(self.ev, "evaluar", falso), \
                    mock.patch.object(self.ev.core, "VARIANTES_PROMPT", ("v1", "v2")):
                self.ev.main([ruta, "--modelos", "a,b", "--variantes", "v1,v2"])
        self.assertEqual(llamadas, [("a", "v1"), ("a", "v2"),
                                    ("b", "v1"), ("b", "v2")])

    def test_variante_desconocida_se_rechaza(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "p.json")
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump([], f)
            with self.assertRaises(SystemExit):
                self.ev.main([ruta, "--modelos", "a", "--variantes", "inventada"])


if __name__ == "__main__":
    unittest.main()
