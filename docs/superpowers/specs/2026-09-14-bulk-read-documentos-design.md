# bulk_read fiable con documentos — diseño

**Fecha:** 2026-09-14
**Estado:** aprobado, pendiente de plan de implementación

## 1. Problema

`bulk_read` se diseñó y se midió solo con código Python. Probado con fichas reales de un temario (Markdown en español):

| Fichero | Resultado |
|---|---|
| 52 líneas | correcto en lo esencial |
| 479 líneas | **inventado**: no respondió a la pregunta y rellenó con cifras una tabla que en el original está vacía |
| más de 950 líneas | tiempo agotado, sin respuesta |

Causas identificadas en el código:

1. `SYSTEM_BULK` presenta al modelo como *"a precise code analyst"* y le exige *"Lead every bullet with exact name/type/line"*. Ante una tabla vacía no hay nada que citar, y un modelo de 3B rellena.
2. La instrucción de no inventar solo está en el paso de fusión (`SYSTEM_REDUCE`), no en la lectura de cada trozo.
3. El modelo no ve números de línea: cada trozo lleva solo su rango (`lines="1-240"`). Pedirle que cite líneas es pedirle que cuente.
4. La fusión con el modelo (`_reduce`) es otra oportunidad de inventar y otra llamada que suma tiempo.
5. `chunk_files` abre cualquier archivo como UTF-8 con `errors="replace"`: un PDF o un `.docx` llegan al modelo como bytes ilegibles, y el modelo devuelve un resumen igualmente.

## 2. Objetivo y límites

`bulk_read` debe servir con código y con documentos, tanto para **extraer datos concretos** como para **orientarse** en un documento.

Principio: **ningún dato llega a la respuesta sin una cita literal que el servidor haya encontrado en el archivo.** Lo que no se puede verificar se descarta y se cuenta.

Lo que el diseño **no** garantiza, y se documentará:

- Una cita real con una interpretación equivocada pasa la verificación.
- Las cifras escritas con palabras ("tres quintos") no las cubre el filtro de cifras.
- Un resumen de un modelo de 3–7B no sustituye a leer el documento. Para extraer cifras que importan, se lee.

Fuera de alcance: OCR de PDF escaneados, `.doc` antiguo, búsqueda previa de trozos relevantes (fase B, condicionada a la medición de la sección 8).

## 3. Enfoque elegido

**A: afirmaciones con cita, verificadas por el servidor, y fusión en código.** Se descartaron:

- **C, solo cambiar el prompt y verificar al final:** mantiene las llamadas de fusión (no ataca el timeout) y la respuesta fusionada pierde la relación entre afirmación y cita.
- **B, buscar antes de leer:** reduce llamadas, pero puede descartar el trozo bueno por sinónimos ("mayoría cualificada" frente a "tres quintos"). Queda como segunda fase si la medición lo justifica.

## 4. Extracción de texto

Módulo nuevo `cheap_worker_extract.py`. Convierte cada archivo en una lista de líneas, cada una con su **ubicación**.

| Formato | Extracción | Ubicación |
|---|---|---|
| Texto, código, `.md`, `.csv`, `.json` y extensiones no listadas | tal cual, UTF-8 | `línea N` |
| `.pdf` | `pypdf`, página a página | `p. N` |
| `.docx` | `zipfile` + XML de `word/document.xml`: párrafos y celdas de tabla | `párrafo N` |
| `.odt` | `zipfile` + XML de `content.xml`: párrafos, encabezados y celdas | `párrafo N` |
| `.html`, `.htm` | `html.parser` de la biblioteca estándar, sin `<script>` ni `<style>`; los elementos de bloque generan saltos de línea | `línea N` del texto extraído |

Errores de extracción, con motivo explícito:

| Caso | Mensaje (esencia) |
|---|---|
| PDF sin texto | no tiene texto extraíble; ¿es un escaneo? |
| PDF cifrado | está cifrado |
| `.docx`/`.odt` corrupto | no es un documento válido |
| Bytes nulos en los primeros 8 KB de un formato no reconocido | formato binario no soportado |
| `pypdf` no instalado y se pide un PDF | cómo instalarlo |

`pypdf` es **opcional**: sin él funciona todo salvo los PDF. `setup-ollama.ps1` avisa si falta, sin fallar.

Con varios archivos, los que fallan se listan al final con su motivo y el resto se procesa. Si no se lee ninguno, `bulk_read` lanza error.

`chunk_files` trocea las líneas extraídas en lugar de leer el archivo.

## 5. Qué devuelve el modelo

`SYSTEM_BULK` se reescribe: válido para código y prosa, responde en el idioma de la pregunta (con código en inglés y pregunta en español, la respuesta útil es en español), y por cada afirmación da una cita literal copiada del texto:

```
- Las leyes orgánicas requieren mayoría absoluta del Congreso
  > requerirá mayoría absoluta del Congreso, en una votación final
```

- Formato viñeta más cita (`-` y `>`), no JSON: más fiable en modelos pequeños y fácil de analizar.
- Si el trozo no contiene la respuesta, responde exactamente `NO CONSTA`.
- El modelo **no** da números de línea.

`SYSTEM_REDUCE`, `_prompt_reduce` y `_reduce` se eliminan.

## 6. Verificación

Para cada afirmación, dos filtros. Si falla cualquiera, se descarta.

1. **La cita existe en el trozo enviado.** Normalización de ambos lados: minúsculas, espacios y saltos de línea colapsados, sin `*`, `_`, `` ` `` ni comillas tipográficas. Mínimo 3 palabras. La búsqueda se hace sobre el texto del trozo unido con espacios, de modo que una cita puede cruzar saltos de línea. La ubicación mostrada es la de la línea donde empieza.
2. **Cada cifra de la afirmación aparece en su cita.** Cifra: secuencia de dígitos, con separadores `.` o `,` internos. Una afirmación sin cifras pasa este filtro.

Motivos de descarte que se cuentan por separado: sin cita, cita no encontrada, cita demasiado corta, cifras no respaldadas.

## 7. Respuesta final

- Afirmaciones verificadas de todos los trozos, en orden de aparición en el documento, sin duplicados (misma cita normalizada y mismo archivo).
- Cada una con su cita y `(archivo:ubicación)`.
- Si no queda ninguna: `No consta en los documentos`.
- Al final, si los hay: resumen de descartadas por motivo, y archivos no leídos con su motivo.

La clave de caché incluye una constante de versión del formato, para no reutilizar resúmenes antiguos sin verificar.

`code_write` no cambia. El hook de bloqueo sigue limitado a extensiones de código hasta que la evaluación demuestre que `bulk_read` es fiable con documentos; ampliarlo es una decisión posterior.

## 8. Pruebas y elección de modelo

**Tests del repositorio** (sin material real, con el backend falso de `tests/helpers.py`):

- Verificación: cita real conservada e inventada descartada; cifra inventada sobre cita real descartada; `NO CONSTA`; cita que cruza líneas; cita de menos de 3 palabras; afirmación sin cita; duplicados entre trozos; contadores de descarte.
- Extracción: `.docx`, `.odt` y HTML generados en el propio test; un PDF pequeño generado en el test o incluido como fixture mínimo; archivo binario; PDF sin `pypdf` instalado.
- Servidor: la descripción de `bulk_read` deja de excluir documentos.

**Evaluación con documentos reales**, fuera de git:

- `eval-bulk-read.py` (en el repositorio, genérico) recibe un archivo JSON de preguntas: archivo, pregunta, tipo (`dato`, `orientacion`, `sin_respuesta`) y fragmentos esperados.
- Las preguntas y respuestas sobre el corpus real viven en `.cache/eval/`, ignorado por git. Ni rutas ni contenido del corpus entran en el repositorio.
- Métricas por modelo: aciertos (el fragmento esperado aparece entre las verificadas), descartadas, `NO CONSTA` correctos en `sin_respuesta`, tiempo y llamadas.
- Candidatos (caben en 4 GB de VRAM): `qwen2.5-coder:3b` (actual), `qwen2.5:3b`, `llama3.2:3b`, `gemma3:4b`. Se conserva el ganador; los demás se borran.

**Timeout:** con el documento real más largo (unas 2 700 líneas, 130 KB) se mide si el corte viene de `SHUNT_TIMEOUT` o del cliente MCP, y cuántas llamadas hace. Si sigue siendo inaceptable, se diseña la fase B.

## 9. Documentación

README: formatos soportados, dependencia opcional `pypdf`, formato de respuesta con citas, qué garantiza y qué no la verificación, resultados de la evaluación (sin datos del corpus), y el aviso de que para extraer cifras que importan se lee el documento.
