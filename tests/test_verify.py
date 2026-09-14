import unittest
from collections import Counter

from cheap_worker_verify import (
    Afirmacion, Tramo, Verificada, analizar_respuesta, cifras, componer, normalizar, verificar,
)


def tramo(lineas, ruta="doc.md", orden=0, primera=0):
    ubicaciones = [f"línea {primera + i + 1}" for i in range(len(lineas))]
    return Tramo(ruta, orden, list(lineas), ubicaciones, primera)


class TestAnalizarRespuesta(unittest.TestCase):
    def test_vineta_con_su_cita(self):
        texto = "- Requieren mayoría absoluta\n  > requerirá mayoría absoluta del Congreso"
        self.assertEqual(
            analizar_respuesta(texto),
            [Afirmacion("Requieren mayoría absoluta", "requerirá mayoría absoluta del Congreso")],
        )

    def test_cita_en_varias_lineas_se_une(self):
        texto = "- Hecho\n  > primera parte\n  > segunda parte"
        self.assertEqual(analizar_respuesta(texto)[0].cita, "primera parte segunda parte")

    def test_no_consta_no_produce_afirmaciones(self):
        self.assertEqual(analizar_respuesta("NO CONSTA"), [])

    def test_vineta_sin_cita_queda_con_cita_vacia(self):
        self.assertEqual(analizar_respuesta("- Algo sin prueba"), [Afirmacion("Algo sin prueba", "")])

    def test_admite_asteriscos_y_listas_numeradas(self):
        texto = "* uno\n  > cita uno aquí\n2. dos\n  > cita dos aquí"
        self.assertEqual([a.texto for a in analizar_respuesta(texto)], ["uno", "dos"])

    def test_ignora_el_texto_antes_de_la_primera_vineta(self):
        texto = "Aquí tienes la respuesta:\n- hecho\n  > cita del hecho"
        self.assertEqual(len(analizar_respuesta(texto)), 1)

    def test_una_linea_de_continuacion_se_suma_a_la_afirmacion(self):
        texto = "- hecho que sigue\n  en otra línea\n  > la cita del hecho"
        self.assertEqual(analizar_respuesta(texto)[0].texto, "hecho que sigue en otra línea")


class TestNormalizarYCifras(unittest.TestCase):
    def test_normalizar_quita_marcas_mayusculas_y_espacios(self):
        self.assertEqual(normalizar("  **Hola**   `Mundo`\n «ahí» "), "hola mundo ahí")

    def test_cifras_con_separadores(self):
        self.assertEqual(cifras("30 días y 1.500 euros, art. 81."), {"30", "1.500", "81"})


class TestVerificar(unittest.TestCase):
    LINEAS = [
        "# Título",
        "**Artículo 81.** Son leyes orgánicas las relativas al desarrollo",
        "de los derechos fundamentales. Su aprobación requerirá mayoría absoluta",
        "del Congreso, en una votación final sobre el conjunto del proyecto.",
        "El plazo será de treinta días.",
    ]

    def verificar_una(self, texto, cita, tramos=None):
        return verificar([Afirmacion(texto, cita)], tramos or [tramo(self.LINEAS)])

    def test_cita_real_se_conserva_con_su_ubicacion(self):
        verificadas, descartes = self.verificar_una("Leyes orgánicas", "son leyes orgánicas las relativas")
        self.assertEqual(descartes, Counter())
        self.assertEqual(verificadas[0].ubicacion, "línea 2")
        self.assertEqual(verificadas[0].ruta, "doc.md")

    def test_cita_inventada_se_descarta(self):
        verificadas, descartes = self.verificar_una("Tabla", "la progresión es de 4 horas diarias")
        self.assertEqual(verificadas, [])
        self.assertEqual(descartes, Counter({"cita_no_encontrada": 1}))

    def test_cita_que_cruza_lineas_se_encuentra_y_marca_donde_empieza(self):
        verificadas, _ = self.verificar_una("Mayoría absoluta", "requerirá mayoría absoluta del Congreso")
        self.assertEqual(verificadas[0].ubicacion, "línea 3")

    def test_marcas_de_markdown_y_mayusculas_no_impiden_encontrarla(self):
        verificadas, _ = self.verificar_una("Artículo 81", "ARTÍCULO 81. Son leyes orgánicas")
        self.assertEqual(len(verificadas), 1)

    def test_puntos_suspensivos_en_los_extremos_se_ignoran(self):
        verificadas, _ = self.verificar_una("Leyes", "...son leyes orgánicas las relativas…")
        self.assertEqual(len(verificadas), 1)

    def test_cifra_inventada_sobre_cita_real_se_descarta(self):
        verificadas, descartes = self.verificar_una("El plazo es de 45 días", "El plazo será de treinta días")
        self.assertEqual(verificadas, [])
        self.assertEqual(descartes, Counter({"cifras_no_respaldadas": 1}))

    def test_cifra_presente_en_la_cita_se_acepta(self):
        verificadas, _ = self.verificar_una("Es el artículo 81", "Artículo 81. Son leyes orgánicas")
        self.assertEqual(len(verificadas), 1)

    def test_cita_corta_se_descarta(self):
        _, descartes = self.verificar_una("Leyes", "leyes orgánicas")
        self.assertEqual(descartes, Counter({"cita_corta": 1}))

    def test_sin_cita_se_descarta(self):
        _, descartes = self.verificar_una("Algo", "")
        self.assertEqual(descartes, Counter({"sin_cita": 1}))

    def test_una_vineta_no_consta_se_ignora_sin_contar(self):
        verificadas, descartes = self.verificar_una("NO CONSTA", "")
        self.assertEqual((verificadas, descartes), ([], Counter()))

    def test_busca_en_todos_los_tramos_del_bloque(self):
        tramos = [tramo(["nada que ver aquí"], ruta="a.md"),
                  tramo(["x", "la cita correcta está aquí"], ruta="b.md", orden=1)]
        verificadas, _ = self.verificar_una("Hecho", "la cita correcta está", tramos)
        self.assertEqual((verificadas[0].ruta, verificadas[0].ubicacion), ("b.md", "línea 2"))
        self.assertEqual(verificadas[0].posicion, (1, 1))


class TestComponer(unittest.TestCase):
    def v(self, texto, cita, ubicacion="línea 3", ruta="doc.md", posicion=(0, 2)):
        return Verificada(texto, cita, ruta, ubicacion, posicion)

    def test_formato_de_una_afirmacion(self):
        resultado = componer([self.v("Hecho", "cita literal aquí")], Counter(), [])
        self.assertEqual(resultado, "- Hecho\n  > cita literal aquí\n  (doc.md:línea 3)")

    def test_ordena_por_posicion_y_quita_duplicados(self):
        verificadas = [
            self.v("Segundo", "cita dos aquí", "línea 9", posicion=(0, 8)),
            self.v("Primero", "cita uno aquí", "línea 1", posicion=(0, 0)),
            self.v("Repetido", "CITA uno aquí", "línea 1", posicion=(0, 0)),
        ]
        resultado = componer(verificadas, Counter(), [])
        self.assertEqual([l for l in resultado.splitlines() if l.startswith("- ")], ["- Primero", "- Segundo"])

    def test_sin_verificadas_dice_no_consta(self):
        self.assertEqual(componer([], Counter(), []), "No consta en los documentos.")

    def test_resume_los_descartes_en_orden_fijo(self):
        resultado = componer([], Counter({"cifras_no_respaldadas": 1, "cita_no_encontrada": 2}), [])
        self.assertEqual(resultado, (
            "No consta en los documentos.\n\n"
            "Descartadas 3 afirmaciones: 2 con cita no encontrada en el documento, "
            "1 con cifras que no están en su cita."
        ))

    def test_descarte_en_singular(self):
        resultado = componer([], Counter({"sin_cita": 1}), [])
        self.assertTrue(resultado.endswith("Descartada 1 afirmación: 1 sin cita."))

    def test_lista_los_archivos_no_leidos(self):
        resultado = componer([self.v("Hecho", "cita literal aquí")], Counter(),
                             [("escaneo.pdf", "no tiene texto extraíble; ¿es un escaneo?")])
        self.assertTrue(resultado.endswith(
            "Archivos no leídos:\n- escaneo.pdf: no tiene texto extraíble; ¿es un escaneo?"
        ))


if __name__ == "__main__":
    unittest.main()
