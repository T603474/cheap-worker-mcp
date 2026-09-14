# Diseño: modo self-hosted del MCP shunt

**Fecha:** 2026-09-09
**Estado:** implementado, fusionado en `main` y verificado contra un modelo local
**Referencia:** [Universal Model Routing (Spotify Portal Shunt, generalizado)](https://gist.github.com/vtri950/84b2261efbadba243870bf161764aeb7)

## 1. Problema

El servidor MCP actual (`mcp-server-cheap-worker.py`) implementa el patrón del shunt pero no puede funcionar en modo self-hosted portable. Cinco defectos concretos, encontrados al explorar:

1. **El endpoint está hardcodeado.** `OLLAMA_API` y `MODEL` son constantes de módulo y el script nunca lee `os.environ`. El `env: {"OLLAMA_API": ...}` que declaran los configs se ignora en silencio. No se puede apuntar a vLLM, a otro host ni a otro modelo sin editar el fuente.
2. **El contrato de herramientas no cuadra.** Ambos configs delegan en `bulk_read` (`shunt-ollama:bulk_read`, `shunt-vllm:bulk_read`), pero el servidor declara `analyze_code` y `generate_code`. El nombre al que apuntan no existe.
3. **La truncación contradice el patrón.** Los archivos de más de 350 líneas se recortan a las primeras 50 antes de enviarse al modelo. El propósito del shunt es que el worker barato lea el archivo íntegro para que el modelo frontier no lo vea nunca; resumir sobre el 12% del archivo anula el propósito.
4. **Los errores se devuelven como resultado válido.** Si el backend está caído, `analyze_code` retorna la cadena `"No se puede conectar a Ollama"` como si fuera el análisis. El modelo frontier razona sobre ella sin saber que es un fallo.
5. **Nada está conectado.** Claude Code lee `.mcp.json` con una clave `mcpServers`. Los dos configs del proyecto usan un formato propio (`mcp_servers` como array) que ningún cliente interpreta, y ambos apuntan a rutas OneDrive que ya no existen.

Además, `code_write` acepta hoy la referencia como opcional, justo lo que el gist advierte que provoca alucinación de patrones en el modelo barato.

## 2. Decisiones

| Decisión | Elegido | Motivo |
|---|---|---|
| Portabilidad de backend | Driver único OpenAI-compatible | Un solo servidor sirve vLLM, Ollama, llama.cpp y LM Studio sin tocar código |
| Alcance | Servidor + wiring funcional | Sin capa de enforcement (hooks) en esta iteración |
| Desborde de contexto | Map-reduce por trozos | El frontier nunca ve el archivo entero, y aguanta archivos grandes |
| Estructura | Dos módulos | Separa lógica del shunt del transporte MCP; permite probar el troceado sin JSON-RPC |
| Nombre del servidor | Se mantiene `mcp-server-cheap-worker.py` | No romper rutas ni memoria muscular |
| Configs antiguos | Se eliminan | Fuente de verdad única; ningún cliente los lee y sus rutas están muertas |

## 3. Arquitectura

```
Claude Code
  |  JSON-RPC 2.0 sobre stdin/stdout
  v
mcp-server-cheap-worker.py     <- transporte MCP: parseo, declaración, despacho
  |  llamadas Python directas
  v
cheap_worker_core.py            <- config, presupuesto, troceado, workers
  |  HTTP POST {base}/chat/completions
  v
Backend local: vLLM :8000 | Ollama :11434 | llama.cpp | LM Studio
```

`cheap_worker_core.py` no conoce JSON-RPC ni MCP: recibe argumentos Python y devuelve texto. `mcp-server-cheap-worker.py` no conoce HTTP ni modelos. Esa frontera permite ejercitar el troceado desde un REPL sin levantar el protocolo.

### `cheap_worker_core.py`

- **`Config`** — dataclass con `from_env()`. Único punto del proyecto que toca `os.environ`.
- **`Backend`** — expone `chat(system, user, temperature) -> str`. Único punto que conoce la forma HTTP/JSON de la API OpenAI-compatible. Lanza `BackendError` ante fallo de conexión, HTTP no-2xx o respuesta no parseable.
- **`chunk_files(paths, budget) -> ChunkResult`** — lee archivos, los envuelve en etiquetas XML y los agrupa en bloques que quepan en el presupuesto. Función pura sobre disco: sin red, testeable de forma aislada.
- **`bulk_read(cfg, question, paths)`** y **`code_write(cfg, spec, reference, target)`** — los dos workers.

### `mcp-server-cheap-worker.py`

Conserva el esqueleto actual (`send_response`, `send_error`, `handle_initialize`, `handle_tools_list`, `handle_tools_call`, `process_message`, `main`) con dos correcciones:

- `handle_initialize` pasa a declarar `"capabilities": {"tools": {}}`. Hoy declara `{}` vacío, lo que puede llevar a un cliente estricto a no consultar siquiera la lista de herramientas.
- `handle_tools_list` publica los esquemas nuevos.

## 4. Contrato de herramientas

| Herramienta | Argumento | Tipo | Requerido |
|---|---|---|---|
| `bulk_read` | `question` | string | sí |
| `bulk_read` | `paths` | array de string | sí |
| `code_write` | `spec` | string | sí |
| `code_write` | `reference` | string (una ruta) | sí |
| `code_write` | `target` | string (una ruta) | no |

Se adoptan los nombres del gist, que son además los que los configs ya esperaban. **No se mantienen alias** de `analyze_code` / `generate_code`: el wiring nunca llegó a funcionar, así que no hay consumidor real que romper, y cuatro nombres para dos herramientas dificultan al modelo la elección correcta.

Se incorporan los dos prompts de sistema del gist, hoy inexistentes: el analista que devuelve solo bullets encabezados por nombre/tipo/línea, y el generador que copia patrones de la referencia sin explicaciones. El código actual mete todo en un único prompt de usuario.

### Modo línea de comandos

Tanto el README como `test-mcp-ollama.ps1:148` invocan el servidor como CLI:

```
python mcp-server-cheap-worker.py bulk_read "<pregunta>" "<ruta1|ruta2>"
python mcp-server-cheap-worker.py code_write "<spec>" "<referencia>" ["<destino>"]
```

Esa interfaz **nunca ha existido**: `main()` ignora `sys.argv` y lee de stdin, así que el script de pruebas se queda colgado esperando entrada. Se implementa ahora, con las rutas separadas por `|` como documenta el README.

En modo CLI stdout transporta el resultado en texto plano —no JSON-RPC— y los errores salen por stderr con código de salida distinto de cero. Es la vía para probar el shunt a mano sin construir mensajes JSON-RPC.

## 5. Configuración por entorno

| Variable | Default | Para qué |
|---|---|---|
| `SHUNT_API_BASE` | `http://localhost:11434/v1` | Base OpenAI-compatible |
| `SHUNT_MODEL` | `qwen2.5-coder:7b` | Modelo |
| `SHUNT_API_KEY` | *(vacío)* | Bearer si el backend lo exige |
| `SHUNT_MAX_CTX_TOKENS` | `4096` | Ventana real del modelo |
| `SHUNT_RESERVE_TOKENS` | `1280` | Reserva para prompt de sistema y respuesta |
| `SHUNT_TEMP_BULK` | `0.2` | Temperatura del analista (del gist) |
| `SHUNT_TEMP_CODE` | `0.0` | Temperatura del generador (del gist) |
| `SHUNT_MAX_OUTPUT_TOKENS` | `1024` | `max_tokens` de la petición |
| `SHUNT_TIMEOUT` | `120` | Segundos por llamada HTTP |

`SHUNT_MAX_OUTPUT_TOKENS` se envía siempre como `max_tokens` explícito y la reserva debe ser **estrictamente mayor**, porque cubre prompt de sistema más respuesta. Con la reserva igual a la salida el presupuesto no cierra: 2816 de payload + 1024 de salida + 62 del prompt de sistema caben en 4096, pero 3072 + 1024 + 62 no, y Ollama recorta en silencio en vez de dar error. `tests/test_config.py` comprueba esa desigualdad para que no vuelva a descuadrarse.

Se envía explícito porque los valores por defecto varían entre backends, y en `code_write` un límite de salida corto trunca el archivo generado a media función sin avisar.

Queda una holgura de unos 190 tokens que absorbe la pregunta de `bulk_read`. Esa pregunta **no** se descuenta del presupuesto: `chunk_files` solo mide el contenido de los archivos. Una pregunta muy larga puede comerse la holgura, así que si se sube `SHUNT_MAX_OUTPUT_TOKENS` conviene subir la reserva en la misma medida y algo más.

`OLLAMA_API` se lee como alias de respaldo cuando `SHUNT_API_BASE` no está definida, normalizando el sufijo `/v1` si falta.

### Limitación conocida: `num_ctx` en Ollama

El endpoint OpenAI-compatible de Ollama no admite `num_ctx` por petición. Si el modelo arranca con 4096 y se le envía un bloque de 8000 tokens, Ollama recorta por su cuenta y en silencio. Por tanto `SHUNT_MAX_CTX_TOKENS` debe reflejar lo que el backend sirve **realmente**, no lo deseado.

Para ampliar ventana en Ollama hay que fijarla en un Modelfile (`PARAMETER num_ctx 16384`) y publicar una variante del modelo. En vLLM no aplica: manda `--max-model-len`. Queda documentado junto a la variable.

## 6. Flujo de `bulk_read`

Presupuesto = `SHUNT_MAX_CTX_TOKENS - SHUNT_RESERVE_TOKENS`. La estimación de tokens usa una heurística de caracteres/4, sin introducir dependencia de tokenizador; `SHUNT_RESERVE_TOKENS` existe para absorber el error de esa aproximación.

1. **Empaquetado.** Cada archivo se lee íntegro y se envuelve como `<file path="src/A.py">...</file>`. Los archivos inexistentes no abortan la llamada: se acumulan y se reportan al final. Se agrupan archivos consecutivos hasta llenar el presupuesto.
2. **Archivo que no cabe por sí solo.** Se parte por líneas y cada trozo se reetiqueta con su rango real: `<file path="src/A.py" lines="421-840" part="2/3">`. El modelo sabe así que ve un fragmento y cuál es.
3. **Map.** Una llamada por bloque, con el prompt de sistema del analista y la pregunta.
4. **Reduce.** Solo si hubo más de un bloque: una llamada final que recibe los conjuntos de bullets y la pregunta original y los funde eliminando duplicados. Si los bullets combinados tampoco cupieran, se pliegan por tandas hasta que quepan.

El caso común —archivos que caben en un bloque— cuesta **una sola llamada**, sin reduce. La latencia adicional solo se paga cuando hay desborde real.

## 7. Flujo de `code_write`

Sin map-reduce: trocear una referencia de estilo la inutiliza. Si `reference` no cabe en el presupuesto se devuelve error, no un truncado silencioso.

- **`reference` es obligatorio.** Hoy es opcional y, si falta, se envía vacío.
- **Con `target`, la respuesta es solo la ruta y el número de líneas escritas.** El modelo frontier nunca ve el código generado: ahí está el ahorro de tokens.
- **Sin `target`**, se devuelve el código.
- Se eliminan los fences markdown de la respuesta del modelo, como pide el gist.

### Validación de la salida

Contar vallas no distingue código legítimo de salida corrupta: tanto un docstring que contiene una valla como un preámbulo de prosa seguido de una valla truncada presentan «contenido antes de una valla suelta». Por eso, tras extraer el bloque, el resultado se pasa por `compile()`; si no es Python válido se lanza `InputError` y no se escribe nada.

Esto cubre el fallo que la sección 5 nombra y que ninguna heurística de vallas detecta: un `SHUNT_MAX_OUTPUT_TOKENS` corto corta el archivo a media función, y el resultado se escribiría en disco reportado como éxito. Ata `code_write` a Python, que es el único lenguaje que este proyecto genera.

## 8. Errores

`cheap_worker_core` lanza excepciones tipadas y el transporte las traduce a respuestas MCP. Ningún fallo vuelve a viajar como resultado válido.

| Situación | Comportamiento |
|---|---|
| `BackendError` (conexión, HTTP no-2xx, JSON inesperado) | `send_error`, incluyendo el endpoint intentado |
| `BudgetError` (referencia que no cabe) | `send_error` |
| Archivo inexistente | No es error: se reporta dentro de la respuesta y se sigue con el resto |
| Ningún archivo legible | `send_error` |
| Fallo al escribir `target` | `send_error` explícito, nunca un "ok" a medias |

El logging permanece en stderr: stdout es el canal JSON-RPC y contaminarlo rompe el protocolo.

## 9. Pruebas

1. **`chunk_files`** — única pieza con lógica sustancial, se escribe con test primero: archivo pequeño; varios que caben juntos; uno que desborda y se parte con rangos correctos; path inexistente; archivo vacío.
2. **`Backend`** con un doble: verifica construcción del payload y parseo de la respuesta sin tocar la red.
3. **Extremo a extremo** reutilizando `test-mcp-ollama.ps1`, que ya invoca `bulk_read` con los nombres correctos. Le faltaban dos cosas: el modo CLI, y una ruta viva — tenía codificada la carpeta OneDrive, así que fallaba al escribir sus ficheros de prueba antes siquiera de llamar al modelo. Ahora deriva la ruta de su propia ubicación. Requiere backend levantado.

## 10. Wiring

Se crea `.mcp.json` en la raíz del proyecto:

```json
{
  "mcpServers": {
    "shunt": {
      "command": "python",
      "args": ["C:\\Projects\\Cut-AI-Coding-MCP\\mcp-server-cheap-worker.py"],
      "env": {
        "SHUNT_API_BASE": "http://localhost:11434/v1",
        "SHUNT_MODEL": "qwen2.5-coder:7b",
        "SHUNT_MAX_CTX_TOKENS": "4096"
      }
    }
  }
}
```

Se eliminan `.claude-mcp.json` y `.claude-mcp-ollama.json`.

## 10.bis Verificación contra un modelo vivo

Ejecutado el 2026-09-11 contra Ollama con `qwen2.5-coder:7b` en local, por el endpoint OpenAI-compatible `/v1/chat/completions`.

| Prueba | Resultado | Tiempo |
|---|---|---|
| `bulk_read` sobre `cheap_worker_core.py` (464 líneas) | 2 bloques → 2 llamadas map + 1 reduce | 2 min 32 s |
| `code_write` con `target` | Escribe el archivo, devuelve solo ruta y líneas | 19 s |
| `test-mcp-ollama.ps1` extremo a extremo | Completa | 17 s |

**El plegado fusiona de verdad.** El resumen de `cheap_worker_core.py` nombra funciones de los *dos* bloques —`Config` y `chunk_files` del primero, `_validar_python` y `code_write` del segundo—, algo que una sola llamada no podría cubrir. Es la confirmación de que el paso reduce no se limita a devolver el primer parcial.

**Ahorro medido**, que es la razón de ser del proyecto:

| | Tokens |
|---|---|
| `cheap_worker_core.py` leído directamente por el modelo caro | 4179 |
| Resumen que recibe en su lugar | 415 |
| **Ahorro** | **3764 (90%)** |

En `code_write` el ahorro es aún más limpio: el código generado (86 tokens) no vuelve nunca, solo la ruta (27 tokens).

La validación de salida no dio falsos rechazos con generación real: el modelo devolvió una clase con docstring en español que copiaba el estilo de la referencia, y pasó.

**Latencia y dónde se va el tiempo.** Medido con la API nativa: procesar la entrada va a ~400 tok/s, generar a ~5.7 tok/s. El 76% del tiempo es escribir, no leer.

Eso corrige una intuición equivocada que estuvo en este documento: ampliar la ventana de contexto no acelera por sí misma, porque solo toca el 23% barato. Pero **quedarse corto sí penaliza**, y mucho, porque obliga a trocear y cada trozo es un resumen más que generar más la llamada de fusión:

| `SHUNT_MAX_CTX_TOKENS` | Bloques | Llamadas | Tiempo sobre `cheap_worker_core.py` |
|---|---|---|---|
| 4096 | 2 | 3 | 414 s |
| 8192 | 1 | 1 | 126 s |

3,3× de diferencia, y 8192 era lo que Ollama ya servía. La regla es igualar la ventana real del backend (`ollama ps`, columna `CONTEXT`), nunca estimarla: pasarse hace que Ollama recorte en silencio, y quedarse corto multiplica las llamadas.

Una medición anterior de este mismo par dio empate (2 min 32 s contra 2 min 29 s) y llevó a descartar el ajuste. Estaba contaminada: había un segundo modelo residente compitiendo por memoria, y la generación iba a 3,2 tok/s en vez de 5,7. Conviene medir con un solo modelo cargado.

## 11. Fuera de alcance

- **Capa de enforcement (Capa 1 del gist).** El gist insiste en que sin bloqueo duro el patrón no se aplica: *"Written rules are a suggestion. A block is not."* Queda para una iteración posterior.
- **README.** `README.md` y `README-shared.md` documentan un montaje vLLM con archivos que no existen (`setup-vllm.ps1`, `start-vllm.ps1`, `mcp-server.py`) y rutas OneDrive muertas. Un operador que siga solo esa documentación no puede levantar el sistema, aunque el sistema en sí funcione. Necesita una pasada aparte.
- **`copy-to-new-project.ps1` y `test-mcp.ps1`** conservan rutas OneDrive absolutas y nombran archivos de la época vLLM que no existen. Misma pasada.

### `setup-ollama.ps1`: sí se tocó, y por qué

Estaba fuera de alcance, pero la revisión final encontró que no solo regeneraba el config obsoleto: regeneraba **un `mcp-server-cheap-worker.py` completo con el diseño anterior** —Ollama hardcodeado, truncación a 50 líneas, errores devueltos como resultado válido—, es decir, los defectos 3 y 4 de la sección 1. Solo era inofensivo porque su ruta OneDrive estaba muerta: corregir esa ruta, algo que cualquiera haría al limpiar, habría sobrescrito el servidor bueno con el viejo sin avisar.

Se le quitó toda la generación de archivos. Conserva lo que sí aporta —comprobar que Ollama está instalado y descargar el modelo— y remite a `.mcp.json` en vez de mandar copiar nada a `~/.claude`.
