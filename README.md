# Shunt MCP self-hosted

Un servidor MCP que delega la lectura masiva de archivos y la generación de código a un modelo local barato, para que el modelo caro no gaste contexto en el contenido. Implementa el patrón [Spotify Portal Shunt, generalizado](https://gist.github.com/vtri950/84b2261efbadba243870bf161764aeb7).

Todo corre en local. Ni el código ni las preguntas salen de la máquina.

## Qué hace

```
Claude Code
    │  JSON-RPC por stdin/stdout
    ▼
mcp-server-cheap-worker.py      transporte MCP y modo CLI
    │
    ▼
cheap_worker_core.py             config, presupuesto, troceado, workers
    │  HTTP POST {base}/chat/completions
    ▼
Backend local             Ollama · vLLM · llama.cpp · LM Studio
```

Dos herramientas:

| Herramienta | Qué recibe | Qué devuelve |
|---|---|---|
| `bulk_read` | una pregunta y una lista de rutas | bullets con la respuesta |
| `code_write` | una especificación y un archivo de referencia | la ruta del archivo escrito |

En `code_write`, con `target`, el código generado **no vuelve**: solo la ruta y el número de líneas. Ahí está el ahorro.

## Ahorro medido

Sobre `cheap_worker_core.py`, 473 líneas, con `qwen2.5-coder:7b`:

| | Tokens |
|---|---|
| Lectura directa por el modelo caro | 4179 |
| Resumen que recibe en su lugar | 415 |
| **Ahorro** | **3764 (90%)** |

## Instalación

En cualquier equipo, tras clonar el repositorio:

```powershell
.\setup-ollama.ps1      # comprueba dependencias, descarga modelos, ajusta .mcp.json
.\start-ollama.ps1      # en otra terminal
```

Y reinicia Claude Code para que lea `.mcp.json`. Las herramientas aparecerán como `bulk_read` y `code_write`.

`setup-ollama.ps1` es obligatorio en un equipo nuevo, no opcional: `.mcp.json` guarda la ruta **absoluta** del servidor, así que la del equipo anterior no sirve. El script la reescribe con la de donde esté el proyecto. También comprueba Python y `requests` —la única dependencia— y descarga los modelos que `.mcp.json` declare, leyéndolos de ahí para que script y configuración no se separen.

No genera ningún archivo de código: el servidor vive en el repositorio y se versiona.

## Comprobar que funciona

```powershell
.\test-mcp-ollama.ps1     # prueba extremo a extremo
.\test-mcp.ps1            # banco de pruebas: mide el ahorro real
python -m unittest discover -s tests -t . -v   # 98 tests
```

También se puede llamar a mano, sin construir mensajes JSON-RPC:

```powershell
python mcp-server-cheap-worker.py bulk_read "¿Qué funciones define?" "cheap_worker_core.py"
python mcp-server-cheap-worker.py code_write "Añade logging" "referencia.py" "salida.py"
```

Las rutas van separadas por `|`. En modo CLI el resultado sale por stdout en texto plano y los errores por stderr con código de salida distinto de cero.

## Configuración

Todo se ajusta en la sección `env` de `.mcp.json`. Ninguna variable es obligatoria: los valores por defecto funcionan.

| Variable | Defecto | Para qué |
|---|---|---|
| `SHUNT_API_BASE` | `http://localhost:11434/v1` | Base OpenAI-compatible |
| `SHUNT_API_KEY` | *(vacío)* | Bearer, si el backend lo exige |
| `SHUNT_MAX_CTX_TOKENS` | `4096` | Ventana **real** del backend |
| `SHUNT_RESERVE_EXTRA` | `256` | Margen para el prompt de sistema |
| `SHUNT_TIMEOUT` | `600` | Segundos por llamada |
| `SHUNT_MIN_LINES` | `350` | Umbral en líneas del hook de bloqueo — ver [Cambiar el umbral](#cambiar-el-umbral) |
| `SHUNT_CACHE_DIR` | `.cache/cheap-worker` | Dónde se guardan los resúmenes |
| `SHUNT_CACHE_MAX` | `200` | Entradas en caché; `0` la desactiva |

Y lo que cambia por herramienta, porque las dos piden cosas opuestas:

| Variable | Defecto | Para qué |
|---|---|---|
| `SHUNT_MODEL_BULK` / `SHUNT_MODEL_CODE` | `qwen2.5-coder:7b` | Modelo de cada una |
| `SHUNT_MAX_OUTPUT_BULK` | `512` | Techo de salida al resumir |
| `SHUNT_MAX_OUTPUT_CODE` | `2048` | Techo de salida al generar |
| `SHUNT_TEMP_BULK` / `SHUNT_TEMP_CODE` | `0.2` / `0.0` | Temperaturas |

`SHUNT_MODEL` y `SHUNT_MAX_OUTPUT_TOKENS` siguen valiendo como respaldo para ambas, si no quieres separarlas.

### Por qué hay un techo por herramienta

`bulk_read` resume: escribe 275–415 tokens y necesita sitio para muchos archivos. `code_write` genera un archivo entero: escribe miles de tokens y solo manda una referencia. Un único techo servía mal a las dos — sobraba para una y faltaba para la otra.

El presupuesto para archivos sale de restar: `ventana − techo de salida − margen`. No hay que mantener ninguna coherencia a mano, y si la configuración no deja presupuesto el servidor falla al arrancar con un mensaje que dice cuál de las dos se quedó sin sitio.

**El timeout sí hay que vigilarlo.** Generar es lo lento: en CPU van unos pocos tokens por segundo, así que un techo alto son minutos. Un timeout corto revienta llamadas que iban bien.

## Otros clientes MCP

El servidor es un MCP estándar por stdio. No tiene nada de Claude Code: cualquier cliente que hable el protocolo puede usarlo dándole tres cosas.

| | Valor |
|---|---|
| Comando | `python` |
| Argumento | la ruta absoluta de `mcp-server-cheap-worker.py` |
| Entorno | las variables `SHUNT_*` que quieras fijar |

Lo que cambia entre clientes es **dónde** se declara eso y cómo se llama la clave. Claude Code usa `.mcp.json` con `mcpServers`; VS Code y Copilot usan `.vscode/mcp.json` con `servers`; Codex usa `~/.codex/config.toml` con `[mcp_servers.<nombre>]`.

```json
// .vscode/mcp.json
{
  "servers": {
    "cheap-worker": {
      "command": "python",
      "args": ["C:/ruta/al/proyecto/mcp-server-cheap-worker.py"],
      "env": { "SHUNT_MODEL_BULK": "qwen2.5-coder:3b" }
    }
  }
}
```

```toml
# ~/.codex/config.toml
[mcp_servers.cheap-worker]
command = "python"
args = ["C:/ruta/al/proyecto/mcp-server-cheap-worker.py"]
```

El servidor **negocia la versión del protocolo**: responde con la que pida el cliente si la conoce (`2024-11-05`, `2025-03-26`, `2025-06-18`) y con la más reciente propia si no. Su superficie se limita a `tools/list` y `tools/call`, que no cambian entre esas versiones.

**Solo se ha verificado con Claude Code.** Los demás deberían funcionar porque el protocolo es el mismo, pero nadie los ha probado. Si uno falla, `python mcp-server-cheap-worker.py` con un mensaje JSON-RPC por stdin reproduce el intercambio fuera del cliente, que es la forma rápida de ver de qué lado está el problema.

Lo que **no** viaja a otro cliente es el enforcement: el hook de `.claude/settings.json` es de Claude Code. El gist describe el equivalente para otros entornos, y sin él la Capa 1 no existe — el modelo puede seguir leyendo archivos enteros por su cuenta.

## Otros backends

El núcleo habla la API OpenAI-compatible `/v1/chat/completions` y nada más. Apuntarlo a otro motor es cambiar una línea:

```json
"SHUNT_API_BASE": "http://localhost:8000/v1"
```

**Pero conviene ser honesto sobre lo que está probado.** Todo lo medido en este documento es con Ollama. vLLM, llama.cpp y LM Studio deberían funcionar porque sirven la misma API, pero **nadie lo ha verificado todavía**.

Lo que sí se ha hecho para no atarlo a Ollama:

- El chequeo de salud de `test-mcp.ps1` usa `/v1/models`, el estándar OpenAI, en vez del `/api/tags` que solo tiene Ollama.
- La detección de modelos de razonamiento mira `reasoning` **y** `reasoning_content`, porque Ollama y vLLM nombran ese campo distinto.
- `SHUNT_API_KEY` cubre el bearer si hay un proxy o el backend lo exige.

Lo que sigue siendo de Ollama y no aplica a otros motores: `setup-ollama.ps1`, `start-ollama.ps1`, `test-mcp-ollama.ps1`, y el consejo de comprobar la ventana con `ollama ps`. Con vLLM la ventana la fija `--max-model-len` al arrancar.

## Caché de resúmenes

En una sesión de trabajo se releen los mismos archivos una y otra vez. `bulk_read` guarda cada resultado y lo reutiliza mientras nada cambie:

```
primera llamada   24 193 ms
segunda llamada        956 ms      (y casi todo es arrancar Python)
```

La clave es la huella de los bloques que se le mandan al modelo, más el modelo y el techo de salida. Eso significa que se invalida sola: si tocas un archivo, cambia su bloque y cambia la clave. Cambiar de modelo o de techo también produce entradas distintas, porque darían otro resumen.

La caché es una optimización, nunca un requisito: si el directorio no se puede escribir, se recalcula y ya. Se poda sola al llegar a `SHUNT_CACHE_MAX` entradas, tirando las más antiguas.

`code_write` no se cachea. Es lo infrecuente, y repetir una generación puede ser justo lo que quieres.

## Enforcement

`.claude/settings.json` instala un hook `PreToolUse` sobre `Read` que **deniega** leer archivos de **código** de más de `SHUNT_MIN_LINES` líneas y redirige a `bulk_read`.

Los documentos (`.md`, `.txt`, `.csv`, `.json` y cualquier extensión que no sea de código) no se bloquean. `bulk_read` se midió con código, y con prosa inventa: al resumir una ficha de 479 líneas devolvió rellena con cifras una tabla que en el original estaba vacía. Mientras no sea fiable con documentos, empujar a usarlo con ellos es peor que leerlos enteros. La lista de extensiones está en `EXTENSIONES_CODIGO`, en el propio hook. El gist insiste en por qué hace falta:

> *"Written rules are a suggestion. A block is not."*

La lectura acotada con `offset`/`limit` sigue permitida: para editar hacen falta números de línea fiables, y un resumen no los da.

El hook falla abierto. Ante un JSON ilegible o un archivo que no se puede abrir, deja pasar: uno roto que bloquea todo sería peor.

Cubre también `cat`, `less` y `more` por Bash, que es el atajo obvio para saltarse lo anterior. Ahí la regla es deliberadamente estrecha: solo se bloquea el comando **desnudo**.

| Comando | Qué pasa | Por qué |
|---|---|---|
| `cat grande.py` | **bloqueado** | vuelca el archivo entero al contexto |
| `cat grande.py \| grep def` | pasa | al contexto van las coincidencias, no el archivo |
| `cat grande.py > copia.py` | pasa | no lee nada hacia el modelo |
| `head -50 grande.py` | pasa | ya viene acotado |
| `grep def grande.py` | pasa | no es un volcado |

Bloquear los casos de la derecha haría el shell inusable a cambio de nada.

### Cambiar el umbral

`SHUNT_MIN_LINES` lo leen dos procesos distintos, y cada uno recibe su entorno de un sitio:

| Quién | Para qué | Dónde se fija |
|---|---|---|
| El hook | bloquear la lectura | `env` de la configuración de Claude Code (`.claude/settings.json`, `.claude/settings.local.json` o `~/.claude/settings.json`) |
| El servidor | anunciarlo en la descripción de `bulk_read` | `env` del servidor MCP (`.mcp.json`, `claude mcp add -e`, `claude_desktop_config.json`) |

Ponerlo solo en `.mcp.json` no cambia el bloqueo: el hook no ve el entorno del servidor. Fíjalo en los dos con el mismo valor.

Un valor que no sea un entero positivo se ignora y se usa 350, en los dos. Con `0` literal se bloquearía cualquier lectura.

### Activarlo en todos tus proyectos

El hook de `.claude/settings.json` solo actúa dentro de este repositorio. Registrar el servidor con `claude mcp add --scope user` pone las herramientas en todos tus proyectos, pero no el bloqueo: fuera de aquí el modelo elige si las usa.

Para bloquear en todos, declara el hook en `~/.claude/settings.json` con la ruta absoluta del script:

```json
{
  "env": { "SHUNT_MIN_LINES": "350" },
  "hooks": {
    "PreToolUse": [
      { "matcher": "Read|Bash",
        "hooks": [{ "type": "command", "timeout": 10,
                    "command": "python",
                    "args": ["C:/ruta/al/proyecto/hooks/bloquear-lectura-grande.py"] }] }
    ]
  }
}
```

`args` hace que Claude Code lance el script sin pasar por una shell, así que la ruta no necesita comillas. Si gestionas Python con mise, usa `"command": "C:/Users/<tú>/AppData/Local/mise/bin/mise.exe"` y antepón `"exec", "--", "python.exe"` a `args`: el `.exe` hace falta porque un proceso lanzado sin shell puede no tener `PATHEXT`, y sin él mise no encuentra `python`.

Dentro de este repositorio el hook correrá dos veces, el global y el del proyecto. Es inocuo: los dos toman la misma decisión.

**Claude Desktop no tiene hooks.** Ahí no hay bloqueo posible; solo cuenta la descripción de `bulk_read`, que es una indicación y no una barrera.

## Elegir modelo

Lo que domina el tiempo es **generar**, no leer. Medido en un portátil con RTX A500 de 4 GB:

| | Procesar entrada | Generar |
|---|---|---|
| `qwen2.5-coder:7b` | 415 t/s | 5.7 t/s |

El 76% del tiempo se va escribiendo. De ahí se siguen dos cosas:

- **Bajar `SHUNT_MAX_OUTPUT_TOKENS` acelera**, proporcionalmente.
- **`SHUNT_MAX_CTX_TOKENS` debe igualar la ventana que el backend sirve de verdad.** No porque una ventana grande sea rápida —procesar la entrada es la parte barata— sino porque quedarse corto fuerza a trocear, y cada trozo es un resumen más que generar, más la llamada de fusión.

Ese segundo punto es el ajuste más rentable, y es gratis. Medido sobre `cheap_worker_core.py` (473 líneas):

| `SHUNT_MAX_CTX_TOKENS` | Bloques | Llamadas | Tiempo |
|---|---|---|---|
| 4096 | 2 | 3 | 414 s |
| 8192 | 1 | 1 | **126 s** |

**3.3× más rápido**, y 8192 era lo que Ollama ya estaba sirviendo. Comprueba el tuyo con `ollama ps`, columna `CONTEXT`, y pon ese número. Pasarse es peor que quedarse corto: Ollama recorta en silencio.

**Un modelo solo va rápido si cabe entero en la VRAM.** Esa es la frontera, no el número de parámetros ni la cuantización. Medido sobre el mismo archivo y la misma pregunta:

| Modelo | Tamaño cargado | Reparto | Tiempo |
|---|---|---|---|
| `qwen2.5-coder:3b` | 2.3 GB | **100% GPU** | **25 s** |
| `qwen2.5-coder:7b` | 5.4 GB | 56%/44% CPU/GPU | 138 s |
| `qwen2.5-coder:7b-q3_K_M` | 4.5 GB | 49%/51% | más lento que el Q4 |

**5,5× de diferencia** entre caber y no caber. Bajar la cuantización sin bajar de tamaño no sirve: el Q3 sigue sin caber y encima paga más descuantización.

La calidad del 3B resumiendo aguanta bien: cubre las mismas responsabilidades que el 7B, con menos anidamiento. Por eso la configuración por defecto de `.mcp.json` le da el 3B a `bulk_read`, que es lo frecuente, y reserva el 7B para `code_write`, donde la especialización en código sí se paga.

**No uses modelos de razonamiento.** `gemma4:12b` devuelve la cadena de pensamiento en un campo aparte y deja el contenido vacío: gasta el techo de salida pensando y no llega a responder. El shunt lo detecta y da un error con ese diagnóstico, pero el modelo no sirve para este papel.

## Solución de problemas

**`No se pudo conectar con http://localhost:11434/v1/chat/completions`** — Ollama no está levantado. `.\start-ollama.ps1`.

**`no respondió en N s`** — no es la red: el modelo seguía escribiendo. Sube `SHUNT_TIMEOUT` o baja `SHUNT_MAX_OUTPUT_TOKENS`.

**`gastó los N tokens de salida razonando`** — el modelo es de razonamiento. Cámbialo.

**`Valor no numérico en una variable SHUNT_*`** — errata en la sección `env` de `.mcp.json`.

**Resúmenes que ignoran parte del archivo** — `SHUNT_MAX_CTX_TOKENS` es mayor que la ventana que el backend sirve de verdad, y está recortando en silencio. Compruébala con `ollama ps`, columna `CONTEXT`. El endpoint OpenAI-compatible de Ollama no admite `num_ctx` por petición: para ampliarla hay que fijarla en un Modelfile y publicar una variante.

## Documentos

- [`DESIGN-self-hosted.md`](DESIGN-self-hosted.md) — decisiones de diseño y por qué
- [`PLAN-self-hosted.md`](PLAN-self-hosted.md) — el plan de implementación que se siguió
- [`README-shared.md`](README-shared.md) — reutilizar el shunt en varios proyectos

---

Victoriano Fernández
