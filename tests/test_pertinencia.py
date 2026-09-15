import unittest

from cheap_worker_pertinencia import cobertura, palabras_clave, respalda, toca_pregunta, valores


class TestPalabrasClave(unittest.TestCase):
    def test_quita_vacias_tildes_y_cortas(self):
        self.assertEqual(
            palabras_clave("La reforma del estatuto requiere una mayoría en cada junta."),
            ["reforma", "estatuto", "requiere", "mayoria", "junta"],
        )

    def test_ignora_numeros_en_digitos(self):
        self.assertEqual(palabras_clave("artículo 81"), ["articulo"])

    def test_ingles(self):
        self.assertEqual(palabras_clave("The committee shall approve the budget"),
                         ["committee", "approve", "budget"])

    def test_pregunta_sin_contenido(self):
        self.assertEqual(palabras_clave("¿Qué?"), [])


class TestCobertura(unittest.TestCase):
    """Casos de la sección 6 del diseño: forma de los fallos reales, texto inventado."""

    def test_cita_que_respalda(self):
        afirmacion = "La reforma del estatuto requiere una mayoría de 3/5 en cada junta."
        cita = "Mayoría 3/5: necesaria para la reforma del estatuto en cada una de las juntas"
        self.assertAlmostEqual(cobertura(afirmacion, cita), 0.8)
        self.assertTrue(respalda(afirmacion, cita))

    def test_cita_de_otro_asunto(self):
        afirmacion = "El recurso se resuelve en un plazo de tres años."
        cita = "A los tres años de su nombramiento, los vocales del consejo se renuevan por sorteo"
        self.assertAlmostEqual(cobertura(afirmacion, cita), 0.4)
        self.assertFalse(respalda(afirmacion, cita))

    def test_afirmacion_con_anadidos_que_la_cita_no_dice(self):
        afirmacion = "Para convocar una asamblea hace falta 1/10 de los socios de cualquiera de las juntas."
        cita = "Mayoría 1/10: la que se necesita para convocar una asamblea por parte de los socios"
        self.assertAlmostEqual(cobertura(afirmacion, cita), 0.5)
        self.assertFalse(respalda(afirmacion, cita))

    def test_raiz_de_cinco_caracteres(self):
        self.assertTrue(respalda("Las Cámaras deliberan", "cada una de las cámara delibera"))

    def test_afirmacion_sin_contenido_no_respalda(self):
        self.assertIsNone(cobertura("Sí, de las", "cualquier cita larga aquí"))
        self.assertFalse(respalda("Sí, de las", "cualquier cita larga aquí"))


class TestTocaPregunta(unittest.TestCase):
    def test_comparte_una_palabra(self):
        self.assertTrue(toca_pregunta("¿Qué mayoría exige la reforma del estatuto?",
                                      "necesaria para la reforma del estatuto"))

    def test_no_comparte_ninguna(self):
        self.assertFalse(toca_pregunta("¿En qué plazo se resuelve un recurso?",
                                       "los vocales del consejo se renuevan por sorteo"))

    def test_pregunta_sin_contenido_no_descarta(self):
        self.assertTrue(toca_pregunta("¿Qué?", "los vocales del consejo se renuevan"))


class TestValores(unittest.TestCase):
    def test_equivalencias(self):
        casos = {
            "doce": {"12"}, "12": {"12"}, "treinta y dos": {"32"}, "tres quintos": {"3/5"},
            "3/5": {"3/5"}, "la mitad": {"1/2"}, "dos tercios": {"2/3"}, "2/3": {"2/3"},
            "quince días": {"15"}, "dos mil quinientos": {"2500"}, "1.500 euros": {"1.500"},
            "veintiún años": {"21"}, "el artículo 81": {"81"},
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(valores(texto), esperado)

    def test_un_articulo_indefinido_no_es_un_numero(self):
        self.assertEqual(valores("en un plazo razonable"), set())

    def test_fraccion_en_letras_equivale_a_digitos(self):
        self.assertTrue(valores("dos tercios de la junta") <= valores("mayoría de 2/3 de la junta"))
        self.assertTrue(valores("mayoría de 2/3") <= valores("dos tercios de sus miembros"))


if __name__ == "__main__":
    unittest.main()
