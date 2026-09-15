import queue
import threading
import unittest

from cheap_worker_pertinencia import cobertura, palabras_clave, respalda, toca_pregunta, valores


def _valores_con_limite(texto, segundos=2):
    """Ejecuta valores(texto) con límite de tiempo; falla por timeout en vez de colgarse.

    Usa un hilo daemon (no un ThreadPoolExecutor): sus hilos no son daemon y el
    intérprete los espera al salir con atexit, así que un hilo colgado bloquearía
    igualmente el fin del proceso aunque el test ya haya fallado. Si el hilo lanza
    una excepción, se relanza aquí en vez de reportarse como bucle infinito.
    """
    cola = queue.Queue(maxsize=1)

    def _ejecutar():
        try:
            cola.put((True, valores(texto)))
        except Exception as excepcion:
            cola.put((False, excepcion))

    hilo = threading.Thread(target=_ejecutar, daemon=True)
    hilo.start()
    try:
        ok, valor_o_excepcion = cola.get(timeout=segundos)
    except queue.Empty:
        raise AssertionError(f"valores({texto!r}) no terminó en {segundos}s (bucle infinito)")
    if not ok:
        raise valor_o_excepcion
    return valor_o_excepcion


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
        self.assertAlmostEqual(cobertura(afirmacion, cita), 0.25)
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
            "quince días": {"15"}, "dos mil quinientos": {"2500"}, "1.500 euros": {"1500"},
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

    def test_rangos_no_se_suman(self):
        """Números separados por puntuación no se suman."""
        self.assertEqual(valores("entre tres y cinco años"), {"3", "5"})
        self.assertEqual(valores("artículos diez, once y doce"), {"10", "11", "12"})
        self.assertEqual(valores("los días quince y dieciséis"), {"15", "16"})
        self.assertEqual(valores("de los cinco, un miembro"), {"5"})

    def test_numeros_compuestos(self):
        """Números compuestos con operaciones internas."""
        self.assertEqual(valores("ciento veinte"), {"120"})
        self.assertEqual(valores("doscientos treinta y cuatro"), {"234"})
        self.assertEqual(valores("treinta y un días"), {"31"})
        self.assertEqual(valores("cien mil"), {"100000"})

    def test_mil_al_inicio(self):
        """'mil' puede ser la primera palabra de una frase numérica."""
        self.assertEqual(valores("mil novecientos setenta y ocho"), {"1978"})
        self.assertEqual(valores("mil euros"), {"1000"})
        self.assertEqual(valores("mil quinientos"), {"1500"})

    def test_fechas_no_son_fracciones(self):
        """Fechas en formato numérico no se interpretan como fracciones."""
        resultado = valores("el 15/09/2026")
        self.assertNotIn("15/9", resultado)
        self.assertIn("2026", resultado)

    def test_ley_no_es_fraccion(self):
        """Referencias normativas con / no se interpretan como fracciones reducidas."""
        self.assertEqual(valores("Ley 39/2015"), {"39/2015"})

    def test_y_sin_unidad_no_causa_bucle_infinito(self):
        """'y' que no encabeza una unidad 1-9 termina la frase numérica, no cuelga."""
        self.assertEqual(
            _valores_con_limite("entre treinta y cuarenta días"), {"30", "40"}
        )
        self.assertEqual(_valores_con_limite("treinta y plazo"), {"30"})
        self.assertEqual(
            _valores_con_limite("los sesenta y los noventa días"), {"60", "90"}
        )

    def test_fraccion_seguida_de_punto_final(self):
        """Un punto de fin de frase tras la fracción no la descarta."""
        self.assertEqual(valores("mayoría de 3/5."), {"3/5"})
        self.assertEqual(valores("una mayoría de 3/5, y"), {"3/5"})

    def test_centenas_no_se_encadenan(self):
        """Tras una palabra de CENTENAS no se acepta otra CENTENAS."""
        self.assertEqual(valores("cien doscientos"), {"100", "200"})
        self.assertEqual(valores("doscientos mil trescientos"), {"200300"})

    def test_mil_no_se_repite(self):
        """'mil' no puede seguir directamente a otro 'mil'."""
        self.assertEqual(valores("mil mil"), {"1000"})

    def test_separador_de_miles_se_canonicaliza(self):
        """Un punto de millar se quita: "2.500" y "1500" valen lo mismo."""
        self.assertEqual(valores("2.500 euros"), {"2500"})
        self.assertTrue(valores("dos mil quinientos euros") <= valores("2.500 euros"))
        self.assertEqual(valores("1500"), valores("1.500"))

    def test_decimal_con_coma_no_se_toca(self):
        self.assertEqual(valores("15,5"), {"15,5"})

    def test_punto_que_no_es_separador_de_miles_no_se_toca(self):
        """"81.2" no es un millar (el grupo tras el punto no tiene 3 dígitos)."""
        self.assertIn("81.2", valores("artículo 81.2"))

    def test_un_una_tras_centenas(self):
        self.assertEqual(valores("ciento un diputados"), {"101"})

    def test_un_una_tras_mil(self):
        self.assertEqual(valores("mil una noches"), {"1001"})

    def test_y_tras_mil_no_es_decena(self):
        """La "y" tras "mil" no forma parte de la gramática de decenas."""
        self.assertEqual(valores("mil y una noches"), {"1000"})

    def test_treinta_y_un_sigue_funcionando(self):
        self.assertEqual(valores("treinta y un días"), {"31"})

    def test_un_articulo_sigue_sin_ser_numero(self):
        self.assertEqual(valores("un plazo"), set())

    def test_por_ciento_no_es_cien(self):
        self.assertEqual(valores("tres por ciento"), {"3"})
        self.assertEqual(valores("3 %"), {"3"})
        self.assertEqual(valores("el 21 por ciento"), {"21"})

    def test_ciento_veinte_sigue_dando_120(self):
        self.assertEqual(valores("ciento veinte"), {"120"})


class TestPalabrasClaveSinNumeros(unittest.TestCase):
    """Los números en letras no cuentan como palabras clave."""

    def test_excluye_numeros_en_letras(self):
        self.assertEqual(
            palabras_clave("dicho plazo será de ocho días"),
            ["dicho", "plazo", "dias"]
        )

    def test_respalda_con_numeros(self):
        """Números tienen su propio filtro; las palabras se comparan por contenido."""
        self.assertTrue(respalda("ocho días de plazo", "un plazo de 8 días"))

    def test_excluye_denominadores_de_fraccion(self):
        """"tres quintos" no deja "quintos" como palabra clave (igual que "tres")."""
        self.assertEqual(palabras_clave("tres quintos de la junta"), ["junta"])

    def test_respalda_con_fraccion_en_letras(self):
        self.assertTrue(respalda(
            "Se requiere una mayoría de tres quintos",
            "se requiere una mayoría de 3/5 de cada una de las juntas",
        ))


if __name__ == "__main__":
    unittest.main()
