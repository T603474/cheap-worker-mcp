# bulk_read: que el modelo cite — diseño

**Fecha:** 2026-09-15
**Estado:** implementado
**Precede:** `2026-09-15-relevancia-citas-design.md`

## 1. Problema

Tras el filtro de pertinencia ya no llegan afirmaciones falsas a la salida, pero casi todo se pierde antes: entre 64 y 155 afirmaciones **sin cita** por modelo en la evaluación. Dos respuestas en bruto de `gemma3:4b` muestran dos fallos distintos:

1. **No ve la pregunta.** Con un trozo de unos 7 000 tokens, ignora la pregunta y el formato y devuelve en inglés un resumen del documento con decenas de viñetas. La pregunta y las instrucciones van al principio del mensaje, antes del documento.
2. **Se inventa la cita.** Acierta el dato, pero como cita repite su propia frase en lugar de copiar el documento, y omite el guion de la viñeta.

## 2. Objetivo

Aumentar las afirmaciones correctas y respaldadas con documentos, sin volver a mostrar afirmaciones falsas. Se decide con la evaluación, no por intuición.

Fuera de alcance: palabras genéricas en la pregunta, tiempos verbales en las raíces de la pertinencia, preselección de líneas relevantes antes del modelo. Se decidirán con las cifras de esta medición.

## 3. Variantes del prompt

Una función del núcleo, `_mensajes_bulk(pregunta, texto_bloque, variante) -> (system, user)`, construye los mensajes. `bulk_read` la usa en lugar de componerlos en línea.

| Variante | Sistema | Usuario |
|---|---|---|
| `actual` | `SYSTEM_BULK` de hoy | `Question: …\n\nFiles:\n…` (como hoy) |
| `pregunta_al_final` | Instrucciones y ejemplo | `Files:\n…`, y **después** la pregunta y un recordatorio corto del formato |
| `cita_primero` | Instrucciones y ejemplo con la cita antes de la afirmación | Igual que `pregunta_al_final`, con el recordatorio en ese orden |

Las variantes `pregunta_al_final` y `cita_primero`:

- Piden responder en el idioma de la pregunta, **no resumir** el documento y responder solo a la pregunta.
- Exigen que la cita se **copie del documento**, nunca con palabras propias.
- Incluyen un ejemplo completo **inventado** (sin contenido de material real): un documento breve, una pregunta con respuesta y su formato, y una pregunta sin respuesta con `NO CONSTA`.

## 4. Analizador

`analizar_respuesta(texto, cita_primero=False)`:

- Modo normal: sin cambios.
- Modo `cita_primero`: las líneas `>` se acumulan y se asignan a la **siguiente** viñeta. Las líneas de continuación de una viñeta se suman a su texto como hoy. Citas acumuladas sin viñeta posterior se descartan silenciosamente (no hay afirmación que respaldar).

`verificar_respuesta` recibe también `cita_primero` y lo pasa al analizador.

## 5. Selección y caché

- Constante del núcleo `VARIANTE_PROMPT`; no se expone como variable de entorno.
- La clave de caché incluye la variante.

## 6. Medición

`eval-bulk-read.py --variantes actual,pregunta_al_final,cita_primero`: evalúa cada combinación modelo × variante, fijando `cheap_worker_core.VARIANTE_PROMPT` en el proceso. El volcado de respuestas incluye la variante en el nombre del archivo y el resumen muestra una línea por combinación.

Medición real con `gemma3:4b` y `qwen2.5:3b`, ventana 8192, mismas preguntas (fuera de git). Revisión manual: correctas y respaldadas, verdaderas que no responden, falsas, "No consta" correctos, descartes por motivo y tiempo.

## 7. Cierre

- La variante con más afirmaciones correctas y respaldadas, sin falsas, queda en `VARIANTE_PROMPT`. En empate, la más rápida.
- Las variantes perdedoras se eliminan del código y de los tests. Si la medición es ambigua, decide el usuario.
- README: resultados agregados y el prompt vigente.

## 8. Pruebas del repositorio

- `_mensajes_bulk`: en `pregunta_al_final` y `cita_primero` la pregunta aparece después del texto del bloque y el ejemplo va incluido; `actual` reproduce el formato de hoy; variante desconocida → error.
- Analizador en modo `cita_primero`: cita antes de viñeta; varias afirmaciones; cita final sin viñeta; continuaciones.
- `bulk_read` con `BackendFalso` en modo `cita_primero`.
- La clave de caché cambia con la variante.
- `eval-bulk-read.py` recorre las variantes y fija la constante.

## 9. Resultado

Se midieron las tres variantes con dos modelos; ganó `pregunta_al_final` en
ambos; `actual` y `cita_primero` (con su analizador) se eliminaron;
resultados agregados en el README.
