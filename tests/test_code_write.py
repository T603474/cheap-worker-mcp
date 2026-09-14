import os
import tempfile
import unittest

from cheap_worker_core import BudgetError, Config, InputError, code_write
from tests.helpers import BackendFalso


class TestCodeWrite(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cfg = Config.from_env({})
        self.ref = self._write("ref.py", "def ejemplo():\n    return 1\n")

    def _write(self, name, text):
        path = os.path.join(self.dir.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_sin_target_devuelve_el_codigo(self):
        backend = BackendFalso(["def nuevo():\n    return 2\n"])
        resultado = code_write(self.cfg, "haz nuevo", self.ref, backend=backend)
        self.assertEqual(resultado, "def nuevo():\n    return 2")

    def test_con_target_escribe_y_devuelve_solo_ruta_y_lineas(self):
        backend = BackendFalso(["x = 1\ny = 2\n"])
        destino = os.path.join(self.dir.name, "sub", "salida.py")
        resultado = code_write(self.cfg, "spec", self.ref, target=destino, backend=backend)
        self.assertTrue(os.path.isfile(destino))
        with open(destino, encoding="utf-8") as f:
            self.assertEqual(f.read(), "x = 1\ny = 2")
        self.assertIn(destino, resultado)
        self.assertTrue(resultado.endswith("(2 líneas)"))
        self.assertNotIn("x = 1", resultado)

    def test_quita_los_fences_markdown(self):
        backend = BackendFalso(["```python\ndef f():\n    pass\n```"])
        resultado = code_write(self.cfg, "spec", self.ref, backend=backend)
        self.assertEqual(resultado, "def f():\n    pass")

    def test_prosa_por_delante_lanza_inputerror(self):
        # La respuesta no empieza por valla, asi que no se trata como envoltorio:
        # llega intacta a _validar_python y ahi falla al compilar.
        backend = BackendFalso(["Aquí tienes el código:\n```python\ndef f():\n    pass\n```"])
        with self.assertRaises(InputError) as ctx:
            code_write(self.cfg, "spec", self.ref, backend=backend)
        self.assertIn("Python válido", str(ctx.exception))

    def test_prosa_por_detras_lanza_inputerror(self):
        backend = BackendFalso(["```python\ndef f():\n    pass\n```\n\nEsto implementa lo pedido."])
        with self.assertRaises(InputError) as ctx:
            code_write(self.cfg, "spec", self.ref, backend=backend)
        self.assertIn("fuera del bloque", str(ctx.exception))

    def test_varias_vallas_incrustadas_en_codigo_sin_envolver_se_aceptan(self):
        # Dos funciones, cada una con una valla dentro de su docstring, sin
        # bloque markdown que las envuelva. Es Python valido y debe pasar.
        codigo = (
            'def a():\n    """\n    ```\n    """\n    return 1\n\n\n'
            'def b():\n    """\n    ```\n    """\n    return 2'
        )
        backend = BackendFalso([codigo])
        resultado = code_write(self.cfg, "spec", self.ref, backend=backend)
        self.assertEqual(resultado, codigo)

    def test_valla_sin_cerrar_pierde_solo_la_apertura(self):
        backend = BackendFalso(["```python\ndef f():\n    pass"])
        resultado = code_write(self.cfg, "spec", self.ref, backend=backend)
        self.assertEqual(resultado, "def f():\n    pass")

    def test_conserva_una_valla_interna_legitima(self):
        backend = BackendFalso(["```python\nDOC = '''\n```\n'''\n```"])
        resultado = code_write(self.cfg, "spec", self.ref, backend=backend)
        self.assertEqual(resultado, "DOC = '''\n```\n'''")

    def test_valla_suelta_en_codigo_sin_envolver_se_respeta(self):
        # Código correcto, sin bloque markdown, que contiene una valla dentro de
        # un docstring. No hay bloque que extraer: debe devolverse intacto.
        codigo = 'def sep():\n    """Ejemplo markdown:\n    ```\n    """\n    return "---"'
        backend = BackendFalso([codigo])
        resultado = code_write(self.cfg, "spec", self.ref, backend=backend)
        self.assertEqual(resultado, codigo)

    def test_prosa_con_target_no_escribe_nada_en_disco(self):
        destino = os.path.join(self.dir.name, "no-debe-existir.py")
        backend = BackendFalso(["```python\nx = 1\n```\n\nY listo."])
        with self.assertRaises(InputError):
            code_write(self.cfg, "spec", self.ref, target=destino, backend=backend)
        self.assertFalse(os.path.exists(destino))

    def test_prosa_con_valla_suelta_no_compila_y_no_llega_a_disco(self):
        # Preámbulo de prosa más una valla que nunca cierra: el recuento de vallas
        # no puede distinguirlo de un docstring, pero no compila.
        destino = os.path.join(self.dir.name, "tampoco.py")
        backend = BackendFalso(["Aquí tienes el código:\n```python\ndef f():\n    pass"])
        with self.assertRaises(InputError):
            code_write(self.cfg, "spec", self.ref, target=destino, backend=backend)
        self.assertFalse(os.path.exists(destino))

    def test_codigo_truncado_a_media_funcion_lanza_inputerror(self):
        # Lo que produce un SHUNT_MAX_OUTPUT_TOKENS corto.
        backend = BackendFalso(['def f():\n    return {"a": 1,'])
        with self.assertRaises(InputError) as ctx:
            code_write(self.cfg, "spec", self.ref, backend=backend)
        self.assertIn("Python válido", str(ctx.exception))

    def test_coletilla_de_una_palabra_tras_valla_abierta_no_llega_a_disco(self):
        # El bloque compila (Done es una expresión válida), así que solo el
        # análisis del AST puede distinguir la coletilla del código.
        destino = os.path.join(self.dir.name, "ni-de-broma.py")
        backend = BackendFalso(["```python\ndef f():\n    pass\n\nDone"])
        with self.assertRaises(InputError) as ctx:
            code_write(self.cfg, "spec", self.ref, target=destino, backend=backend)
        self.assertIn("expresión suelta", str(ctx.exception))
        self.assertFalse(os.path.exists(destino))

    def test_return_fuera_de_funcion_lanza_inputerror(self):
        # ast.parse acepta esto; solo el paso de tabla de símbolos de compile()
        # lo rechaza. Por eso se compila el árbol además de parsearlo.
        destino = os.path.join(self.dir.name, "invalido.py")
        backend = BackendFalso(["def f():\n    pass\nreturn 5"])
        with self.assertRaises(InputError):
            code_write(self.cfg, "spec", self.ref, target=destino, backend=backend)
        self.assertFalse(os.path.exists(destino))

    def test_respuesta_vacia_no_escribe_un_archivo_de_cero_bytes(self):
        # Un modulo vacio es Python valido, asi que solo un guardia explicito
        # evita que una respuesta agotada acabe en disco como exito.
        destino = os.path.join(self.dir.name, "vacio.py")
        backend = BackendFalso([""])
        with self.assertRaises(InputError):
            code_write(self.cfg, "spec", self.ref, target=destino, backend=backend)
        self.assertFalse(os.path.exists(destino))

    def test_despedida_entre_comillas_tras_el_bloque_lanza_inputerror(self):
        backend = BackendFalso(['def f():\n    pass\n\n"Listo, esto implementa lo pedido."'])
        with self.assertRaises(InputError) as ctx:
            code_write(self.cfg, "spec", self.ref, backend=backend)
        self.assertIn("expresión suelta", str(ctx.exception))

    def test_coletillas_literales_tras_el_bloque_lanzan_inputerror(self):
        # None, True, ... y los numeros son ast.Constant igual que las cadenas:
        # ninguno es codigo que alguien escriba al final de un archivo.
        for coletilla in ("None", "True", "False", "42", "..."):
            with self.subTest(coletilla=coletilla):
                backend = BackendFalso(["def f():\n    pass\n\n" + coletilla])
                with self.assertRaises(InputError) as ctx:
                    code_write(self.cfg, "spec", self.ref, backend=backend)
                self.assertIn("expresión suelta", str(ctx.exception))

    def test_docstring_de_modulo_al_principio_se_acepta(self):
        # Contracara del test anterior: al principio, una cadena suelta es un
        # docstring legitimo y debe pasar.
        codigo = '"""Utilidades de ejemplo."""\n\n\ndef f():\n    pass'
        backend = BackendFalso([codigo])
        resultado = code_write(self.cfg, "spec", self.ref, backend=backend)
        self.assertEqual(resultado, codigo)

    def test_archivo_de_solo_comentarios_se_acepta(self):
        # Los comentarios no producen nodos del AST, asi que el arbol queda
        # vacio sin que la respuesta lo este. No debe confundirse con vacia.
        codigo = "# Marcador de posicion, aun sin implementar."
        backend = BackendFalso([codigo])
        resultado = code_write(self.cfg, "spec", self.ref, backend=backend)
        self.assertEqual(resultado, codigo)

    def test_la_referencia_llega_envuelta_al_modelo(self):
        backend = BackendFalso(["x = 1"])
        code_write(self.cfg, "mi spec", self.ref, backend=backend)
        user = backend.llamadas[0]["user"]
        self.assertIn("mi spec", user)
        self.assertIn("def ejemplo():", user)
        self.assertIn('<file path="', user)

    def test_usa_la_temperatura_de_codigo(self):
        backend = BackendFalso(["x = 1"])
        code_write(self.cfg, "spec", self.ref, backend=backend)
        self.assertEqual(backend.llamadas[0]["temperature"], 0.0)

    def test_referencia_inexistente_lanza_inputerror(self):
        backend = BackendFalso(["codigo"])
        with self.assertRaises(InputError):
            code_write(self.cfg, "spec", os.path.join(self.dir.name, "no.py"), backend=backend)

    def test_referencia_que_no_cabe_lanza_budgeterror(self):
        cfg = Config.from_env({"SHUNT_MAX_CTX_TOKENS": "200", "SHUNT_MAX_OUTPUT_CODE": "50",
                                "SHUNT_MAX_OUTPUT_BULK": "50", "SHUNT_RESERVE_EXTRA": "50"})
        gorda = self._write("gorda.py", "x" * 8000)
        backend = BackendFalso(["codigo"])
        with self.assertRaises(BudgetError):
            code_write(cfg, "spec", gorda, backend=backend)

    def test_coletilla_con_coma_tras_el_bloque_lanza_inputerror(self):
        # "Listo, gracias" es una tupla para Python: ni Name ni Constant.
        backend = BackendFalso(["def f():\n    pass\n\nListo, gracias"])
        with self.assertRaises(InputError) as ctx:
            code_write(self.cfg, "spec", self.ref, backend=backend)
        self.assertIn("expresión suelta", str(ctx.exception))

    def test_llamada_suelta_al_final_se_acepta(self):
        # main() al final de un modulo es normal y debe pasar.
        codigo = "def main():\n    pass\n\n\nmain()"
        backend = BackendFalso([codigo])
        resultado = code_write(self.cfg, "spec", self.ref, backend=backend)
        self.assertEqual(resultado, codigo)

    def test_modulo_de_solo_docstring_se_acepta(self):
        # Unica cadena suelta legitima: la que es el docstring del modulo entero.
        codigo = '"""Modulo aun sin implementar."""'
        backend = BackendFalso([codigo])
        resultado = code_write(self.cfg, "spec", self.ref, backend=backend)
        self.assertEqual(resultado, codigo)

    def test_coletilla_al_principio_lanza_inputerror(self):
        # Un modelo escueto abre con un acuse tan facilmente como lo cierra.
        destino = os.path.join(self.dir.name, "con-acuse.py")
        backend = BackendFalso(["Listo\ndef f():\n    return 1"])
        with self.assertRaises(InputError) as ctx:
            code_write(self.cfg, "spec", self.ref, target=destino, backend=backend)
        self.assertIn("expresión suelta", str(ctx.exception))
        self.assertFalse(os.path.exists(destino))

    def test_coletilla_en_medio_lanza_inputerror(self):
        backend = BackendFalso(["def f():\n    pass\n\nListo\n\ndef g():\n    pass"])
        with self.assertRaises(InputError) as ctx:
            code_write(self.cfg, "spec", self.ref, backend=backend)
        self.assertIn("expresión suelta", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
