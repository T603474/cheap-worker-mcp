# bulk_read: modelo distinto para leer código — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que `bulk_read` use `SHUNT_MODEL_BULK_CODE` para archivos de código y `SHUNT_MODEL_BULK` para documentos.

**Architecture:** Nuevo campo y perfil en `Config`; `chunk_files` empaqueta código y documentos en bloques separados marcados con `es_codigo`; `bulk_read` elige perfil por bloque; la clave de caché incluye el modelo de código.

**Tech Stack:** Python 3.14 (mise), `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-16-modelo-lectura-codigo-design.md`

## Global Constraints

- Python solo como `mise exec -- python.exe`. Suite de partida: 257 OK.
- Rama `feat/modelo-lectura-codigo`. Commits terminan con `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. No tocar `.mcp.json`.
- Variable exacta: `SHUNT_MODEL_BULK_CODE`; por defecto, el valor efectivo de `SHUNT_MODEL_BULK`.
- No cambiar el prompt, la verificación ni los filtros de pertinencia.
- Comentarios en español. Editar con herramientas de edición, no heredocs.

---

### Task 1: Modelo de lectura de código

**Files:**
- Modify: `cheap_worker_core.py` (imports, `Config`, `Bloque`, `chunk_files`, `_clave_cache`, `bulk_read`)
- Modify: `mcp-server-cheap-worker.py` (log de arranque)
- Modify: `tests/test_config.py`, `tests/test_chunking.py`, `tests/test_bulk_read.py`, `tests/test_cache.py`, `tests/test_servidor.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `Config.model_bulk_code: str`; `Config.perfil_bulk_code -> Perfil`; `Bloque.es_codigo: bool` (último campo, por defecto `False`); `_clave_cache(perfil, question, bloques, faltan, modelo_codigo)`.

- [ ] **Step 1: Tests que fallan**

`tests/test_config.py`, en `TestConfigFromEnv`:

```python
    def test_modelo_de_lectura_de_codigo_por_defecto_es_el_de_bulk(self):
        cfg = Config.from_env({"SHUNT_MODEL_BULK": "gemma3:4b"})
        self.assertEqual(cfg.model_bulk_code, "gemma3:4b")
        self.assertEqual(Config.from_env({}).model_bulk_code, Config.from_env({}).model_bulk)

    def test_modelo_de_lectura_de_codigo_explicito(self):
        cfg = Config.from_env({"SHUNT_MODEL_BULK": "gemma3:4b",
                               "SHUNT_MODEL_BULK_CODE": "qwen2.5-coder:3b"})
        self.assertEqual(cfg.model_bulk_code, "qwen2.5-coder:3b")
        self.assertEqual(cfg.perfil_bulk_code.modelo, "qwen2.5-coder:3b")
        self.assertEqual(cfg.perfil_bulk_code.salida_max, cfg.perfil_bulk.salida_max)
        self.assertEqual(cfg.perfil_bulk_code.temperatura, cfg.perfil_bulk.temperatura)
        self.assertEqual(cfg.perfil_bulk_code.presupuesto, cfg.perfil_bulk.presupuesto)
```

`tests/test_chunking.py`, en `TestChunkFiles`:

```python
    def test_codigo_y_documentos_van_en_bloques_distintos(self):
        codigo = self._write("a.py", "x = 1\n")
        documento = self._write("b.md", "texto\n")
        result = chunk_files([codigo, documento], budget=1000)
        self.assertEqual(len(result.blocks), 2)
        self.assertEqual([b.es_codigo for b in result.blocks], [False, True])
        self.assertEqual([t.ruta for t in result.blocks[0].tramos], [documento])
        self.assertEqual([t.ruta for t in result.blocks[1].tramos], [codigo])
```

`tests/test_bulk_read.py`, en `TestBulkRead`:

```python
    def test_cada_bloque_usa_el_modelo_de_su_tipo(self):
        cfg = Config.from_env({"SHUNT_MODEL_BULK": "documentos:4b", "SHUNT_MODEL_BULK_CODE": "codigo:3b",
                               "SHUNT_CACHE_MAX": "0"})
        documento = self._write("a.md", "texto del documento\n")
        codigo = self._write("b.py", "x = 1\n")
        backend = BackendFalso(["NO CONSTA", "NO CONSTA"])
        bulk_read(cfg, "¿Qué hay?", [codigo, documento], backend=backend)
        self.assertEqual([l["modelo"] for l in backend.llamadas], ["documentos:4b", "codigo:3b"])
```

`tests/test_cache.py`, en `TestCache` (el archivo de prueba `a.py` es código):

```python
    def test_cambiar_el_modelo_de_lectura_de_codigo_invalida_la_entrada(self):
        bulk_read(self._cfg(SHUNT_MODEL_BULK_CODE="a:3b"), "q", [self.archivo],
                  backend=BackendFalso([respuesta("del modelo a")]))
        otro = bulk_read(self._cfg(SHUNT_MODEL_BULK_CODE="b:3b"), "q", [self.archivo],
                         backend=BackendFalso([respuesta("del modelo b")]))
        self.assertIn("- del modelo b", otro)
```

`tests/test_servidor.py`, en `TestArranqueDelServidor`, junto al test del log de arranque:

```python
    def test_el_log_de_arranque_nombra_el_modelo_de_lectura_de_codigo(self):
        _, _, stderr = hablar({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
                              {"SHUNT_MODEL_BULK_CODE": "codigo:3b"})
        self.assertIn("codigo:3b", stderr)
```

- [ ] **Step 2: Comprobar que fallan**

Run: `mise exec -- python.exe -m unittest tests.test_config tests.test_chunking tests.test_bulk_read tests.test_cache tests.test_servidor`
Expected: `AttributeError`/`TypeError` (`model_bulk_code`, `es_codigo`) y el log sin el modelo.

- [ ] **Step 3: Implementar**

`cheap_worker_core.py`:

**3a.** `from dataclasses import dataclass` → `from dataclasses import dataclass, replace`. En el import de `cheap_worker_extract`, añadir `es_codigo`.

**3b.** En `Config`, tras `model_bulk: str`, añadir `model_bulk_code: str`. Tras `perfil_bulk`:

```python
    @property
    def perfil_bulk_code(self) -> Perfil:
        """perfil_bulk con el modelo para leer código.

        Leer código y leer prosa piden modelos distintos: el mejor con
        documentos en español respondía flojo con código. Ventana, techo y
        temperatura son los mismos, así que el presupuesto también.
        """
        return replace(self.perfil_bulk, modelo=self.model_bulk_code)
```

En `from_env`, antes de `try:`, añadir `modelo_bulk = env.get("SHUNT_MODEL_BULK", modelo)`, y en el constructor:

```python
                model_bulk=modelo_bulk,
                model_bulk_code=env.get("SHUNT_MODEL_BULK_CODE", modelo_bulk),
```

**3c.** `Bloque`, tras `tramos: list`:

```python
    # Código y documentos nunca comparten bloque: cada tipo va a su modelo.
    es_codigo: bool = False
```

**3d.** En `chunk_files`, sustituir la construcción de `blocks` por:

```python
    blocks = []
    for codigo in (False, True):
        del_tipo = [u for u in unidades if es_codigo(u[1].ruta) == codigo]
        blocks.extend(
            Bloque("".join(texto for texto, _ in grupo), [tramo for _, tramo in grupo], codigo)
            for grupo in _agrupar_por_presupuesto(del_tipo, budget)
        )
```

y actualizar su docstring: "Extrae, envuelve y agrupa archivos en bloques que quepan en el presupuesto. Los de código y los de documentos van en bloques separados."

**3e.** `_clave_cache(perfil, question, bloques, faltan, modelo_codigo)`: añadir `modelo_codigo` a `partes` justo después de `perfil.modelo`, y mencionarlo en el docstring ("y el modelo de lectura de código").

**3f.** En `bulk_read`, la llamada a `_clave_cache` pasa `cfg.model_bulk_code` como último argumento, y en el bucle:

```python
        perfil_bloque = cfg.perfil_bulk_code if bloque.es_codigo else perfil
        sistema, usuario = _mensajes_bulk(question, bloque.texto, VARIANTE_PROMPT)
        respuesta = backend.chat(perfil_bloque, sistema, usuario)
```

`mcp-server-cheap-worker.py`, en `main`, tras el bucle que registra los perfiles:

```python
    logger.info(f"bulk_read con código: {cfg.perfil_bulk_code.modelo}")
```

Buscar otros usos de `_clave_cache(` en el repositorio (fuera de `docs/`) y adaptarlos.

**3g.** `README.md`: en la tabla por herramienta, tras la fila de `SHUNT_MODEL_BULK` / `SHUNT_MODEL_CODE`, añadir:

```markdown
| `SHUNT_MODEL_BULK_CODE` | el de `SHUNT_MODEL_BULK` | Modelo de `bulk_read` para archivos de código |
```

y, en `## Elegir modelo`, antes de `### Con documentos: evaluación`, el párrafo:

```markdown
**Código y documentos pueden usar modelos distintos.** `bulk_read` manda los archivos de código a `SHUNT_MODEL_BULK_CODE` y el resto a `SHUNT_MODEL_BULK`, en bloques separados. Un modelo de código (`qwen2.5-coder:3b`) lee bien código y mal prosa; uno generalista (`gemma3:4b`) al revés. Si una consulta mezcla los dos tipos y no caben ambos en la VRAM, Ollama cambia de modelo a mitad y tarda unos segundos más.
```

- [ ] **Step 4: Comprobar**

Run: `mise exec -- python.exe -m unittest tests.test_config tests.test_chunking tests.test_bulk_read tests.test_cache tests.test_servidor` → `OK`
Run: `mise exec -- python.exe -m unittest discover -s tests` → `OK`

- [ ] **Step 5: Commit**

```bash
git add cheap_worker_core.py mcp-server-cheap-worker.py tests/test_config.py tests/test_chunking.py tests/test_bulk_read.py tests/test_cache.py tests/test_servidor.py README.md
git commit -m "feat: bulk_read usa un modelo propio para leer codigo

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Comprobación real y configuración (sesión principal)

- [ ] Con `SHUNT_MODEL_BULK=gemma3:4b` y `SHUNT_MODEL_BULK_CODE=qwen2.5-coder:3b`, lanzar por CLI dos preguntas sobre código del repositorio y una sobre un documento, y revisar las respuestas.
- [ ] Con la aprobación del usuario, añadir `SHUNT_MODEL_BULK_CODE=qwen2.5-coder:3b` a la configuración global de Claude Code y a `claude_desktop_config.json`.
