# bulk_read: que el modelo cite — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tener tres variantes del prompt de `bulk_read` medibles con `eval-bulk-read.py`, para quedarse con la que produzca más afirmaciones correctas y respaldadas.

**Architecture:** `cheap_worker_core._mensajes_bulk(pregunta, texto_bloque, variante)` construye los mensajes de cada variante; `VARIANTE_PROMPT` elige la activa y entra en la clave de caché. `cheap_worker_verify.analizar_respuesta` gana un modo `cita_primero`. `eval-bulk-read.py --variantes` recorre modelo × variante. La medición y la limpieza final las hace la sesión principal.

**Tech Stack:** Python 3.14 (mise), `unittest`, Ollama.

**Spec:** `docs/superpowers/specs/2026-09-15-prompt-citas-design.md`

## Global Constraints

- Python se invoca siempre como `mise exec -- python.exe`. Nunca `python` a secas.
- Tests: `mise exec -- python.exe -m unittest discover -s tests` desde `C:\Projects\cheap-worker-mcp`. Suite de partida: 242 OK.
- Rama: `feat/prompt-citas`. Commits terminan con `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- No tocar `.mcp.json`. Nada del corpus real en git: el ejemplo del prompt es inventado.
- Variantes, con estos nombres exactos: `actual`, `pregunta_al_final`, `cita_primero`.
- `VARIANTE_PROMPT` es una constante del núcleo, no una variable de entorno. Mientras se mide vale `"actual"`.
- `FORMATO_RESPUESTA` no cambia en este plan: la variante entra por separado en la clave de caché.
- El margen `SHUNT_RESERVE_EXTRA` por defecto pasa de 256 a **512**: debe cubrir el prompt de sistema y el texto fijo del mensaje de usuario de cualquier variante.
- Mensajes, comentarios y nombres en español (los prompts al modelo siguen en inglés, como hoy, salvo el ejemplo). Escribir con herramientas de edición, no heredocs.

---

### Task 1: Variantes del prompt en el núcleo

**Files:**
- Modify: `cheap_worker_core.py` (default de `reserve_extra`; `_clave_cache`; bloque de `SYSTEM_BULK`; `bulk_read`)
- Modify: `tests/test_config.py`, `tests/test_bulk_read.py`, `tests/test_cache.py`
- Modify: `README.md` (fila de `SHUNT_RESERVE_EXTRA`)

**Interfaces:**
- Produces:
  - `VARIANTES_PROMPT = ("actual", "pregunta_al_final", "cita_primero")`
  - `VARIANTE_PROMPT = "actual"`
  - `SYSTEM_BULK` (sin cambios), `SYSTEM_BULK_PREGUNTA_AL_FINAL`, `SYSTEM_BULK_CITA_PRIMERO`
  - `def _mensajes_bulk(pregunta: str, texto_bloque: str, variante: str) -> tuple[str, str]` — `ValueError` si la variante no existe.

- [ ] **Step 1: Tests que fallan**

En `tests/test_config.py`, cambiar en el primer test la aserción `self.assertEqual(cfg.reserve_extra, 256)` por `self.assertEqual(cfg.reserve_extra, 512)`, y sustituir la clase `TestInvarianteDelPresupuesto` por:

```python
class TestInvarianteDelPresupuesto(unittest.TestCase):
    """Antes habia que mantener la coherencia a mano; ahora es estructural."""

    def _fijo(self):
        """Tokens de los mensajes que no son el documento, en la peor variante."""
        from cheap_worker_core import SYSTEM_CODE, VARIANTES_PROMPT, _mensajes_bulk, estimate_tokens

        bulk = max(
            estimate_tokens(sistema) + estimate_tokens(usuario)
            for sistema, usuario in (_mensajes_bulk("", "", v) for v in VARIANTES_PROMPT)
        )
        return max(bulk, estimate_tokens(SYSTEM_CODE))

    def test_el_margen_cubre_el_texto_fijo_de_los_mensajes(self):
        cfg = Config.from_env({})
        self.assertLessEqual(self._fijo(), cfg.reserve_extra,
                             "el margen por defecto no cubre el texto fijo de los mensajes")

    def test_ninguna_herramienta_desborda_la_ventana(self):
        fijo = self._fijo()
        for ctx in ("4096", "8192", "16384"):
            cfg = Config.from_env({"SHUNT_MAX_CTX_TOKENS": ctx})
            for nombre, perfil in (("bulk", cfg.perfil_bulk), ("code", cfg.perfil_code)):
                with self.subTest(ctx=ctx, herramienta=nombre):
                    self.assertLessEqual(perfil.presupuesto + perfil.salida_max + fijo,
                                         cfg.max_ctx_tokens)
```

Si en el archivo quedaba algún otro test de esa clase (por ejemplo, `test_un_presupuesto_negativo_se_rechaza_al_configurar`), conservarlo dentro de la clase nueva sin cambios.

En `tests/test_bulk_read.py`, añadir a los imports `from unittest import mock`, `import cheap_worker_core` y, junto a `SYSTEM_BULK`, `VARIANTES_PROMPT, _mensajes_bulk`. Añadir la clase:

```python
class TestMensajesBulk(unittest.TestCase):
    PREGUNTA = "¿Cuántas veces se reúne el consejo?"
    TEXTO = '<file path="a.md">\nEl consejo se reúne tres veces.\n</file>\n'

    def test_actual_reproduce_el_formato_de_siempre(self):
        sistema, usuario = _mensajes_bulk(self.PREGUNTA, self.TEXTO, "actual")
        self.assertIs(sistema, SYSTEM_BULK)
        self.assertEqual(usuario, f"Question: {self.PREGUNTA}\n\nFiles:\n{self.TEXTO}")

    def test_la_pregunta_va_despues_del_documento(self):
        for variante in ("pregunta_al_final", "cita_primero"):
            with self.subTest(variante=variante):
                _, usuario = _mensajes_bulk(self.PREGUNTA, self.TEXTO, variante)
                self.assertGreater(usuario.index(self.PREGUNTA), usuario.index(self.TEXTO))
                self.assertTrue(usuario.rstrip().endswith("Answer:"))

    def test_las_variantes_nuevas_llevan_ejemplo_y_no_consta(self):
        for variante in ("pregunta_al_final", "cita_primero"):
            with self.subTest(variante=variante):
                sistema, _ = _mensajes_bulk(self.PREGUNTA, self.TEXTO, variante)
                self.assertIn("Example", sistema)
                self.assertIn("NO CONSTA", sistema)
                self.assertIn("La junta se reúne dos veces al año", sistema)

    def test_orden_del_ejemplo_segun_la_variante(self):
        normal, _ = _mensajes_bulk(self.PREGUNTA, self.TEXTO, "pregunta_al_final")
        primero, _ = _mensajes_bulk(self.PREGUNTA, self.TEXTO, "cita_primero")
        self.assertLess(normal.index("- La junta se reúne"), normal.index("> La junta se reúne"))
        self.assertLess(primero.index("> La junta se reúne"), primero.index("- La junta se reúne"))

    def test_variante_desconocida(self):
        with self.assertRaises(ValueError):
            _mensajes_bulk(self.PREGUNTA, self.TEXTO, "otra")

    def test_bulk_read_usa_la_variante_activa(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "a.md")
            with open(ruta, "w", encoding="utf-8") as f:
                f.write("texto sin relación\n")
            cfg = Config.from_env({"SHUNT_CACHE_MAX": "0"})
            backend = BackendFalso(["NO CONSTA"])
            with mock.patch.object(cheap_worker_core, "VARIANTE_PROMPT", "pregunta_al_final"):
                bulk_read(cfg, "¿Plazo?", [ruta], backend=backend)
            self.assertIs(backend.llamadas[0]["system"], cheap_worker_core.SYSTEM_BULK_PREGUNTA_AL_FINAL)
            self.assertTrue(backend.llamadas[0]["user"].rstrip().endswith("Answer:"))
```

En `tests/test_cache.py`, añadir a `TestCache`:

```python
    def test_cambiar_la_variante_de_prompt_invalida_la_entrada(self):
        cfg = self._cfg()
        bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("antiguo")]))
        with mock.patch.object(cheap_worker_core, "VARIANTE_PROMPT", "pregunta_al_final"):
            nuevo = bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("actual")]))
        self.assertIn("- actual", nuevo)
```

- [ ] **Step 2: Comprobar que fallan**

Run: `mise exec -- python.exe -m unittest tests.test_config tests.test_bulk_read tests.test_cache`
Expected: `ImportError` (`VARIANTES_PROMPT`, `_mensajes_bulk`) y el default 512.

- [ ] **Step 3: Implementar**

En `cheap_worker_core.py`:

**3a.** En `Config.from_env`, cambiar `reserve_extra=int(env.get("SHUNT_RESERVE_EXTRA", "256"))` por `"512"`.

**3b.** En `_clave_cache`, añadir `VARIANTE_PROMPT` a `partes`, justo después de `FORMATO_RESPUESTA`:

```python
    partes = [FORMATO_RESPUESTA, VARIANTE_PROMPT, perfil.modelo, str(perfil.salida_max),
              str(perfil.temperatura), question, *bloques, *faltan]
```

**3c.** Justo después de la definición de `SYSTEM_BULK` (que no cambia), añadir:

```python
# Variantes del prompt en medición (ver docs/superpowers/specs/2026-09-15-prompt-citas-design.md).
# Con trozos de ~7000 tokens, un modelo pequeño que lee la pregunta antes del
# documento la olvida y resume; y a veces "cita" su propia frase. Las variantes
# nuevas repiten la pregunta tras el documento y enseñan el formato con un
# ejemplo inventado. La medición decide cuál queda.
VARIANTES_PROMPT = ("actual", "pregunta_al_final", "cita_primero")
VARIANTE_PROMPT = "actual"

_EJEMPLO_DOCUMENTO = (
    'Files: "Artículo 4. La junta se reúne dos veces al año. '
    'Sus acuerdos requieren mayoría simple."\n'
)

SYSTEM_BULK_PREGUNTA_AL_FINAL = (
    "You answer ONE question using ONLY the files the user sends. Never summarize the files, "
    "never guess, never invent numbers. Answer in the language of the question.\n"
    "For each fact that answers the question, write two lines:\n"
    "- <the fact>\n"
    "  > <a sentence copied character by character from the files that proves it>\n"
    "The quote must be copied from the files, never written by you.\n"
    "If the files do not answer the question, write only: NO CONSTA\n\n"
    "Example.\n"
    + _EJEMPLO_DOCUMENTO
    + "Question: ¿Cuántas veces se reúne la junta?\n"
    "Answer:\n"
    "- La junta se reúne dos veces al año.\n"
    "  > La junta se reúne dos veces al año\n"
    "Question: ¿Quién preside la junta?\n"
    "Answer:\n"
    "NO CONSTA"
)

SYSTEM_BULK_CITA_PRIMERO = (
    "You answer ONE question using ONLY the files the user sends. Never summarize the files, "
    "never guess, never invent numbers. Answer in the language of the question.\n"
    "For each fact that answers the question, write two lines, quote first:\n"
    "> <a sentence copied character by character from the files>\n"
    "- <the fact that this sentence proves>\n"
    "The quote must be copied from the files, never written by you.\n"
    "If the files do not answer the question, write only: NO CONSTA\n\n"
    "Example.\n"
    + _EJEMPLO_DOCUMENTO
    + "Question: ¿Cuántas veces se reúne la junta?\n"
    "Answer:\n"
    "> La junta se reúne dos veces al año\n"
    "- La junta se reúne dos veces al año.\n"
    "Question: ¿Quién preside la junta?\n"
    "Answer:\n"
    "NO CONSTA"
)

_RECORDATORIO = {
    "pregunta_al_final": (
        "Answer only this question, do not summarize. Every fact needs its line \"> \" with a "
        "quote copied from the files. If the files do not answer it, write NO CONSTA.\nAnswer:\n"
    ),
    "cita_primero": (
        "Answer only this question, do not summarize. For every fact, first the line \"> \" with "
        "a quote copied from the files, then the line \"- \" with the fact. If the files do not "
        "answer it, write NO CONSTA.\nAnswer:\n"
    ),
}

_SISTEMA = {
    "pregunta_al_final": SYSTEM_BULK_PREGUNTA_AL_FINAL,
    "cita_primero": SYSTEM_BULK_CITA_PRIMERO,
}


def _mensajes_bulk(pregunta, texto_bloque, variante):
    """(system, user) de una llamada de bulk_read según la variante del prompt."""
    if variante == "actual":
        return SYSTEM_BULK, f"Question: {pregunta}\n\nFiles:\n{texto_bloque}"
    if variante in _SISTEMA:
        usuario = f"Files:\n{texto_bloque}\n\nQuestion: {pregunta}\n{_RECORDATORIO[variante]}"
        return _SISTEMA[variante], usuario
    raise ValueError(f"Variante de prompt desconocida: {variante}")
```

**3d.** En `bulk_read`, sustituir la línea
`respuesta = backend.chat(perfil, SYSTEM_BULK, f"Question: {question}\n\nFiles:\n{bloque.texto}")`
por:

```python
        sistema, usuario = _mensajes_bulk(question, bloque.texto, VARIANTE_PROMPT)
        respuesta = backend.chat(perfil, sistema, usuario)
```

**3e.** En `README.md`, fila de la tabla de configuración: `| \`SHUNT_RESERVE_EXTRA\` | \`256\` | Margen para el prompt de sistema |` → `| \`SHUNT_RESERVE_EXTRA\` | \`512\` | Margen para el texto fijo de los mensajes (prompt de sistema, ejemplo y recordatorio) |`.

- [ ] **Step 4: Comprobar**

Run: `mise exec -- python.exe -m unittest tests.test_config tests.test_bulk_read tests.test_cache` → `OK`. Si `test_config` falla porque algún test fija `presupuesto` con el margen por defecto, actualizar el número esperado a 512 y anotarlo.

Run: `mise exec -- python.exe -m unittest discover -s tests` → `OK`

- [ ] **Step 5: Commit**

```bash
git add cheap_worker_core.py tests/test_config.py tests/test_bulk_read.py tests/test_cache.py README.md
git commit -m "feat: variantes del prompt de bulk_read con la pregunta tras el documento

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Analizador con la cita primero

**Files:**
- Modify: `cheap_worker_verify.py` (`analizar_respuesta`, nueva `_analizar_cita_primero`, `verificar_respuesta`)
- Modify: `cheap_worker_core.py` (llamada a `verificar_respuesta` en `bulk_read`)
- Modify: `tests/test_verify.py`, `tests/test_bulk_read.py`

**Interfaces:**
- Consumes: `VARIANTE_PROMPT`, `_mensajes_bulk` (Task 1).
- Produces: `analizar_respuesta(texto, cita_primero=False)`; `verificar_respuesta(texto, tramos, pregunta="", cita_primero=False)`.

- [ ] **Step 1: Tests que fallan**

En `tests/test_verify.py`, dentro de `TestAnalizarRespuesta`:

```python
    def test_cita_primero_asigna_la_cita_a_la_vineta_siguiente(self):
        texto = "> requerirá mayoría absoluta del Congreso\n- Requieren mayoría absoluta"
        self.assertEqual(
            analizar_respuesta(texto, cita_primero=True),
            [Afirmacion("Requieren mayoría absoluta", "requerirá mayoría absoluta del Congreso")],
        )

    def test_cita_primero_con_varias_afirmaciones(self):
        texto = "> cita uno aquí\n- uno\n> cita dos aquí\n> sigue la dos\n- dos\n  continúa"
        self.assertEqual(analizar_respuesta(texto, cita_primero=True), [
            Afirmacion("uno", "cita uno aquí"),
            Afirmacion("dos continúa", "cita dos aquí sigue la dos"),
        ])

    def test_cita_primero_cita_final_sin_vineta_se_ignora(self):
        texto = "> cita uno aquí\n- uno\n> cita suelta al final"
        self.assertEqual(analizar_respuesta(texto, cita_primero=True),
                         [Afirmacion("uno", "cita uno aquí")])

    def test_cita_primero_vineta_sin_cita_previa(self):
        self.assertEqual(analizar_respuesta("- sin cita", cita_primero=True),
                         [Afirmacion("sin cita", "")])

    def test_cita_primero_no_consta(self):
        self.assertEqual(analizar_respuesta("NO CONSTA", cita_primero=True), [])
```

En `TestVerificarRespuesta`:

```python
    def test_cita_primero_solo_citas_es_sin_formato(self):
        _, descartes = verificar_respuesta("> una cita sin afirmación alguna", [tramo(self.LINEAS)],
                                           cita_primero=True)
        self.assertEqual(descartes, Counter({"sin_formato": 1}))
```

En `tests/test_bulk_read.py`, dentro de `TestBulkRead`:

```python
    def test_cita_primero_de_extremo_a_extremo(self):
        path = self._write("a.md", "# Título\nLas leyes orgánicas requieren mayoría absoluta del Congreso.\n")
        backend = BackendFalso(["> requieren mayoría absoluta del Congreso\n- Requieren mayoría absoluta"])
        with mock.patch.object(cheap_worker_core, "VARIANTE_PROMPT", "cita_primero"):
            resultado = bulk_read(self.cfg, "¿Qué mayoría?", [path], backend=backend)
        self.assertEqual(resultado, (
            "- Requieren mayoría absoluta\n"
            "  > requieren mayoría absoluta del Congreso\n"
            f"  ({path}:línea 2)"
        ))
```

- [ ] **Step 2: Comprobar que fallan**

Run: `mise exec -- python.exe -m unittest tests.test_verify tests.test_bulk_read`
Expected: `TypeError` por `cita_primero` inesperado.

- [ ] **Step 3: Implementar**

En `cheap_worker_verify.py`, cambiar la firma a `def analizar_respuesta(texto: str, cita_primero: bool = False) -> list:`, añadir al principio del cuerpo:

```python
    if cita_primero:
        return _analizar_cita_primero(texto)
```

y, justo después de `analizar_respuesta`, añadir:

```python
def _analizar_cita_primero(texto):
    """Formato con la cita delante: las líneas `>` respaldan la viñeta siguiente.

    Una cita sin viñeta posterior no respalda nada y se ignora; una viñeta sin
    cita previa queda con cita vacía y se descartará como "sin cita".
    """
    afirmaciones = []
    pendientes = []
    actual = None
    cita_actual = ""
    for linea in texto.splitlines():
        limpia = linea.strip()
        if not limpia:
            continue
        if limpia.startswith(">"):
            if actual is not None:
                afirmaciones.append(Afirmacion(actual.strip(), cita_actual))
                actual = None
            pendientes.append(limpia.lstrip(">").strip())
            continue
        vineta = _VINETA_SIMBOLO.match(linea) or _VINETA_NUMERADA.match(linea)
        if vineta:
            if actual is not None:
                afirmaciones.append(Afirmacion(actual.strip(), cita_actual))
            actual = linea[vineta.end():]
            cita_actual = " ".join(pendientes).strip()
            pendientes = []
        elif actual is not None:
            actual += " " + limpia
    if actual is not None:
        afirmaciones.append(Afirmacion(actual.strip(), cita_actual))
    return afirmaciones
```

Cambiar la firma de `verificar_respuesta` a `def verificar_respuesta(texto, tramos, pregunta="", cita_primero=False):` y su primera línea a `afirmaciones = analizar_respuesta(texto, cita_primero)`.

En `cheap_worker_core.py`, en `bulk_read`, la llamada pasa a:

```python
        buenas, malas = verificar_respuesta(
            respuesta, bloque.tramos, question, cita_primero=VARIANTE_PROMPT == "cita_primero",
        )
```

- [ ] **Step 4: Comprobar**

Run: `mise exec -- python.exe -m unittest tests.test_verify tests.test_bulk_read` → `OK`
Run: `mise exec -- python.exe -m unittest discover -s tests` → `OK`

- [ ] **Step 5: Commit**

```bash
git add cheap_worker_verify.py cheap_worker_core.py tests/test_verify.py tests/test_bulk_read.py
git commit -m "feat: analizar respuestas con la cita antes de la afirmacion

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: La evaluación recorre las variantes

**Files:**
- Modify: `eval-bulk-read.py` (`evaluar`, `resumen`, `main`, docstring)
- Modify: `tests/test_eval.py`

**Interfaces:**
- Consumes: `cheap_worker_core.VARIANTE_PROMPT`, `VARIANTES_PROMPT` (Task 1).
- Produces: `evaluar(modelo, casos, salida, variante)`; `resumen(etiqueta, filas)`; CLI `--variantes` (por defecto, la variante activa).

- [ ] **Step 1: Tests que fallan**

Añadir a `tests/test_eval.py` (con `import json`, `import tempfile` y `from unittest import mock` en los imports):

```python
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
                mock.patch.object(self.ev.core, "VARIANTE_PROMPT", "actual"):
            filas = self.ev.evaluar("modelo:3b", [caso], None, "cita_primero")
        self.assertEqual(vistas, ["cita_primero"])
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
            with mock.patch.object(self.ev, "evaluar", falso):
                self.ev.main([ruta, "--modelos", "a,b", "--variantes", "actual,cita_primero"])
        self.assertEqual(llamadas, [("a", "actual"), ("a", "cita_primero"),
                                    ("b", "actual"), ("b", "cita_primero")])

    def test_variante_desconocida_se_rechaza(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "p.json")
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump([], f)
            with self.assertRaises(SystemExit):
                self.ev.main([ruta, "--modelos", "a", "--variantes", "inventada"])
```

- [ ] **Step 2: Comprobar que fallan**

Run: `mise exec -- python.exe -m unittest tests.test_eval`
Expected: `TypeError` (argumento `variante`) y opción `--variantes` desconocida.

- [ ] **Step 3: Implementar**

En `eval-bulk-read.py`:

`evaluar` pasa a `def evaluar(modelo, casos, salida, variante):`. Al principio del cuerpo, tras construir `cfg`:

```python
    anterior = core.VARIANTE_PROMPT
    core.VARIANTE_PROMPT = variante
    try:
```

y el resto del cuerpo (el bucle y el volcado) queda dentro del `try`, con:

```python
    finally:
        core.VARIANTE_PROMPT = anterior
```

En el volcado, el nombre del archivo pasa a `re.sub(r"[^\w.-]", "_", f"{modelo}__{variante}") + ".md"`.

`resumen(modelo, filas)` pasa a `resumen(etiqueta, filas)`, usando `etiqueta` donde usaba `modelo` y ancho `{etiqueta:34}`.

En `main`, añadir el argumento:

```python
    parser.add_argument("--variantes", default=core.VARIANTE_PROMPT,
                        help="variantes del prompt separadas por comas: " + ", ".join(core.VARIANTES_PROMPT))
```

tras `parse_args`:

```python
    variantes = [v.strip() for v in args.variantes.split(",") if v.strip()]
    desconocidas = [v for v in variantes if v not in core.VARIANTES_PROMPT]
    if desconocidas:
        parser.error("variantes desconocidas: " + ", ".join(desconocidas))
```

y el bucle:

```python
    resumenes = []
    for modelo in [m.strip() for m in args.modelos.split(",") if m.strip()]:
        for variante in variantes:
            print(f"\n== {modelo} [{variante}]", flush=True)
            resumenes.append(resumen(f"{modelo} [{variante}]",
                                     evaluar(modelo, casos, args.salida, variante)))
```

Añadir al docstring del módulo, en el uso: `[--variantes actual,pregunta_al_final,cita_primero]`.

- [ ] **Step 4: Comprobar**

Run: `mise exec -- python.exe -m unittest tests.test_eval` → `OK`
Run: `mise exec -- python.exe eval-bulk-read.py --help` → muestra `--variantes`
Run: `mise exec -- python.exe -m unittest discover -s tests` → `OK`

- [ ] **Step 5: Commit**

```bash
git add eval-bulk-read.py tests/test_eval.py
git commit -m "feat: eval-bulk-read mide cada combinacion de modelo y variante de prompt

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Medición y cierre (sesión principal)

**No la hace un subagente:** usa el corpus real (fuera de git) y la decisión puede requerir al usuario.

- [ ] **Step 1: Medir.** Con `ollama ps` en 8192, en segundo plano:
  `SHUNT_API_BASE=http://localhost:11434/v1 SHUNT_MAX_CTX_TOKENS=8192 SHUNT_MAX_OUTPUT_BULK=512 mise exec -- python.exe eval-bulk-read.py .cache/eval/preguntas.json --modelos gemma3:4b,qwen2.5:3b --variantes actual,pregunta_al_final,cita_primero --salida .cache/eval/respuestas-prompt`
- [ ] **Step 2: Revisar a mano** cada afirmación verificada: correcta y respaldada, verdadera que no responde, falsa. Contar "No consta" correctos, descartes por motivo y tiempo.
- [ ] **Step 3: Decidir.** Gana la variante con más correctas y respaldadas y ninguna falsa; en empate, la más rápida. Si hay falsas en la mejor, o los resultados cambian de ganadora entre modelos, consultar al usuario.
- [ ] **Step 4: Limpiar** (tarea de subagente, con revisión). Dejar `VARIANTE_PROMPT` en la ganadora y eliminar las perdedoras:
  - Borrar de `cheap_worker_core.py` los prompts, recordatorios y ramas de `_mensajes_bulk` de las perdedoras, y dejar `VARIANTES_PROMPT` con solo la ganadora. Si pierde `actual`, el `SYSTEM_BULK` antiguo se elimina y la ganadora pasa a llamarse `SYSTEM_BULK`.
  - Si pierde `cita_primero`, eliminar `_analizar_cita_primero`, el parámetro `cita_primero` de `analizar_respuesta`/`verificar_respuesta` y su uso en `bulk_read`.
  - Ajustar o borrar los tests de las variantes eliminadas; mantener `--variantes` en `eval-bulk-read.py`.
  - Subir `FORMATO_RESPUESTA` a `"citas-verificadas-4"`.
- [ ] **Step 5: README.** Tabla agregada de la medición (sin contenido del corpus) en `### Con documentos: evaluación` y descripción breve del prompt vigente. Commit.
