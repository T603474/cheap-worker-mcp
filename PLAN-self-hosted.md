# Modo self-hosted del MCP shunt — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convertir el servidor MCP en un shunt portable que hable con cualquier backend local OpenAI-compatible, exponiendo `bulk_read` y `code_write` según el contrato del gist.

**Architecture:** Dos módulos. `cheap_worker_core.py` concentra configuración, cliente HTTP, presupuesto de contexto, troceado y los dos workers; no conoce JSON-RPC. `mcp-server-cheap-worker.py` queda como transporte MCP puro; no conoce HTTP ni modelos. Los tests ejercitan `cheap_worker_core` directamente, sin levantar el protocolo.

**Tech Stack:** Python 3.14.6 · `requests` 2.33.0 (ya instalado) · `unittest` de la biblioteca estándar · API OpenAI-compatible (`/chat/completions`).

> **Nota sobre nombres.** Este plan se ejecutó cuando el servidor se llamaba
> `mcp-server-ollama.py` y el núcleo `shunt_core.py`. Ambos se renombraron
> después a `mcp-server-cheap-worker.py` y `cheap_worker_core.py`, y la clave
> del servidor en `.mcp.json` pasó de `shunt` a `cheap-worker`. Los nombres de
> abajo están actualizados para que las rutas sigan siendo utilizables; los
> commits del historial conservan los originales.

## Global Constraints

- **Diseño de referencia:** `DESIGN-self-hosted.md`. Ante discrepancia, manda el spec.
- **Sin dependencias nuevas.** No instalar pytest ni ninguna librería. `requests` y `unittest` bastan.
- **`os.environ` se toca en un solo sitio:** `Config.from_env()`. Ningún otro punto del código lee variables de entorno.
- **stdout es el canal JSON-RPC.** Todo log va a stderr. Un `print()` de depuración rompe el protocolo.
- **Ningún fallo viaja como resultado válido.** Los errores se propagan como excepción y el transporte los convierte en `send_error`.
- **Comandos desde la raíz del proyecto:** `C:\Projects\Cut-AI-Coding-MCP`.
- **Tests:** `python -m unittest discover -s tests -t . -v`
- **Idioma:** comentarios y mensajes de error en español, coherente con el código actual.

> **Estado de partida:** el repositorio ya está inicializado, con un commit base que captura el proyecto tal como estaba antes de este trabajo, y la rama activa es `feat/self-hosted`. Cada tarea commitea encima de esa base. No cambies de rama.

---

### Task 1: Andamiaje y configuración

Crea la estructura de tests y la clase `Config`, único punto que lee el entorno.

**Files:**
- Create: `cheap_worker_core.py`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: nada.
- Produces: `Config` (dataclass congelada) con campos `api_base: str`, `model: str`, `api_key: str`, `max_ctx_tokens: int`, `reserve_tokens: int`, `max_output_tokens: int`, `temp_bulk: float`, `temp_code: float`, `timeout: int`; propiedad `budget_tokens -> int`; constructor de clase `Config.from_env(env: Mapping[str, str] | None = None) -> Config`. Excepciones `ShuntError(Exception)`, `BackendError(ShuntError)`, `BudgetError(ShuntError)`, `InputError(ShuntError)`.

- [ ] **Step 1: Crear el paquete de tests**

Crea `tests/__init__.py` vacío (sin contenido). Es necesario para que `unittest discover` importe los módulos como `tests.test_*` con la raíz del proyecto en `sys.path`.

- [ ] **Step 2: Escribir el test que falla**

Crea `tests/test_config.py`:

```python
import unittest

from cheap_worker_core import Config


class TestConfigFromEnv(unittest.TestCase):
    def test_defaults_cuando_el_entorno_esta_vacio(self):
        cfg = Config.from_env({})
        self.assertEqual(cfg.api_base, "http://localhost:11434/v1")
        self.assertEqual(cfg.model, "qwen2.5-coder:7b")
        self.assertEqual(cfg.api_key, "")
        self.assertEqual(cfg.max_ctx_tokens, 4096)
        self.assertEqual(cfg.reserve_tokens, 1024)
        self.assertEqual(cfg.max_output_tokens, 1024)
        self.assertEqual(cfg.temp_bulk, 0.2)
        self.assertEqual(cfg.temp_code, 0.0)
        self.assertEqual(cfg.timeout, 120)

    def test_budget_resta_la_reserva(self):
        cfg = Config.from_env({"SHUNT_MAX_CTX_TOKENS": "8192", "SHUNT_RESERVE_TOKENS": "2048"})
        self.assertEqual(cfg.budget_tokens, 6144)

    def test_shunt_api_base_se_respeta_tal_cual(self):
        cfg = Config.from_env({"SHUNT_API_BASE": "http://gpu-box:8000/v1"})
        self.assertEqual(cfg.api_base, "http://gpu-box:8000/v1")

    def test_ollama_api_es_alias_y_se_le_anade_v1(self):
        cfg = Config.from_env({"OLLAMA_API": "http://localhost:11434"})
        self.assertEqual(cfg.api_base, "http://localhost:11434/v1")

    def test_ollama_api_que_ya_trae_v1_no_se_duplica(self):
        cfg = Config.from_env({"OLLAMA_API": "http://localhost:11434/v1/"})
        self.assertEqual(cfg.api_base, "http://localhost:11434/v1")

    def test_shunt_api_base_gana_sobre_el_alias(self):
        cfg = Config.from_env(
            {"SHUNT_API_BASE": "http://vllm:8000/v1", "OLLAMA_API": "http://localhost:11434"}
        )
        self.assertEqual(cfg.api_base, "http://vllm:8000/v1")

    def test_valores_numericos_y_de_coma_flotante(self):
        cfg = Config.from_env({"SHUNT_TEMP_BULK": "0.7", "SHUNT_TIMEOUT": "30"})
        self.assertEqual(cfg.temp_bulk, 0.7)
        self.assertEqual(cfg.timeout, 30)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Ejecutar el test y comprobar que falla**

Run: `python -m unittest tests.test_config -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'cheap_worker_core'`

- [ ] **Step 4: Escribir la implementación mínima**

Crea `cheap_worker_core.py`:

```python
#!/usr/bin/env python3
"""
Núcleo del shunt: configuración, backend, presupuesto de contexto y workers.

Este módulo no sabe nada de JSON-RPC ni de MCP. Recibe argumentos Python y
devuelve texto, de modo que puede ejercitarse desde un REPL o desde tests
sin levantar el protocolo.
"""

import ast
import os
from dataclasses import dataclass
from typing import Mapping, Optional

DEFAULT_API_BASE = "http://localhost:11434/v1"
DEFAULT_MODEL = "qwen2.5-coder:7b"


class ShuntError(Exception):
    """Base de los errores propios del shunt."""


class BackendError(ShuntError):
    """Fallo hablando con el backend de inferencia."""


class BudgetError(ShuntError):
    """El contenido no cabe en el presupuesto de contexto."""


class InputError(ShuntError):
    """Argumentos o rutas inválidas."""


def _normalize_base(url: Optional[str]) -> Optional[str]:
    """Normaliza una base de API para que termine exactamente en /v1."""
    if not url:
        return None
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


@dataclass(frozen=True)
class Config:
    api_base: str
    model: str
    api_key: str
    max_ctx_tokens: int
    reserve_tokens: int
    max_output_tokens: int
    temp_bulk: float
    temp_code: float
    timeout: int

    @property
    def budget_tokens(self) -> int:
        """Tokens disponibles para el payload, descontada la reserva."""
        return self.max_ctx_tokens - self.reserve_tokens

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Config":
        """Único punto del proyecto que lee variables de entorno."""
        env = os.environ if env is None else env
        base = (
            _normalize_base(env.get("SHUNT_API_BASE"))
            or _normalize_base(env.get("OLLAMA_API"))
            or DEFAULT_API_BASE
        )
        return cls(
            api_base=base,
            model=env.get("SHUNT_MODEL", DEFAULT_MODEL),
            api_key=env.get("SHUNT_API_KEY", ""),
            max_ctx_tokens=int(env.get("SHUNT_MAX_CTX_TOKENS", "4096")),
            reserve_tokens=int(env.get("SHUNT_RESERVE_TOKENS", "1024")),
            max_output_tokens=int(env.get("SHUNT_MAX_OUTPUT_TOKENS", "1024")),
            temp_bulk=float(env.get("SHUNT_TEMP_BULK", "0.2")),
            temp_code=float(env.get("SHUNT_TEMP_CODE", "0.0")),
            timeout=int(env.get("SHUNT_TIMEOUT", "120")),
        )
```

- [ ] **Step 5: Ejecutar el test y comprobar que pasa**

Run: `python -m unittest tests.test_config -v`
Expected: PASS, 7 tests OK

- [ ] **Step 6: Commit**

```bash
git add cheap_worker_core.py tests/__init__.py tests/test_config.py
git commit -m "feat: Config leida desde entorno con alias OLLAMA_API"
```

---

### Task 2: Troceado y presupuesto (`chunk_files`)

La pieza con más aristas del plan. Envuelve archivos en etiquetas XML, parte los que no caben y agrupa el resto en bloques.

**Files:**
- Modify: `cheap_worker_core.py` (añadir al final)
- Create: `tests/test_chunking.py`

**Interfaces:**
- Consumes: `Config.budget_tokens` de la Tarea 1.
- Produces: `estimate_tokens(text: str) -> int`; `ChunkResult` (dataclass con `blocks: list[str]` y `missing: list[str]`); `chunk_files(paths: list[str], budget: int) -> ChunkResult`; helpers internos `_wrap(path, content, lines=None, part=None, total=None) -> str` y `_agrupar_por_presupuesto(items: list[str], budget: int) -> list[list[str]]`, este último reutilizado por la Tarea 4.

- [ ] **Step 1: Escribir los tests que fallan**

Crea `tests/test_chunking.py`:

```python
import os
import tempfile
import unittest

from cheap_worker_core import ChunkResult, chunk_files, estimate_tokens


class TestChunkFiles(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def _write(self, name, text):
        path = os.path.join(self.dir.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_archivo_pequeno_va_en_un_solo_bloque_envuelto(self):
        path = self._write("a.py", "print('hola')\n")
        result = chunk_files([path], budget=1000)
        self.assertEqual(len(result.blocks), 1)
        self.assertEqual(result.missing, [])
        self.assertIn('<file path="', result.blocks[0])
        self.assertIn("print('hola')", result.blocks[0])
        self.assertIn("</file>", result.blocks[0])

    def test_varios_archivos_que_caben_comparten_bloque(self):
        a = self._write("a.py", "uno\n")
        b = self._write("b.py", "dos\n")
        result = chunk_files([a, b], budget=1000)
        self.assertEqual(len(result.blocks), 1)
        self.assertIn("uno", result.blocks[0])
        self.assertIn("dos", result.blocks[0])

    def test_archivos_que_no_caben_juntos_se_reparten(self):
        # Cada archivo ~250 tokens con la heuristica de caracteres/4.
        a = self._write("a.py", "a" * 1000 + "\n")
        b = self._write("b.py", "b" * 1000 + "\n")
        result = chunk_files([a, b], budget=300)
        self.assertEqual(len(result.blocks), 2)

    def test_archivo_que_desborda_se_parte_con_rangos_de_linea(self):
        # 400 lineas de 40 caracteres = ~4000 tokens, muy por encima del presupuesto.
        path = self._write("grande.py", "".join(f"# linea {i:03d} {'x' * 25}\n" for i in range(400)))
        result = chunk_files([path], budget=500)
        self.assertGreater(len(result.blocks), 1)
        primero = result.blocks[0]
        self.assertIn('lines="1-', primero)
        self.assertIn('part="1/', primero)
        # Los rangos deben ser contiguos y cubrir el archivo entero.
        self.assertIn("# linea 000", result.blocks[0])
        self.assertIn("# linea 399", result.blocks[-1])

    def test_archivo_inexistente_se_reporta_y_no_aborta(self):
        bueno = self._write("bueno.py", "ok\n")
        malo = os.path.join(self.dir.name, "no-existe.py")
        result = chunk_files([bueno, malo], budget=1000)
        self.assertEqual(result.missing, [malo])
        self.assertEqual(len(result.blocks), 1)
        self.assertIn("ok", result.blocks[0])

    def test_todos_los_archivos_faltan_deja_bloques_vacios(self):
        malo = os.path.join(self.dir.name, "no-existe.py")
        result = chunk_files([malo], budget=1000)
        self.assertEqual(result.blocks, [])
        self.assertEqual(result.missing, [malo])

    def test_archivo_vacio_produce_un_envoltorio_valido(self):
        path = self._write("vacio.py", "")
        result = chunk_files([path], budget=1000)
        self.assertEqual(len(result.blocks), 1)
        self.assertIn('<file path="', result.blocks[0])
        self.assertIn("</file>", result.blocks[0])

    def test_estimate_tokens_es_caracteres_entre_cuatro(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertEqual(estimate_tokens("abcde"), 2)

    def test_devuelve_un_chunkresult(self):
        path = self._write("a.py", "x\n")
        self.assertIsInstance(chunk_files([path], budget=1000), ChunkResult)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Ejecutar los tests y comprobar que fallan**

Run: `python -m unittest tests.test_chunking -v`
Expected: FAIL con `ImportError: cannot import name 'ChunkResult' from 'cheap_worker_core'`

- [ ] **Step 3: Escribir la implementación**

Añade al final de `cheap_worker_core.py`:

```python
# Margen fijo, en tokens, para la etiqueta <file ...> que envuelve cada trozo.
_WRAP_OVERHEAD_TOKENS = 40


def estimate_tokens(text: str) -> int:
    """Heurística de caracteres/4. Evita depender de un tokenizador.

    Es una aproximación por arriba en código ASCII y por abajo en texto con
    muchos acentos; SHUNT_RESERVE_TOKENS existe para absorber ese error.
    """
    return (len(text) + 3) // 4


@dataclass
class ChunkResult:
    blocks: list
    missing: list


def _wrap(path, content, lines=None, part=None, total=None) -> str:
    """Envuelve contenido en la etiqueta XML que pide el gist."""
    attrs = f'path="{path}"'
    if lines is not None:
        attrs += f' lines="{lines}"'
    if part is not None:
        attrs += f' part="{part}/{total}"'
    if content and not content.endswith("\n"):
        content += "\n"
    return f"<file {attrs}>\n{content}</file>\n"


def _split_lines_to_budget(lines, budget):
    """Parte una lista de líneas en tramos que quepan en el presupuesto.

    Devuelve tuplas (primera_linea, ultima_linea, texto), numeradas desde 1.
    Una línea individual mayor que el límite se emite sola y desbordada: es un
    caso patológico (ficheros minificados) que no merece más maquinaria.
    """
    limite = max(budget - _WRAP_OVERHEAD_TOKENS, 1)
    tramos = []
    actual = []
    inicio = 1
    tokens = 0
    for numero, linea in enumerate(lines, start=1):
        coste = estimate_tokens(linea)
        if actual and tokens + coste > limite:
            tramos.append((inicio, numero - 1, "".join(actual)))
            actual = []
            inicio = numero
            tokens = 0
        actual.append(linea)
        tokens += coste
    if actual:
        tramos.append((inicio, inicio + len(actual) - 1, "".join(actual)))
    return tramos


def _agrupar_por_presupuesto(items, budget):
    """Agrupa textos en tandas cuyo coste estimado quepa en el presupuesto.

    Algoritmo voraz compartido: lo usan tanto el empaquetado de archivos como
    el plegado de análisis parciales en el paso reduce.
    """
    grupos = []
    actual = []
    tokens = 0
    for item in items:
        coste = estimate_tokens(item)
        if actual and tokens + coste > budget:
            grupos.append(actual)
            actual = []
            tokens = 0
        actual.append(item)
        tokens += coste
    if actual:
        grupos.append(actual)
    return grupos


def chunk_files(paths, budget) -> ChunkResult:
    """Lee, envuelve y agrupa archivos en bloques que quepan en el presupuesto."""
    missing = []
    unidades = []

    for path in paths:
        if not os.path.isfile(path):
            missing.append(path)
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            missing.append(path)
            continue

        entero = _wrap(path, "".join(lines))
        if estimate_tokens(entero) <= budget:
            unidades.append(entero)
            continue

        tramos = _split_lines_to_budget(lines, budget)
        total = len(tramos)
        for indice, (primera, ultima, texto) in enumerate(tramos, start=1):
            unidades.append(_wrap(path, texto, lines=f"{primera}-{ultima}", part=indice, total=total))

    blocks = ["".join(grupo) for grupo in _agrupar_por_presupuesto(unidades, budget)]
    return ChunkResult(blocks=blocks, missing=missing)
```

- [ ] **Step 4: Ejecutar los tests y comprobar que pasan**

Run: `python -m unittest tests.test_chunking -v`
Expected: PASS, 9 tests OK

- [ ] **Step 5: Commit**

```bash
git add cheap_worker_core.py tests/test_chunking.py
git commit -m "feat: troceado de archivos por presupuesto de contexto con etiquetas XML"
```

---

### Task 3: Cliente del backend OpenAI-compatible

**Files:**
- Modify: `cheap_worker_core.py` (añadir al final)
- Create: `tests/test_backend.py`

**Interfaces:**
- Consumes: `Config`, `BackendError` de la Tarea 1.
- Produces: `Backend(cfg: Config, session=None)` con método `chat(system: str, user: str, temperature: float) -> str`. El parámetro `session` acepta cualquier objeto con `.post(url, json=..., headers=..., timeout=...)`; por defecto usa el módulo `requests`.

- [ ] **Step 1: Escribir los tests que fallan**

Crea `tests/test_backend.py`:

```python
import unittest

from cheap_worker_core import Backend, BackendError, Config


class RespuestaFalsa:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no es JSON")
        return self._payload


class SesionFalsa:
    def __init__(self, respuesta=None, excepcion=None):
        self.respuesta = respuesta
        self.excepcion = excepcion
        self.llamadas = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.llamadas.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.excepcion:
            raise self.excepcion
        return self.respuesta


def _ok(contenido="bullets"):
    return RespuestaFalsa(payload={"choices": [{"message": {"content": contenido}}]})


class TestBackend(unittest.TestCase):
    def setUp(self):
        self.cfg = Config.from_env({"SHUNT_MODEL": "m1", "SHUNT_MAX_OUTPUT_TOKENS": "512"})

    def test_construye_la_url_de_chat_completions(self):
        sesion = SesionFalsa(_ok())
        Backend(self.cfg, session=sesion).chat("sys", "usr", 0.2)
        self.assertEqual(sesion.llamadas[0]["url"], "http://localhost:11434/v1/chat/completions")

    def test_el_payload_lleva_modelo_temperatura_y_max_tokens(self):
        sesion = SesionFalsa(_ok())
        Backend(self.cfg, session=sesion).chat("sys", "usr", 0.2)
        payload = sesion.llamadas[0]["json"]
        self.assertEqual(payload["model"], "m1")
        self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(payload["max_tokens"], 512)
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "usr"})

    def test_sin_api_key_no_manda_cabecera_de_autorizacion(self):
        sesion = SesionFalsa(_ok())
        Backend(self.cfg, session=sesion).chat("sys", "usr", 0.2)
        self.assertNotIn("Authorization", sesion.llamadas[0]["headers"])

    def test_con_api_key_manda_bearer(self):
        cfg = Config.from_env({"SHUNT_API_KEY": "secreto"})
        sesion = SesionFalsa(_ok())
        Backend(cfg, session=sesion).chat("sys", "usr", 0.2)
        self.assertEqual(sesion.llamadas[0]["headers"]["Authorization"], "Bearer secreto")

    def test_devuelve_el_contenido_del_mensaje_sin_espacios(self):
        sesion = SesionFalsa(_ok("  resultado  "))
        self.assertEqual(Backend(self.cfg, session=sesion).chat("s", "u", 0.2), "resultado")

    def test_fallo_de_conexion_lanza_backenderror_con_la_url(self):
        sesion = SesionFalsa(excepcion=OSError("conexion rechazada"))
        with self.assertRaises(BackendError) as ctx:
            Backend(self.cfg, session=sesion).chat("s", "u", 0.2)
        self.assertIn("chat/completions", str(ctx.exception))

    def test_http_no_2xx_lanza_backenderror(self):
        sesion = SesionFalsa(RespuestaFalsa(status_code=500, text="boom"))
        with self.assertRaises(BackendError) as ctx:
            Backend(self.cfg, session=sesion).chat("s", "u", 0.2)
        self.assertIn("500", str(ctx.exception))

    def test_respuesta_con_forma_inesperada_lanza_backenderror(self):
        sesion = SesionFalsa(RespuestaFalsa(payload={"sin": "choices"}))
        with self.assertRaises(BackendError):
            Backend(self.cfg, session=sesion).chat("s", "u", 0.2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Ejecutar los tests y comprobar que fallan**

Run: `python -m unittest tests.test_backend -v`
Expected: FAIL con `ImportError: cannot import name 'Backend' from 'cheap_worker_core'`

- [ ] **Step 3: Escribir la implementación**

Añade `import requests` junto a los demás imports en la cabecera de `cheap_worker_core.py`, y esto al final del archivo:

```python
class Backend:
    """Único punto que conoce la forma HTTP/JSON de la API OpenAI-compatible."""

    def __init__(self, cfg: Config, session=None):
        self.cfg = cfg
        self._session = session if session is not None else requests

    def chat(self, system: str, user: str, temperature: float) -> str:
        url = f"{self.cfg.api_base}/chat/completions"
        payload = {
            "model": self.cfg.model,
            "temperature": temperature,
            "max_tokens": self.cfg.max_output_tokens,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"

        try:
            respuesta = self._session.post(
                url, json=payload, headers=headers, timeout=self.cfg.timeout
            )
        except Exception as e:
            raise BackendError(f"No se pudo conectar con {url}: {e}") from e

        if respuesta.status_code // 100 != 2:
            raise BackendError(
                f"{url} devolvió HTTP {respuesta.status_code}: {respuesta.text[:200]}"
            )

        try:
            return respuesta.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            raise BackendError(f"Respuesta inesperada de {url}: {e}") from e
```

- [ ] **Step 4: Ejecutar los tests y comprobar que pasan**

Run: `python -m unittest tests.test_backend -v`
Expected: PASS, 8 tests OK

- [ ] **Step 5: Commit**

```bash
git add cheap_worker_core.py tests/test_backend.py
git commit -m "feat: cliente OpenAI-compatible con errores tipados"
```

---

### Task 4: Worker `bulk_read` con map-reduce

**Files:**
- Modify: `cheap_worker_core.py` (añadir al final)
- Create: `tests/helpers.py`
- Create: `tests/test_bulk_read.py`

**Interfaces:**
- Consumes: `Config`, `Backend`, `chunk_files`, `estimate_tokens`, `_agrupar_por_presupuesto`, `BudgetError` de las tareas 1–3. **No reimplementes la agrupación por presupuesto**: la Tarea 2 ya definió `_agrupar_por_presupuesto` y el plegado del reduce la reutiliza.
- Produces: `bulk_read(cfg: Config, question: str, paths: list[str], backend=None) -> str`. El parámetro `backend` acepta cualquier objeto con `chat(system, user, temperature) -> str`. Constantes `SYSTEM_BULK` y `SYSTEM_REDUCE`. Además `tests/helpers.BackendFalso`, que reutiliza la Tarea 5.

- [ ] **Step 1: Crear el doble de test compartido**

Crea `tests/helpers.py`:

```python
"""Dobles de test compartidos entre los módulos de prueba."""


class BackendFalso:
    """Backend de mentira: sirve respuestas predefinidas y registra las llamadas.

    `respuestas` es siempre una lista, incluso para una sola respuesta, para que
    todas las llamadas tengan la misma forma.
    """

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []

    def chat(self, system, user, temperature):
        self.llamadas.append({"system": system, "user": user, "temperature": temperature})
        return self.respuestas.pop(0)
```

- [ ] **Step 2: Escribir los tests que fallan**

Crea `tests/test_bulk_read.py`:

```python
import os
import tempfile
import unittest

from cheap_worker_core import BudgetError, Config, bulk_read
from tests.helpers import BackendFalso


class TestBulkRead(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cfg = Config.from_env({"SHUNT_MAX_CTX_TOKENS": "4096", "SHUNT_RESERVE_TOKENS": "1024"})

    def _write(self, name, text):
        path = os.path.join(self.dir.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_un_solo_bloque_hace_una_sola_llamada_y_no_reduce(self):
        path = self._write("a.py", "def f(): pass\n")
        backend = BackendFalso(["- f(): función, línea 1"])
        resultado = bulk_read(self.cfg, "¿Qué define?", [path], backend=backend)
        self.assertEqual(len(backend.llamadas), 1)
        self.assertEqual(resultado, "- f(): función, línea 1")

    def test_la_pregunta_y_el_payload_llegan_al_modelo(self):
        path = self._write("a.py", "def f(): pass\n")
        backend = BackendFalso(["ok"])
        bulk_read(self.cfg, "¿Qué define?", [path], backend=backend)
        user = backend.llamadas[0]["user"]
        self.assertIn("¿Qué define?", user)
        self.assertIn("def f(): pass", user)
        self.assertIn('<file path="', user)

    def test_usa_la_temperatura_bulk(self):
        path = self._write("a.py", "x\n")
        backend = BackendFalso(["ok"])
        bulk_read(self.cfg, "q", [path], backend=backend)
        self.assertEqual(backend.llamadas[0]["temperature"], 0.2)

    def test_varios_bloques_disparan_una_llamada_de_reduce(self):
        cfg = Config.from_env({"SHUNT_MAX_CTX_TOKENS": "600", "SHUNT_RESERVE_TOKENS": "100"})
        a = self._write("a.py", "a" * 1600 + "\n")
        b = self._write("b.py", "b" * 1600 + "\n")
        backend = BackendFalso(["parcial A", "parcial B", "fusion"])
        resultado = bulk_read(cfg, "q", [a, b], backend=backend)
        self.assertEqual(len(backend.llamadas), 3)
        self.assertEqual(resultado, "fusion")
        reduce_user = backend.llamadas[-1]["user"]
        self.assertIn("parcial A", reduce_user)
        self.assertIn("parcial B", reduce_user)

    def test_los_archivos_que_faltan_se_anexan_a_la_respuesta(self):
        bueno = self._write("bueno.py", "x\n")
        malo = os.path.join(self.dir.name, "fantasma.py")
        backend = BackendFalso(["analisis"])
        resultado = bulk_read(self.cfg, "q", [bueno, malo], backend=backend)
        self.assertIn("analisis", resultado)
        self.assertIn("fantasma.py", resultado)
        self.assertIn("no encontrados", resultado.lower())

    def test_si_no_hay_ningun_archivo_legible_lanza_budgeterror(self):
        malo = os.path.join(self.dir.name, "fantasma.py")
        backend = BackendFalso([])
        with self.assertRaises(BudgetError):
            bulk_read(self.cfg, "q", [malo], backend=backend)

    def test_si_el_plegado_no_avanza_lanza_budgeterror(self):
        cfg = Config.from_env({"SHUNT_MAX_CTX_TOKENS": "600", "SHUNT_RESERVE_TOKENS": "100"})
        a = self._write("a.py", "a" * 1600 + "\n")
        b = self._write("b.py", "b" * 1600 + "\n")
        # Cada parcial ocupa ~1000 tokens, muy por encima del presupuesto de 500,
        # así que ninguna pasada del plegado puede combinarlos.
        backend = BackendFalso(["a" * 4000, "b" * 4000])
        with self.assertRaises(BudgetError):
            bulk_read(cfg, "q", [a, b], backend=backend)
        # Los dos map se hicieron; ningún reduce llegó a lanzarse.
        self.assertEqual(len(backend.llamadas), 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Ejecutar los tests y comprobar que fallan**

Run: `python -m unittest tests.test_bulk_read -v`
Expected: FAIL con `ImportError: cannot import name 'bulk_read' from 'cheap_worker_core'`

- [ ] **Step 4: Escribir la implementación**

Añade al final de `cheap_worker_core.py`:

```python
# Prompt del analista, literal del gist.
SYSTEM_BULK = (
    "You are a precise code analyst. Read the provided files and answer the question concisely.\n"
    "Output structured bullets only. No greetings, no prose. Lead every bullet with exact "
    "name/type/line.\n"
    "Use nested bullets for details. Skip anything not asked."
)

# Prompt de fusión. No está en el gist: hace falta para el paso reduce.
SYSTEM_REDUCE = (
    "You merge several partial analyses of the same codebase into one answer.\n"
    "Output structured bullets only. Remove duplicates and keep exact names and line numbers.\n"
    "Do not add information that is not present in the partial analyses."
)

# Cota dura de pasadas de reduce, por si el plegado no converge.
_MAX_REDUCE_PASADAS = 5


def _prompt_reduce(question, parciales):
    unidas = "\n\n".join(
        f'<partial n="{i}">\n{p}\n</partial>' for i, p in enumerate(parciales, start=1)
    )
    return f"Question: {question}\n\nPartial analyses:\n{unidas}"


def _reduce(cfg, backend, question, parciales):
    """Pliega los análisis parciales hasta dejar uno solo.

    Si una pasada no agrupa nada, cada llamada se limitaría a reescribir un
    parcial aislado, que no es para lo que existe el plegado: se aborta en vez
    de gastar llamadas y acabar devolviendo parciales sin fusionar como si
    fueran una respuesta buena.
    """
    actuales = list(parciales)
    for _ in range(_MAX_REDUCE_PASADAS):
        grupos = _agrupar_por_presupuesto(actuales, cfg.budget_tokens)
        if len(grupos) == len(actuales):
            raise BudgetError(
                f"El plegado no avanza: {len(actuales)} análisis parciales que no caben "
                f"juntos en {cfg.budget_tokens} tokens. Sube SHUNT_MAX_CTX_TOKENS o baja "
                "SHUNT_MAX_OUTPUT_TOKENS."
            )
        actuales = [
            backend.chat(SYSTEM_REDUCE, _prompt_reduce(question, grupo), cfg.temp_bulk)
            for grupo in grupos
        ]
        if len(actuales) == 1:
            return actuales[0]
    raise BudgetError(
        f"El plegado no convergió en {_MAX_REDUCE_PASADAS} pasadas; quedan "
        f"{len(actuales)} análisis parciales sin fusionar."
    )


def bulk_read(cfg: Config, question: str, paths, backend=None) -> str:
    """Analiza archivos con el modelo barato. El frontier nunca ve su contenido."""
    backend = backend if backend is not None else Backend(cfg)
    troceado = chunk_files(paths, cfg.budget_tokens)

    if not troceado.blocks:
        raise BudgetError("Ningún archivo legible en: " + ", ".join(paths))

    parciales = [
        backend.chat(SYSTEM_BULK, f"Question: {question}\n\nFiles:\n{bloque}", cfg.temp_bulk)
        for bloque in troceado.blocks
    ]

    respuesta = parciales[0] if len(parciales) == 1 else _reduce(cfg, backend, question, parciales)

    if troceado.missing:
        respuesta += "\n\nArchivos no encontrados: " + ", ".join(troceado.missing)
    return respuesta
```

- [ ] **Step 5: Ejecutar los tests y comprobar que pasan**

Run: `python -m unittest tests.test_bulk_read -v`
Expected: PASS, 7 tests OK

- [ ] **Step 6: Commit**

```bash
git add cheap_worker_core.py tests/helpers.py tests/test_bulk_read.py
git commit -m "feat: bulk_read con map-reduce sobre bloques que caben en contexto"
```

---

### Task 5: Worker `code_write`

**Files:**
- Modify: `cheap_worker_core.py` (añadir al final)
- Create: `tests/test_code_write.py`

**Interfaces:**
- Consumes: `Config`, `Backend`, `_wrap`, `estimate_tokens`, `BudgetError`, `InputError` de las tareas 1–3, y el doble `tests.helpers.BackendFalso` creado en la Tarea 4. **No redefinas el doble**: impórtalo, y pásale siempre una lista de respuestas aunque sea de un solo elemento.
- Produces: `code_write(cfg: Config, spec: str, reference: str, target: str = "", backend=None) -> str`. Constante `SYSTEM_CODE`. Helper `_strip_fences(text: str) -> str`.

- [ ] **Step 1: Escribir los tests que fallan**

Crea `tests/test_code_write.py`:

```python
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
        cfg = Config.from_env({"SHUNT_MAX_CTX_TOKENS": "200", "SHUNT_RESERVE_TOKENS": "100"})
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
```

- [ ] **Step 2: Ejecutar los tests y comprobar que fallan**

Run: `python -m unittest tests.test_code_write -v`
Expected: FAIL con `ImportError: cannot import name 'code_write' from 'cheap_worker_core'`

- [ ] **Step 3: Escribir la implementación**

Añade al final de `cheap_worker_core.py`:

```python
# Prompt del generador, literal del gist.
SYSTEM_CODE = (
    "You generate code files based on a spec and reference files. Match existing patterns, "
    "conventions, naming, style exactly.\n"
    "Output only the code — no explanations, no markdown fences unless asked.\n"
    "If ambiguous, choose what matches reference."
)


def _strip_fences(text: str) -> str:
    """Extrae el código de una respuesta envuelta en un bloque markdown.

    Se toma lo que hay entre la primera y la última valla, de modo que una valla
    legítima dentro de un docstring sobrevive. Si fuera del bloque queda texto
    que no sea espacio en blanco, se aborta: el modelo añadió explicaciones pese
    al prompt, y escribirlas en el archivo destino como si fueran código es peor
    que fallar.

    Solo se considera envoltorio si la respuesta **empieza** por valla. Una valla
    en cualquier otra posición es contenido, no marcado: un docstring que
    documenta markdown, por ejemplo, y puede haber varias en el mismo archivo.
    Lo que en ese caso no sea código lo atrapa después `_validar_python`.
    """
    lineas = text.strip().splitlines()
    vallas = [i for i, linea in enumerate(lineas) if linea.lstrip().startswith("```")]

    if not vallas or vallas[0] != 0:
        return text.strip()

    if len(vallas) == 1:
        return "\n".join(lineas[1:]).strip()

    ultima = vallas[-1]
    sobrante = "\n".join(lineas[ultima + 1:]).strip()
    if sobrante:
        raise InputError(
            "El modelo devolvió texto fuera del bloque de código, pese a que el "
            f"prompt pide solo código: {sobrante[:120]!r}"
        )
    return "\n".join(lineas[1:ultima]).strip()


def _validar_python(codigo: str, target: str) -> None:
    """Comprueba que lo devuelto por el modelo es Python utilizable.

    Es la red que no puede tejer el recuento de vallas: prosa colada, vallas
    sueltas y sobre todo el archivo cortado a media función por un
    SHUNT_MAX_OUTPUT_TOKENS corto fallan aquí, en vez de acabar en disco
    reportados como éxito.

    Compilar no basta por sí solo: una coletilla como "Done" o "Listo, gracias"
    es una expresión válida y pasaría el filtro. Por eso se recorren las
    sentencias del módulo y se rechaza cualquier expresión desnuda que no sea
    una llamada, salvo el docstring inicial. Se enumera lo válido y no lo
    inválido: enumerar formas malas siempre deja una fuera.

    Se parsea y además se compila el árbol: `ast.parse` por sí solo no ejecuta
    el paso de tabla de símbolos, así que deja pasar `return` fuera de función,
    `break` fuera de bucle o `nonlocal` a nivel de módulo. `compile` acepta un
    AST ya parseado, de modo que no hay que recorrer el fuente dos veces.
    """
    if not codigo.strip():
        raise InputError(
            "El modelo devolvió una respuesta vacía: no hay nada que escribir. "
            "Probablemente se agotó la salida: sube SHUNT_MAX_OUTPUT_TOKENS."
        )

    nombre = target or "<generado>"
    try:
        arbol = ast.parse(codigo, nombre)
        compile(arbol, nombre, "exec")
    except (SyntaxError, ValueError) as e:
        detalle = f"{e.msg}, línea {e.lineno}" if isinstance(e, SyntaxError) else str(e)
        raise InputError(
            f"El modelo no devolvió Python válido ({detalle}). Puede ser prosa colada "
            "o salida truncada: sube SHUNT_MAX_OUTPUT_TOKENS o usa un modelo mejor."
        ) from e

    if not arbol.body:
        # Hay texto pero ninguna sentencia: un archivo de solo comentarios.
        # Es válido y no tiene sentencias que inspeccionar.
        return

    for indice, sentencia in enumerate(arbol.body):
        if not isinstance(sentencia, ast.Expr):
            continue

        valor = sentencia.value

        # Una llamada suelta es legítima en cualquier posición: `main()`,
        # `app.run()`, `logging.basicConfig(...)`.
        if isinstance(valor, ast.Call):
            continue

        # Y una cadena suelta es legítima si es el docstring del módulo, lo que
        # significa estar en la primera posición.
        if indice == 0 and isinstance(valor, ast.Constant) and isinstance(valor.value, str):
            continue

        raise InputError(
            f"El código generado contiene una expresión suelta ({ast.unparse(valor)[:60]!r}) "
            f"en la línea {sentencia.lineno}: parece una coletilla del modelo mezclada "
            "con el código."
        )


def code_write(cfg: Config, spec: str, reference: str, target: str = "", backend=None) -> str:
    """Genera código copiando el patrón de una referencia obligatoria.

    Con `target`, escribe el archivo y devuelve solo ruta y número de líneas:
    el modelo frontier nunca ve el código generado, que es donde está el ahorro.
    """
    backend = backend if backend is not None else Backend(cfg)

    if not os.path.isfile(reference):
        raise InputError(f"Referencia no encontrada: {reference}")

    try:
        with open(reference, "r", encoding="utf-8", errors="replace") as f:
            contenido = f.read()
    except OSError as e:
        raise InputError(f"No se pudo leer la referencia {reference}: {e}") from e

    envuelta = _wrap(reference, contenido)
    coste = estimate_tokens(envuelta)
    if coste > cfg.budget_tokens:
        raise BudgetError(
            f"La referencia {reference} ocupa ~{coste} tokens y el presupuesto es "
            f"{cfg.budget_tokens}. Usa una referencia más pequeña o sube SHUNT_MAX_CTX_TOKENS."
        )

    codigo = _strip_fences(
        backend.chat(SYSTEM_CODE, f"Spec: {spec}\n\nReference:\n{envuelta}", cfg.temp_code)
    )
    _validar_python(codigo, target)

    if not target:
        return codigo

    try:
        carpeta = os.path.dirname(target)
        if carpeta:
            os.makedirs(carpeta, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(codigo)
    except OSError as e:
        raise InputError(f"No se pudo escribir {target}: {e}") from e

    return f"{target} ({len(codigo.splitlines())} líneas)"
```

- [ ] **Step 4: Ejecutar los tests y comprobar que pasan**

Run: `python -m unittest tests.test_code_write -v`
Expected: PASS, 28 tests OK

- [ ] **Step 5: Ejecutar la suite completa**

Run: `python -m unittest discover -s tests -t . -v`
Expected: PASS, 59 tests OK

- [ ] **Step 6: Commit**

```bash
git add cheap_worker_core.py tests/test_code_write.py
git commit -m "feat: code_write con referencia obligatoria y salida por ruta"
```

---

### Task 6: Transporte MCP

Reescribe `mcp-server-cheap-worker.py` para que solo hable JSON-RPC y delegue en `cheap_worker_core`.

**Files:**
- Modify: `mcp-server-cheap-worker.py` (reescritura completa)

**Interfaces:**
- Consumes: `cheap_worker_core.Config`, `cheap_worker_core.bulk_read`, `cheap_worker_core.code_write`, `cheap_worker_core.ShuntError`.
- Produces: servidor MCP que declara `bulk_read` y `code_write` por `tools/list` y los ejecuta por `tools/call`.

- [ ] **Step 1: Reescribir el archivo completo**

Sustituye todo el contenido de `mcp-server-cheap-worker.py` por:

```python
#!/usr/bin/env python3
"""
Servidor MCP del shunt - v5

Transporte puro: lee JSON-RPC de stdin, declara las herramientas y delega en
cheap_worker_core. No conoce HTTP ni modelos.
"""

import sys
import json
import logging
from typing import Any, Dict

import cheap_worker_core

# El logging va a stderr sin excepción: stdout es el canal JSON-RPC.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


def send_response(message_id: Any, result: Dict[str, Any]) -> None:
    """Envía respuesta MCP al cliente."""
    print(json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result}), flush=True)
    logger.info(f"Respuesta enviada - ID: {message_id}")


def send_error(message_id: Any, error_msg: str) -> None:
    """Envía error MCP al cliente."""
    print(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": -32603, "message": error_msg},
            }
        ),
        flush=True,
    )
    logger.error(f"Error enviado - ID: {message_id} - {error_msg}")


def handle_initialize(message_id: Any) -> None:
    """Maneja inicialización del servidor MCP."""
    logger.info("Procesando initialize")
    send_response(
        message_id,
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "shunt-mcp-server", "version": "5.0.0"},
        },
    )


def handle_tools_list(message_id: Any) -> None:
    """Lista herramientas disponibles."""
    logger.info("Procesando tools/list")
    send_response(
        message_id,
        {
            "tools": [
                {
                    "name": "bulk_read",
                    "description": (
                        "OBLIGATORIO para archivos de más de 350 líneas. Lee los archivos en "
                        "el servidor con un modelo local y devuelve bullets concisos. Úsalo en "
                        "lugar de leer el archivo directamente."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "Pregunta sobre el código",
                            },
                            "paths": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Rutas de los archivos a analizar",
                            },
                        },
                        "required": ["question", "paths"],
                    },
                },
                {
                    "name": "code_write",
                    "description": (
                        "Genera código copiando el patrón de un archivo de referencia. Con "
                        "'target' escribe el archivo y devuelve solo la ruta, sin gastar "
                        "contexto en el código generado."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "spec": {"type": "string", "description": "Qué generar"},
                            "reference": {
                                "type": "string",
                                "description": "Archivo cuyo patrón se copia (obligatorio)",
                            },
                            "target": {
                                "type": "string",
                                "description": "Dónde guardar (opcional)",
                            },
                        },
                        "required": ["spec", "reference"],
                    },
                },
            ]
        },
    )


def handle_tools_call(message_id: Any, name: str, arguments: Dict[str, Any]) -> None:
    """Ejecuta una herramienta y traduce las excepciones a errores MCP."""
    logger.info(f"Ejecutando herramienta: {name}")
    try:
        cfg = cheap_worker_core.Config.from_env()

        if name == "bulk_read":
            question = arguments.get("question", "")
            paths = arguments.get("paths", [])
            if not question or not paths:
                send_error(message_id, "Faltan 'question' o 'paths'")
                return
            resultado = cheap_worker_core.bulk_read(cfg, question, paths)

        elif name == "code_write":
            spec = arguments.get("spec", "")
            reference = arguments.get("reference", "")
            if not spec or not reference:
                send_error(message_id, "Faltan 'spec' o 'reference'")
                return
            resultado = cheap_worker_core.code_write(cfg, spec, reference, arguments.get("target", ""))

        else:
            send_error(message_id, f"Herramienta desconocida: {name}")
            return

        send_response(message_id, {"content": [{"type": "text", "text": resultado}]})

    except cheap_worker_core.ShuntError as e:
        logger.error(f"{name}: {e}")
        send_error(message_id, str(e))
    except Exception as e:
        logger.exception(f"Error inesperado en {name}")
        send_error(message_id, f"Error interno: {e}")


def process_message(line: str) -> None:
    """Procesa un mensaje JSON del cliente."""
    try:
        request = json.loads(line)
        message_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        logger.info(f"Método: {method} | ID: {message_id}")

        if method == "initialize":
            handle_initialize(message_id)
        elif method == "tools/list":
            handle_tools_list(message_id)
        elif method == "tools/call":
            handle_tools_call(message_id, params.get("name"), params.get("arguments", {}))
        elif method and method.startswith("notifications/"):
            logger.info(f"Notificación ignorada: {method}")
        elif message_id is not None:
            send_error(message_id, f"Método no soportado: {method}")

    except json.JSONDecodeError as e:
        logger.error(f"JSON inválido: {e}")
    except Exception as e:
        logger.error(f"Error procesando: {e}")


def main():
    """Servidor MCP principal."""
    cfg = cheap_worker_core.Config.from_env()
    logger.info("=== Servidor MCP shunt v5 iniciado ===")
    logger.info(f"Backend: {cfg.api_base}")
    logger.info(f"Modelo: {cfg.model}")
    logger.info(f"Presupuesto: {cfg.budget_tokens} tokens")

    try:
        for line in sys.stdin:
            line = line.strip()
            if line:
                process_message(line)
        logger.info("stdin cerrado, saliendo")
    except KeyboardInterrupt:
        logger.info("Detenido por usuario")
    except Exception as e:
        logger.error(f"Error fatal: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Comprobar `initialize` a mano**

Run:
```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python mcp-server-cheap-worker.py
```
Expected: una línea JSON en stdout que contiene `"capabilities": {"tools": {}}` y `"name": "shunt-mcp-server"`. Los logs salen por stderr, separados.

- [ ] **Step 3: Comprobar `tools/list` a mano**

Run:
```bash
echo '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | python mcp-server-cheap-worker.py
```
Expected: JSON con exactamente dos herramientas, `bulk_read` y `code_write`. No debe aparecer `analyze_code` ni `generate_code`.

- [ ] **Step 4: Comprobar que un backend caído produce error, no un resultado falso**

Run:
```bash
SHUNT_API_BASE=http://localhost:9/v1 bash -c 'echo "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"bulk_read\",\"arguments\":{\"question\":\"q\",\"paths\":[\"cheap_worker_core.py\"]}}}" | python mcp-server-cheap-worker.py'
```
Expected: la respuesta lleva la clave `"error"`, **no** `"result"`. Este es el fallo número 4 del spec: antes el mensaje de conexión viajaba como si fuera el análisis.

- [ ] **Step 5: Commit**

```bash
git add mcp-server-cheap-worker.py
git commit -m "refactor: transporte MCP puro con bulk_read y code_write"
```

---

### Task 7: Modo línea de comandos

`test-mcp-ollama.ps1:148` y el README invocan `python mcp-server-cheap-worker.py bulk_read "<pregunta>" "<rutas>"`, una interfaz que nunca existió: `main()` ignora `sys.argv` y se queda leyendo stdin. Sin esto, la prueba extremo a extremo de la Tarea 8 no puede ejecutarse.

**Files:**
- Modify: `mcp-server-cheap-worker.py` (añadir `run_cli`, modificar `main`)

**Interfaces:**
- Consumes: `cheap_worker_core.Config`, `cheap_worker_core.bulk_read`, `cheap_worker_core.code_write`, `cheap_worker_core.ShuntError`.
- Produces: `run_cli(argv: list[str]) -> int`, que devuelve el código de salida del proceso.

- [ ] **Step 1: Añadir `run_cli` antes de `main`**

Inserta en `mcp-server-cheap-worker.py`, justo antes de la definición de `main()`:

```python
def run_cli(argv) -> int:
    """Modo línea de comandos, tal como lo documentan el README y test-mcp-ollama.ps1.

    A diferencia del modo MCP, aquí stdout lleva el resultado en texto plano y los
    errores salen por stderr con código de salida distinto de cero.
    """
    cfg = cheap_worker_core.Config.from_env()
    comando = argv[0]

    try:
        if comando == "bulk_read":
            if len(argv) != 3:
                print('Uso: bulk_read "<pregunta>" "<ruta1|ruta2>"', file=sys.stderr)
                return 2
            paths = [p for p in argv[2].split("|") if p]
            print(cheap_worker_core.bulk_read(cfg, argv[1], paths))

        elif comando == "code_write":
            if len(argv) not in (3, 4):
                print('Uso: code_write "<spec>" "<referencia>" ["<destino>"]', file=sys.stderr)
                return 2
            destino = argv[3] if len(argv) == 4 else ""
            print(cheap_worker_core.code_write(cfg, argv[1], argv[2], destino))

        else:
            print(f"Comando desconocido: {comando}", file=sys.stderr)
            print("Comandos: bulk_read, code_write", file=sys.stderr)
            return 2

    except cheap_worker_core.ShuntError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    return 0
```

- [ ] **Step 2: Derivar a CLI desde `main`**

En `mcp-server-cheap-worker.py`, sustituye las tres primeras líneas del cuerpo de `main()`:

```python
def main():
    """Servidor MCP principal."""
    cfg = cheap_worker_core.Config.from_env()
```

por:

```python
def main():
    """Servidor MCP principal, o CLI si recibe argumentos."""
    if len(sys.argv) > 1:
        sys.exit(run_cli(sys.argv[1:]))

    cfg = cheap_worker_core.Config.from_env()
```

- [ ] **Step 3: Comprobar que un comando desconocido sale con código 2**

Run:
```bash
python mcp-server-cheap-worker.py inventado; echo "exit=$?"
```
Expected: `Comando desconocido: inventado` por stderr y `exit=2`.

- [ ] **Step 4: Comprobar que un backend caído sale con código 1**

Run:
```bash
SHUNT_API_BASE=http://localhost:9/v1 python mcp-server-cheap-worker.py bulk_read "que hace" "cheap_worker_core.py"; echo "exit=$?"
```
Expected: una línea que empieza por `ERROR:` y menciona `localhost:9`, y `exit=1`. Nada en stdout.

- [ ] **Step 5: Comprobar que el modo MCP sigue intacto**

Run:
```bash
echo '{"jsonrpc":"2.0","id":5,"method":"tools/list","params":{}}' | python mcp-server-cheap-worker.py
```
Expected: el mismo JSON con `bulk_read` y `code_write` de la Tarea 6. Sin argumentos, no entra en CLI.

- [ ] **Step 6: Commit**

```bash
git add mcp-server-cheap-worker.py
git commit -m "feat: modo CLI que el README y el script de pruebas ya asumian"
```

---

### Task 8: Wiring y limpieza

**Files:**
- Create: `.mcp.json`
- Delete: `.claude-mcp.json`
- Delete: `.claude-mcp-ollama.json`
- Verify sin cambios: `test-mcp-ollama.ps1`

**Interfaces:**
- Consumes: el servidor MCP de la Tarea 6 y el modo CLI de la Tarea 7.
- Produces: nada consumido por tareas posteriores.

- [ ] **Step 1: Crear `.mcp.json`**

Crea `.mcp.json` en la raíz:

```json
{
  "mcpServers": {
    "shunt": {
      "command": "python",
      "args": ["C:\\Projects\\Cut-AI-Coding-MCP\\mcp-server-cheap-worker.py"],
      "env": {
        "SHUNT_API_BASE": "http://localhost:11434/v1",
        "SHUNT_MODEL": "qwen2.5-coder:7b",
        "SHUNT_MAX_CTX_TOKENS": "4096",
        "SHUNT_RESERVE_TOKENS": "1280",
        "SHUNT_MAX_OUTPUT_TOKENS": "1024"
      }
    }
  }
}
```

- [ ] **Step 2: Validar que el JSON parsea**

Run: `python -c "import json; print(list(json.load(open('.mcp.json'))['mcpServers']))"`
Expected: `['shunt']`

- [ ] **Step 3: Borrar los configs obsoletos**

Ninguno de los dos lo lee cliente alguno y ambos apuntan a rutas OneDrive inexistentes. Su contenido relevante está recogido en `DESIGN-self-hosted.md`.

```bash
git rm --cached .claude-mcp.json .claude-mcp-ollama.json 2>/dev/null; rm -f .claude-mcp.json .claude-mcp-ollama.json
```

- [ ] **Step 4: Confirmar que el script de pruebas no necesita cambios**

`test-mcp-ollama.ps1` ya invoca `bulk_read` con la firma correcta; lo que le faltaba era el modo CLI, añadido en la Tarea 7. No se edita.

Run:
```bash
grep -n "analyze_code\|generate_code\|specification\|reference_file\|output_file" test-mcp-ollama.ps1
```
Expected: sin resultados. Si aparece alguno, sustitúyelo por el nombre nuevo (`bulk_read` / `code_write`, `paths` / `spec` / `reference` / `target`) antes de seguir.

- [ ] **Step 5: Ejecutar la suite completa por última vez**

Run: `python -m unittest discover -s tests -t . -v`
Expected: PASS, 59 tests OK

- [ ] **Step 6: Prueba extremo a extremo por CLI contra Ollama**

Requiere `ollama serve` levantado en otra terminal. Es el script que el proyecto ya traía y que hasta ahora se colgaba esperando stdin.

Run: `powershell -ExecutionPolicy Bypass -File .\test-mcp-ollama.ps1`
Expected: termina en `=== TEST COMPLETADO ===` habiendo impreso bullets del modelo local sobre el archivo de prueba. Si sale `ERROR:`, es que Ollama no está levantado.

- [ ] **Step 7: Prueba extremo a extremo por JSON-RPC contra Ollama**

Run:
```bash
echo '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"bulk_read","arguments":{"question":"¿Qué funciones define?","paths":["cheap_worker_core.py"]}}}' | python mcp-server-cheap-worker.py
```
Expected: respuesta con `"result"` y bullets nombrando `Config`, `Backend`, `chunk_files`, `bulk_read` y `code_write`. Confirma que las dos vías, CLI y MCP, comparten el mismo núcleo.

- [ ] **Step 8: Commit**

```bash
git add .mcp.json
git add -u
git commit -m "chore: wiring .mcp.json y retirada de configs obsoletos"
```

---

## Verificación final

| Defecto del spec | Tarea que lo cierra |
|---|---|
| 1. Endpoint hardcodeado | Tarea 1 (`Config.from_env`) |
| 2. Contrato de herramientas desajustado | Tarea 6 (`handle_tools_list`) |
| 3. Truncación a 50 líneas | Tarea 2 (`chunk_files`) |
| 4. Errores devueltos como resultado válido | Tarea 6 (`handle_tools_call`), verificado en su paso 4 |
| 5. Nada conectado | Tarea 8 (`.mcp.json`) |
| Referencia opcional en `code_write` | Tarea 5 (`InputError` si falta) |
| Modo CLI documentado pero inexistente | Tarea 7 (`run_cli`) |

Total: 59 tests unitarios sin red, más dos pruebas extremo a extremo que requieren Ollama levantado.
