# bulk_read: citas pertinentes — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que `bulk_read`, con documentos, descarte las afirmaciones cuya cita no las respalda o no viene al caso de la pregunta, y compare números escritos con letras.

**Architecture:** Un módulo nuevo y puro, `cheap_worker_pertinencia.py`, calcula palabras con contenido, cobertura y valores numéricos. `cheap_worker_verify.verificar` lo aplica solo a tramos que no son de código (decidido por `cheap_worker_extract.es_codigo`) y recibe la pregunta. `bulk_read` pasa la pregunta; la evaluación puntúa por las citas.

**Tech Stack:** Python 3.14 (mise), `unittest`, biblioteca estándar (`re`, `unicodedata`, `importlib`).

**Spec:** `docs/superpowers/specs/2026-09-15-relevancia-citas-design.md`

## Global Constraints

- Python se invoca siempre como `mise exec -- python.exe`. Nunca `python` a secas.
- Tests: `mise exec -- python.exe -m unittest discover -s tests` desde `C:\Projects\cheap-worker-mcp`. Suite de partida: 186 tests OK.
- Rama: `feat/relevancia-citas`. Commits terminan con `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- No tocar `.mcp.json` (cambio local sin commitear).
- **Ni rutas ni contenido del corpus real entran en git.** Los ejemplos de tests son inventados.
- Umbral de cobertura: **60 %**. Raíz: **5 primeros caracteres**. Palabra con contenido: al menos **3 caracteres**, no vacía, no solo dígitos.
- Filtros de pertinencia y números con letras: **solo documentos** (extensión que no es de código). Código: verificación actual (cita literal y cifras en dígitos).
- Nuevos motivos, tras los existentes y en este orden: `cita_no_respalda` = "con cita que no respalda la afirmación"; `cita_ajena` = "con cita ajena a la pregunta".
- `FORMATO_RESPUESTA` pasa a `"citas-verificadas-2"`.
- Mensajes, comentarios y nombres en español. Escribir archivos con herramientas de edición, no heredocs (corrompen `\n` y no ASCII).

---

### Task 1: Módulo de pertinencia

**Files:**
- Create: `cheap_worker_pertinencia.py`
- Create: `tests/test_pertinencia.py`

**Interfaces:**
- Produces:
  - `UMBRAL_COBERTURA = 0.6`, `LONGITUD_RAIZ = 5`, `MIN_LONGITUD_PALABRA = 3`
  - `def palabras_clave(texto: str) -> list[str]`
  - `def cobertura(afirmacion: str, cita: str) -> float | None`
  - `def respalda(afirmacion: str, cita: str) -> bool`
  - `def toca_pregunta(pregunta: str, cita: str) -> bool`
  - `def valores(texto: str) -> set[str]`

- [ ] **Step 1: Escribir los tests que fallan**

`tests/test_pertinencia.py`:

```python
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
```

- [ ] **Step 2: Comprobar que fallan**

Run: `mise exec -- python.exe -m unittest tests.test_pertinencia`
Expected: `ModuleNotFoundError: No module named 'cheap_worker_pertinencia'`

- [ ] **Step 3: Implementar**

`cheap_worker_pertinencia.py`:

```python
#!/usr/bin/env python3
"""Pertinencia de una cita: si respalda la afirmación y si viene al caso de la pregunta.

La verificación de citas comprueba que la cita existe en el documento, y no
basta: los modelos pequeños adjuntan citas reales que no tienen relación con la
afirmación ("plazo de tres años" apoyado en un artículo sobre otra cosa). Aquí
se comparan palabras con contenido y valores numéricos, sin llamar al modelo.

Solo se aplica a documentos: con código las citas son identificadores y las
preguntas genéricas, y un filtro léxico las descartaría.

Límites conocidos: los sinónimos sin palabras en común se pierden, y compartir
palabras no garantiza respaldo lógico (negaciones, excepciones).
"""

import re
import unicodedata

UMBRAL_COBERTURA = 0.6
LONGITUD_RAIZ = 5
MIN_LONGITUD_PALABRA = 3

# Ya plegadas: minúsculas y sin tildes, como las compara _plegar.
PALABRAS_VACIAS = frozenset("""
    el la los las lo un una unos unas al del de ante bajo con contra desde durante
    entre hacia hasta mediante para por segun sin sobre tras que como cuando donde
    cual cuales quien quienes cuanto cuanta cuantos cuantas este esta estos estas
    esto ese esa esos esas eso aquel aquella aquellos aquellas sus mis tus nos les
    ser sera seran son era eran fue fueron sido siendo estar esta estan estaba
    haber hay han habia hace hacen hacer tener tiene tienen tenia puede pueden
    podra podran poder debe deben debera deberan deber mas muy tambien pero sino
    porque pues otro otra otros otras todo toda todos todas cada vez segun
    the and for with from that this these those which what who whom how when
    where there their they them than then not are was were been has have had
    does did its into onto upon also but can could should would will shall may
    might must
""".split())

UNIDADES = {
    "cero": 0, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiun": 21,
    "veintiuno": 21, "veintiuna": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27,
    "veintiocho": 28, "veintinueve": 29,
}
DECENAS = {
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90,
}
CENTENAS = {
    "cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200,
    "trescientos": 300, "trescientas": 300, "cuatrocientos": 400,
    "cuatrocientas": 400, "quinientos": 500, "quinientas": 500,
    "seiscientos": 600, "seiscientas": 600, "setecientos": 700,
    "setecientas": 700, "ochocientos": 800, "ochocientas": 800,
    "novecientos": 900, "novecientas": 900,
}
DENOMINADORES = {
    "tercio": 3, "tercios": 3, "cuarto": 4, "cuartos": 4, "quinto": 5,
    "quintos": 5, "sexto": 6, "sextos": 6, "septimo": 7, "septimos": 7,
    "octavo": 8, "octavos": 8, "noveno": 9, "novenos": 9, "decimo": 10,
    "decimos": 10,
}
# "un", "una" y "uno" no cuentan como número sueltos: "un plazo" no es una cifra.
UNO = {"un": 1, "una": 1, "uno": 1}

_SEPARADOR = re.compile(r"[^0-9a-z]+")
_FRACCION = re.compile(r"(?<![\d.,])(\d+)\s*/\s*(\d+)(?![\d.,])")
_CIFRA = re.compile(r"\d+(?:[.,]\d+)*")


def _plegar(texto):
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def _palabras(texto):
    return [p for p in _SEPARADOR.split(_plegar(texto)) if p]


def palabras_clave(texto):
    """Palabras con contenido: sin vacías, sin cifras, de al menos 3 caracteres."""
    return [
        p for p in _palabras(texto)
        if len(p) >= MIN_LONGITUD_PALABRA and p not in PALABRAS_VACIAS and not p.isdigit()
    ]


def _raices(texto):
    return {p[:LONGITUD_RAIZ] for p in palabras_clave(texto)}


def cobertura(afirmacion, cita):
    """Fracción de palabras con contenido de la afirmación presentes en la cita.

    Dos palabras coinciden si comparten los primeros 5 caracteres ("camaras" y
    "camara"). None si la afirmación no tiene palabras con contenido.
    """
    claves = palabras_clave(afirmacion)
    if not claves:
        return None
    raices = _raices(cita)
    return sum(1 for p in claves if p[:LONGITUD_RAIZ] in raices) / len(claves)


def respalda(afirmacion, cita):
    valor = cobertura(afirmacion, cita)
    return valor is not None and valor >= UMBRAL_COBERTURA


def toca_pregunta(pregunta, cita):
    """La cita comparte al menos una palabra con contenido con la pregunta."""
    claves = palabras_clave(pregunta)
    if not claves:
        return True
    raices = _raices(cita)
    return any(p[:LONGITUD_RAIZ] in raices for p in claves)


def _leer_numero(palabras, i):
    """Número escrito con palabras a partir de la posición i: (valor, siguiente)."""
    total = 0
    actual = 0
    leido = False
    j = i
    while j < len(palabras):
        p = palabras[j]
        if p in CENTENAS:
            actual += CENTENAS[p]
        elif p in DECENAS:
            actual += DECENAS[p]
        elif p in UNIDADES:
            actual += UNIDADES[p]
        elif p == "mil":
            total += (actual or 1) * 1000
            actual = 0
        elif p in UNO and leido:
            actual += 1
        elif p == "y" and leido and j + 1 < len(palabras) and (
            palabras[j + 1] in UNIDADES or palabras[j + 1] in UNO
        ):
            pass
        else:
            break
        leido = True
        j += 1
    if not leido:
        return None, i
    return total + actual, j


def valores(texto):
    """Valores numéricos canónicos: dígitos, números con letras y fracciones.

    "doce" y "12" dan "12"; "tres quintos" y "3/5" dan "3/5". Las fracciones se
    leen primero para que el numerador no cuente además como número suelto.
    """
    plano = _plegar(texto)
    resultado = {f"{int(n)}/{int(d)}" for n, d in _FRACCION.findall(plano)}
    sin_fracciones = _FRACCION.sub(" ", plano)
    resultado.update(_CIFRA.findall(sin_fracciones))

    palabras = [p for p in _palabras(sin_fracciones) if not p.isdigit()]
    i = 0
    while i < len(palabras):
        p = palabras[i]
        if p == "mitad":
            resultado.add("1/2")
            i += 1
            continue
        numerador = UNIDADES.get(p, UNO.get(p))
        if numerador is not None and i + 1 < len(palabras) and palabras[i + 1] in DENOMINADORES:
            resultado.add(f"{numerador}/{DENOMINADORES[palabras[i + 1]]}")
            i += 2
            continue
        valor, siguiente = _leer_numero(palabras, i)
        if valor is None:
            i += 1
        else:
            resultado.add(str(valor))
            i = siguiente
    return resultado
```

- [ ] **Step 4: Comprobar que pasan**

Run: `mise exec -- python.exe -m unittest tests.test_pertinencia`
Expected: `OK` (15 tests). Si `test_raiz_de_cinco_caracteres` u otro caso de cobertura falla, no cambiar el umbral ni la raíz: informar con el valor obtenido.

- [ ] **Step 5: Suite completa y commit**

Run: `mise exec -- python.exe -m unittest discover -s tests` → `OK`

```bash
git add cheap_worker_pertinencia.py tests/test_pertinencia.py
git commit -m "feat: modulo de pertinencia de citas y valores numericos en letras

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: es_codigo compartido con el hook

**Files:**
- Modify: `cheap_worker_extract.py` (añadir `EXTENSIONES_CODIGO` y `es_codigo` tras `Documento`)
- Modify: `tests/test_extract.py` (clase nueva `TestEsCodigo`)

**Interfaces:**
- Produces: `cheap_worker_extract.EXTENSIONES_CODIGO: frozenset[str]`; `cheap_worker_extract.es_codigo(ruta: str) -> bool`.

- [ ] **Step 1: Escribir los tests que fallan**

Al final de `tests/test_extract.py`, antes de `if __name__ == "__main__":`:

```python
class TestEsCodigo(unittest.TestCase):
    def test_extensiones_de_codigo_y_de_documento(self):
        from cheap_worker_extract import es_codigo
        self.assertTrue(es_codigo("a/b/modulo.py"))
        self.assertTrue(es_codigo("Script.PS1"))
        self.assertFalse(es_codigo("ficha.md"))
        self.assertFalse(es_codigo("informe.pdf"))
        self.assertFalse(es_codigo("sin_extension"))

    def test_misma_lista_que_el_hook(self):
        # El hook no importa nada del proyecto (se ejecuta desde otros); conserva
        # su copia, y este test impide que las dos listas se separen.
        import importlib.util
        from cheap_worker_extract import EXTENSIONES_CODIGO
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location(
            "hook_bloqueo", os.path.join(raiz, "hooks", "bloquear-lectura-grande.py"))
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        self.assertEqual(set(EXTENSIONES_CODIGO), set(hook.EXTENSIONES_CODIGO))
```

- [ ] **Step 2: Comprobar que fallan**

Run: `mise exec -- python.exe -m unittest tests.test_extract.TestEsCodigo`
Expected: `ImportError: cannot import name 'es_codigo'`

- [ ] **Step 3: Implementar**

En `cheap_worker_extract.py`, justo después de la clase `Documento`:

```python
# Misma lista que hooks/bloquear-lectura-grande.py, que conserva su copia porque
# se ejecuta desde otros proyectos. tests/test_extract.py comprueba que coinciden.
EXTENSIONES_CODIGO = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt", ".scala",
    ".cs", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cc", ".rb", ".php", ".swift",
    ".m", ".lua", ".pl", ".r", ".sql", ".sh", ".bash", ".ps1", ".psm1", ".vue", ".svelte",
})


def es_codigo(ruta: str) -> bool:
    return os.path.splitext(ruta)[1].lower() in EXTENSIONES_CODIGO
```

- [ ] **Step 4: Comprobar que pasan**

Run: `mise exec -- python.exe -m unittest tests.test_extract`
Expected: `OK`. Si `test_misma_lista_que_el_hook` falla, la lista del hook es la referencia: copiar la suya, no cambiar el hook.

- [ ] **Step 5: Commit**

```bash
git add cheap_worker_extract.py tests/test_extract.py
git commit -m "feat: es_codigo en la extraccion, con la lista del hook

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Aplicar la pertinencia en la verificación, bulk_read y la evaluación

**Files:**
- Modify: `cheap_worker_verify.py` (imports, `MOTIVOS`, `verificar`, `verificar_respuesta`)
- Modify: `cheap_worker_core.py` (`FORMATO_RESPUESTA`, llamada a `verificar_respuesta`)
- Modify: `eval-bulk-read.py` (`acierta`, nueva `citas_verificadas`)
- Modify: `tests/test_verify.py`, `tests/test_bulk_read.py`
- Create: `tests/test_eval.py`

**Interfaces:**
- Consumes: `respalda`, `toca_pregunta`, `valores` (Task 1); `es_codigo` (Task 2).
- Produces:
  - `verificar(afirmaciones, tramos, pregunta="")`, `verificar_respuesta(texto, tramos, pregunta="")`.
  - `MOTIVOS` con `cita_no_respalda` y `cita_ajena` al final.
  - `FORMATO_RESPUESTA = "citas-verificadas-2"`.
  - `eval-bulk-read.py`: `citas_verificadas(resultado) -> str`; `acierta` usa solo citas.

- [ ] **Step 1: Tests de verificación que fallan**

En `tests/test_verify.py`, añadir antes de `class TestComponer`:

```python
class TestPertinencia(unittest.TestCase):
    """Los filtros de pertinencia, solo en documentos (sección 6 del diseño)."""

    def una(self, pregunta, afirmacion, cita, lineas, ruta="doc.md"):
        return verificar([Afirmacion(afirmacion, cita)], [tramo(lineas, ruta=ruta)], pregunta)

    def test_cita_que_respalda_y_toca_la_pregunta_pasa(self):
        verificadas, descartes = self.una(
            "¿Qué mayoría exige la reforma del estatuto?",
            "La reforma del estatuto requiere una mayoría de 3/5 en cada junta.",
            "Mayoría 3/5: necesaria para la reforma del estatuto en cada una de las juntas",
            ["Mayoría 3/5: necesaria para la reforma del estatuto en cada una de las juntas"])
        self.assertEqual((len(verificadas), descartes), (1, Counter()))

    def test_cita_de_otro_asunto_no_respalda(self):
        verificadas, descartes = self.una(
            "¿En qué plazo se resuelve un recurso?",
            "El recurso se resuelve en un plazo de tres años.",
            "A los tres años de su nombramiento, los vocales del consejo se renuevan por sorteo",
            ["A los tres años de su nombramiento, los vocales del consejo se renuevan por sorteo"])
        self.assertEqual((verificadas, descartes), ([], Counter({"cita_no_respalda": 1})))

    def test_numero_en_letras_que_no_esta_en_la_cita(self):
        verificadas, descartes = self.una(
            "¿Cuántos vocales tiene el consejo?",
            "El consejo tiene doce vocales.",
            "Las juntas podrán delegar en la comisión la potestad de dictar normas",
            ["Las juntas podrán delegar en la comisión la potestad de dictar normas"])
        self.assertEqual((verificadas, descartes), ([], Counter({"cifras_no_respaldadas": 1})))

    def test_anadidos_que_la_cita_no_dice(self):
        verificadas, descartes = self.una(
            "¿Qué hace falta para convocar una asamblea?",
            "Para convocar una asamblea hace falta 1/10 de los socios de cualquiera de las juntas.",
            "Mayoría 1/10: la que se necesita para convocar una asamblea por parte de los socios",
            ["Mayoría 1/10: la que se necesita para convocar una asamblea por parte de los socios"])
        self.assertEqual((verificadas, descartes), ([], Counter({"cita_no_respalda": 1})))

    def test_cita_que_respalda_pero_ajena_a_la_pregunta(self):
        linea = "Los vocales del consejo se renuevan por sorteo cada tres años"
        verificadas, descartes = self.una(
            "¿Qué mayoría exige la reforma del estatuto?",
            "Los vocales del consejo se renuevan por sorteo cada tres años.",
            linea, [linea])
        self.assertEqual((verificadas, descartes), ([], Counter({"cita_ajena": 1})))

    def test_numero_en_letras_equivalente_a_digitos_pasa(self):
        linea = "El consejo se compone de 12 vocales elegidos por la junta"
        verificadas, _ = self.una("¿Cuántos vocales tiene el consejo?",
                                  "El consejo se compone de doce vocales.", linea, [linea])
        self.assertEqual(len(verificadas), 1)

    def test_cifra_en_letras_inventada_se_descarta(self):
        linea = "El plazo para resolver el recurso es de treinta días"
        _, descartes = self.una("¿Qué plazo tiene el recurso?",
                                "El plazo para resolver el recurso es de quince días.", linea, [linea])
        self.assertEqual(descartes, Counter({"cifras_no_respaldadas": 1}))

    def test_documento_en_ingles(self):
        linea = "The committee shall approve the annual budget before March"
        verificadas, _ = self.una("Who approves the budget?",
                                  "The committee approves the annual budget.", linea, [linea], ruta="doc.txt")
        self.assertEqual(len(verificadas), 1)

    def test_el_codigo_no_pasa_por_los_filtros_de_pertinencia(self):
        linea = "def bulk_read(cfg, question, paths, backend=None):"
        verificadas, descartes = self.una("¿Qué funciones define?", "bulk_read: analiza dos archivos",
                                          "def bulk_read(cfg, question, paths, backend=None)",
                                          [linea], ruta="modulo.py")
        self.assertEqual((len(verificadas), descartes), (1, Counter()))

    def test_sin_pregunta_no_descarta_por_ajena(self):
        linea = "Los vocales del consejo se renuevan por sorteo cada tres años"
        verificadas, _ = verificar(
            [Afirmacion("Los vocales del consejo se renuevan por sorteo cada tres años.", linea)],
            [tramo([linea])])
        self.assertEqual(len(verificadas), 1)
```

Y en el test existente `test_busca_en_todos_los_tramos_del_bloque`, cambiar la afirmación `"Hecho"` por `"La cita correcta"` (con "Hecho" la cita no la respaldaría y el test dejaría de medir lo que dice su nombre).

- [ ] **Step 2: Comprobar que fallan**

Run: `mise exec -- python.exe -m unittest tests.test_verify.TestPertinencia`
Expected: FAIL/ERROR (`verificar()` no acepta la pregunta; motivos nuevos inexistentes).

- [ ] **Step 3: Implementar en la verificación**

En `cheap_worker_verify.py`:

Imports, tras `from dataclasses import dataclass`:

```python

from cheap_worker_extract import es_codigo
from cheap_worker_pertinencia import respalda, toca_pregunta, valores
```

En `MOTIVOS`, añadir al final:

```python
    "cita_no_respalda": "con cita que no respalda la afirmación",
    "cita_ajena": "con cita ajena a la pregunta",
```

Sustituir la firma `def verificar(afirmaciones, tramos):` por `def verificar(afirmaciones, tramos, pregunta=""):` y, dentro, sustituir desde `if hallada is None:` hasta `tramo, linea = hallada` (inclusive) por:

```python
        if hallada is None:
            descartes["cita_no_encontrada"] += 1
            continue
        tramo, linea = hallada
        documento = not es_codigo(tramo.ruta)
        # En documentos se comparan valores: "doce" equivale a "12". En código,
        # solo cifras en dígitos: "dos valores" frente a `return a, b` no es un
        # número inventado.
        if documento:
            numeros_ok = valores(afirmacion.texto) <= valores(afirmacion.cita)
        else:
            numeros_ok = cifras(afirmacion.texto) <= cifras(afirmacion.cita)
        if not numeros_ok:
            descartes["cifras_no_respaldadas"] += 1
            continue
        if documento and not respalda(afirmacion.texto, afirmacion.cita):
            descartes["cita_no_respalda"] += 1
            continue
        if documento and not toca_pregunta(pregunta, afirmacion.cita):
            descartes["cita_ajena"] += 1
            continue
```

Actualizar el docstring del módulo: sustituir el párrafo "Límites conocidos: …" por:

```
Con documentos, además, la cita debe respaldar la afirmación y tocar la
pregunta (cheap_worker_pertinencia), y los números se comparan por valor.

Límites conocidos: los sinónimos sin palabras en común se pierden, y compartir
palabras no garantiza respaldo lógico.
```

Sustituir `def verificar_respuesta(texto, tramos):` por `def verificar_respuesta(texto, tramos, pregunta=""):` y su llamada interna por `verificar(afirmaciones, tramos, pregunta)`.

- [ ] **Step 4: Comprobar la verificación**

Run: `mise exec -- python.exe -m unittest tests.test_verify`
Expected: `OK`. Si falla un test **existente** con archivo `.md` solo porque su afirmación no la respalda su cita (motivo `cita_no_respalda`), cambiar el texto de la afirmación por uno que la cita respalde, sin tocar la cita ni el resultado esperado, y anotarlo en el informe.

- [ ] **Step 5: bulk_read pasa la pregunta — test que falla**

En `tests/test_bulk_read.py`, añadir:

```python
    def test_una_cita_ajena_a_la_pregunta_se_descarta(self):
        path = self._write("a.md", "Los vocales del consejo se renuevan por sorteo cada tres años\n")
        backend = BackendFalso([
            "- Los vocales del consejo se renuevan por sorteo cada tres años\n"
            "  > Los vocales del consejo se renuevan por sorteo cada tres años"
        ])
        resultado = bulk_read(self.cfg, "¿Qué mayoría exige la reforma del estatuto?", [path], backend=backend)
        self.assertTrue(resultado.startswith("No consta en los documentos."))
        self.assertIn("1 con cita ajena a la pregunta", resultado)
```

Run: `mise exec -- python.exe -m unittest tests.test_bulk_read`
Expected: FAIL (la pregunta no llega a la verificación).

- [ ] **Step 6: Implementar en el núcleo**

En `cheap_worker_core.py`:

```python
FORMATO_RESPUESTA = "citas-verificadas-2"
```

y cambiar la llamada en `bulk_read`:

```python
        buenas, malas = verificar_respuesta(respuesta, bloque.tramos, question)
```

Run: `mise exec -- python.exe -m unittest tests.test_bulk_read tests.test_cache`
Expected: `OK`

- [ ] **Step 7: Evaluación por citas — test que falla**

`tests/test_eval.py`:

```python
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
```

Run: `mise exec -- python.exe -m unittest tests.test_eval`
Expected: FAIL en `test_el_dato_en_la_afirmacion_pero_no_en_la_cita_no_acierta`.

- [ ] **Step 8: Implementar en la evaluación**

En `eval-bulk-read.py`, añadir tras `parte_verificada`:

```python
def citas_verificadas(resultado):
    """Texto de las líneas de cita (`>`) de las afirmaciones verificadas.

    Se puntúa por la cita y no por la afirmación: el modelo puede escribir el
    dato correcto en la afirmación y adjuntar una cita que no lo contiene.
    """
    return " ".join(
        linea.strip()[1:].strip()
        for linea in parte_verificada(resultado).splitlines()
        if linea.strip().startswith(">")
    )
```

y en `acierta`, sustituir `texto = normalizar(verificada)` por `texto = normalizar(citas_verificadas(resultado))`. Actualizar el docstring del módulo: "Un caso `dato` u `orientacion` acierta si todos sus fragmentos esperados aparecen en las citas de las afirmaciones verificadas."

- [ ] **Step 9: Suite completa y commit**

Run: `mise exec -- python.exe -m unittest discover -s tests`
Expected: `OK`

```bash
git add cheap_worker_verify.py cheap_worker_core.py eval-bulk-read.py tests/test_verify.py tests/test_bulk_read.py tests/test_eval.py
git commit -m "feat: con documentos, descartar citas que no respaldan la afirmacion o son ajenas a la pregunta

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Documentación

**Files:**
- Modify: `README.md` (sección `## Qué lee \`bulk_read\` y qué garantiza`)

- [ ] **Step 1: README**

En `README.md`, dentro de `## Qué lee \`bulk_read\` y qué garantiza`, sustituir el párrafo que empieza por `**Lo que no garantiza:**` por:

```markdown
**Con documentos, además, la cita tiene que venir al caso.** Una afirmación se descarta si menos del 60 % de sus palabras con contenido aparecen en su cita ("con cita que no respalda la afirmación") o si la cita no comparte ninguna palabra con la pregunta ("con cita ajena a la pregunta"). Los números se comparan por valor: "doce" equivale a "12" y "tres quintos" a "3/5". Con código no se aplican estos filtros: las citas son identificadores y las preguntas, genéricas.

La prioridad es mostrar poco y pertinente: a veces responderá `No consta` aunque el dato esté.

**Lo que no garantiza:** los sinónimos sin palabras en común se pierden ("mayoría cualificada" frente a "tres quintos"), y compartir palabras no asegura que la cita respalde la conclusión (negaciones, excepciones). Un modelo pequeño no sustituye a leer: para extraer datos que importan, lee el documento.
```

- [ ] **Step 2: Comprobar y commit**

Run: `mise exec -- python.exe -m unittest discover -s tests` → `OK`

```bash
git add README.md
git commit -m "docs: filtros de pertinencia de citas en documentos

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Medición real y decisión de modelo

**No la hace un subagente:** usa el corpus real (fuera de git) y termina en decisiones del usuario. La ejecuta la sesión principal.

- [ ] **Step 1:** Comprobar `ollama ps` → `CONTEXT` 8192 con un modelo cargado.
- [ ] **Step 2:** Ejecutar en segundo plano, con `SHUNT_MAX_CTX_TOKENS=8192 SHUNT_MAX_OUTPUT_BULK=512 SHUNT_API_BASE=http://localhost:11434/v1`:
  `mise exec -- python.exe eval-bulk-read.py .cache/eval/preguntas.json --modelos qwen2.5:3b,llama3.2:3b,gemma3:4b,qwen2.5-coder:3b --salida .cache/eval/respuestas-pertinencia`
- [ ] **Step 3:** Anotar el reparto CPU/GPU de cada modelo (`ollama ps` durante su turno).
- [ ] **Step 4:** Revisar a mano cada afirmación verificada frente al documento: correcta y respaldada, correcta con cita débil, o errónea.
- [ ] **Step 5:** Informar al usuario (tabla por modelo: aciertos reales, "No consta" correctos, descartes por motivo, tiempo, CPU/GPU) y proponer modelo. Esperar decisión antes de cambiar `SHUNT_MODEL_BULK` en la configuración global y en `claude_desktop_config.json`, o de borrar modelos.
- [ ] **Step 6:** Actualizar la tabla de `### Con documentos: evaluación` del README con los resultados agregados (sin contenido del corpus) y commit.
