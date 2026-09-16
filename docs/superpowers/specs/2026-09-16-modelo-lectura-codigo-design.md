# bulk_read: modelo distinto para leer código — diseño

**Fecha:** 2026-09-16
**Estado:** aprobado

## Problema

`bulk_read` usa un único modelo (`SHUNT_MODEL_BULK`). La evaluación con documentos eligió `gemma3:4b`, pero con código responde flojo con cualquier prompt, mientras que `qwen2.5-coder:3b` iba bien con código y mal con documentos.

## Diseño

- **Configuración:** `SHUNT_MODEL_BULK_CODE`, modelo de `bulk_read` para archivos de código. Por defecto, el valor de `SHUNT_MODEL_BULK`: nada cambia para quien no lo fije. `code_write` sigue con `SHUNT_MODEL_CODE`.
- **Perfil:** `Config.perfil_bulk_code` es `perfil_bulk` con otro modelo. Misma ventana, techo de salida, temperatura y presupuesto.
- **Bloques homogéneos:** `chunk_files` empaqueta por separado los trozos de código (`cheap_worker_extract.es_codigo`) y los de documentos. `Bloque` gana `es_codigo`. Primero los bloques de documentos, después los de código.
- **Selección:** `bulk_read` usa `perfil_bulk_code` para bloques de código y `perfil_bulk` para el resto.
- **Caché:** la clave incluye también el modelo de código.
- **Arranque:** el log del servidor muestra el modelo de lectura de código.
- **Sin cambios:** prompt, verificación, filtros de pertinencia (solo documentos), orden de la respuesta (por posición).

Coste conocido: una consulta que mezcla código y documentos obliga a Ollama a cambiar de modelo a mitad; en 4 GB de VRAM no caben los dos.

## Pruebas

- Config: por defecto igual a `SHUNT_MODEL_BULK`; valor explícito; el perfil comparte todo menos el modelo.
- `chunk_files`: un `.py` y un `.md` pequeños van en bloques distintos con `es_codigo` correcto.
- `bulk_read`: consulta con `.md` y `.py` usa un modelo por bloque (el backend falso registra el modelo).
- Caché: cambiar `SHUNT_MODEL_BULK_CODE` invalida la entrada de un archivo de código.
- Comprobación real: preguntas sobre código con `qwen2.5-coder:3b` y el prompt vigente.
