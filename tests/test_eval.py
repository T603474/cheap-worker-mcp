import importlib.util
import os
import unittest

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


if __name__ == "__main__":
    unittest.main()
