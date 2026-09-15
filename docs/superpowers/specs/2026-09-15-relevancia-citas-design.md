# bulk_read: citas pertinentes — diseño

**Fecha:** 2026-09-15
**Estado:** aprobado, pendiente de plan de implementación
**Precede:** `2026-09-14-bulk-read-documentos-design.md`

## 1. Problema

La evaluación con documentos jurídicos reales (sección "Con documentos: evaluación" del README) mostró que la verificación de citas funciona: ninguna cita inventada ni cifra en dígitos inventada llegó a la salida. Pero no basta. Los modelos de 3–4B adjuntan **citas reales que no respaldan la afirmación**:

| Afirmación del modelo | Cita adjunta (real) |
|---|---|
| "El Congreso debe pronunciarse sobre un Decreto-ley en un plazo de tres años" | un artículo sobre la renovación del Tribunal Constitucional |
| "El Tribunal Constitucional se compone de doce miembros" | un fragmento del artículo 82 |

Pasan porque la cita existe y la afirmación no tiene cifras en dígitos. La respuesta buena suele estar, enterrada entre 5–8 afirmaciones "verificadas" irrelevantes. Además, `eval-bulk-read.py` puntuaba de más: daba por acertado un caso si el dato esperado aparecía en el texto de la afirmación, aunque la cita no tuviera relación.

## 2. Objetivo y prioridad

Que lo que muestre `bulk_read` con documentos **venga al caso**. Prioridad elegida: **pocas afirmaciones y siempre pertinentes**. Se acepta que el dato correcto se pierda como "No consta" antes que mostrar afirmaciones que la cita no respalda.

Límites que se documentarán:

- Los sinónimos sin palabras en común ("mayoría cualificada" frente a "tres quintos" sin más contexto) se pierden.
- Una cita que comparte palabras con la afirmación puede seguir sin respaldarla lógicamente (negaciones, excepciones).

Fuera de alcance: una segunda llamada al modelo como juez; mostrar solo citas sin afirmación; filtros de relevancia para código.

## 3. Enfoque

**Filtro léxico determinista en el servidor**, sin llamadas extra. Se descartaron:

- **Modelo como juez:** multiplica las llamadas y un modelo de 3B que empareja mal juzga igual de mal.
- **Solo citas:** elimina la interpretación, pero deja de dar respuestas y sigue mostrando fragmentos irrelevantes.

## 4. Alcance: solo documentos

Los filtros de las secciones 5 y 6 se aplican a tramos cuyo archivo **no** tiene extensión de código. Con código las preguntas son genéricas ("¿qué funciones define?") y las citas son identificadores (`def bulk_read(cfg: Config, ...)`): un filtro léxico estricto las descartaría, y ahí `bulk_read` ya daba buen resultado. El código se sigue verificando como hasta ahora: cita literal y cifras en dígitos.

La lista de extensiones de código es la del hook (`EXTENSIONES_CODIGO`). Se expone como `cheap_worker_extract.es_codigo(ruta)`. El hook conserva su copia, porque se ejecuta desde otros proyectos sin importar nada del repositorio; un test comprueba que ambas listas son idénticas.

## 5. Números escritos con letras

En documentos, el filtro de cifras compara **valores**, no texto. Cada afirmación y cada cita se reducen a un conjunto de valores canónicos:

- **Dígitos:** como hoy (`\d+(?:[.,]\d+)*`), canónico el propio texto (`12`, `1.500`).
- **Palabras numéricas** (español, sin tildes tras normalizar): cero–veintinueve, treinta–noventa, cien, ciento, doscientos–novecientos, mil; y las formas compuestas "treinta y uno"… "noventa y nueve". Canónico: los dígitos (`doce` → `12`, `treinta y dos` → `32`).
- **Fracciones:** `N/M` en dígitos, y en palabras "un/una/dos/tres… tercio(s)/cuarto(s)/quinto(s)/sexto(s)/séptimo(s)/octavo(s)/noveno(s)/décimo(s)", y "mitad" → `1/2`. Canónico: `N/M` (`tres quintos` → `3/5`).

Regla: los valores de la afirmación deben estar entre los de la cita. Una afirmación sin valores pasa este filtro. En código se mantiene el filtro actual de dígitos.

## 6. Pertinencia

**Palabras con contenido** de un texto: minúsculas, sin tildes, separando por lo que no sea letra o dígito; se descartan las palabras vacías (lista fija en español e inglés: artículos, preposiciones, conjunciones, pronombres, interrogativos, auxiliares y verbos muy comunes como "ser", "estar", "haber", "tener") y las de menos de 3 caracteres. Dos palabras **coinciden** si sus primeros 5 caracteres son iguales (o son iguales enteras, si alguna tiene menos de 5).

Filtros, aplicados en este orden tras los actuales:

1. **La cita respalda la afirmación:** al menos el **60 %** de las palabras con contenido de la afirmación coinciden con alguna de la cita. Si la afirmación no tiene palabras con contenido, se descarta.
2. **La cita toca la pregunta:** al menos **una** palabra con contenido de la pregunta coincide con alguna de la cita. Si la pregunta no tiene palabras con contenido, el filtro no descarta.

Casos que fijan el comportamiento. Reproducen la forma de los fallos observados en la evaluación con ejemplos inventados: el contenido del material real no entra en el repositorio.

| Pregunta | Afirmación | Cita | Cobertura | Toca pregunta | Resultado |
|---|---|---|---|---|---|
| ¿Qué mayoría exige la reforma del estatuto? | "La reforma del estatuto requiere una mayoría de 3/5 en cada junta." | "Mayoría 3/5: necesaria para la reforma del estatuto en cada una de las juntas" | 5/6 | sí | pasa |
| ¿En qué plazo se resuelve un recurso? | "El recurso se resuelve en un plazo de tres años." | "A los tres años de su nombramiento, los vocales del consejo se renuevan por sorteo" | 2/5 | no | descartada (`cita_no_respalda`) |
| ¿Cuántos vocales tiene el consejo? | "El consejo tiene doce vocales." | "Las juntas podrán delegar en la comisión la potestad de dictar normas" | 0/2 | no | descartada antes, por números: "doce" no está en la cita |
| ¿Qué hace falta para convocar una asamblea? | "Para convocar una asamblea hace falta 1/10 de los socios de cualquiera de las juntas." | "Mayoría 1/10: la que se necesita para convocar una asamblea por parte de los socios" | 3/6 o 3/7 | sí | descartada (`cita_no_respalda`) |

Las coberturas de la tabla se recalculan en el test con la lista de palabras vacías real; si alguna cifra de la tabla difiere, manda el resultado (pasa/descartada), que es lo que el test fija.

Nuevos motivos de descarte, tras los existentes y en este orden: `cita_no_respalda` ("con cita que no respalda la afirmación") y `cita_ajena` ("con cita ajena a la pregunta").

## 7. Cambios de interfaz

- `cheap_worker_verify.verificar(afirmaciones, tramos, pregunta)` y `verificar_respuesta(texto, tramos, pregunta)`: nuevo parámetro `pregunta`.
- `cheap_worker_core.bulk_read` pasa la pregunta.
- `FORMATO_RESPUESTA` sube de versión.
- `cheap_worker_extract.es_codigo(ruta) -> bool`.

## 8. Evaluación

`eval-bulk-read.py`: un caso `dato` u `orientacion` acierta solo si todos sus fragmentos esperados aparecen, normalizados, en las **líneas de cita** (las que empiezan por `>`) de las afirmaciones verificadas. `sin_respuesta` no cambia.

Medición real, fuera de git como la anterior: mismas preguntas, ventana de 8192 (la que Ollama sirve ahora), los cuatro modelos, revisión manual de las afirmaciones verificadas. Métricas añadidas al informe: reparto CPU/GPU de cada modelo (`ollama ps`). Cambiar `SHUNT_MODEL_BULK` en la configuración del usuario y borrar los modelos descartados requiere su decisión.

## 9. Pruebas del repositorio

- Los cuatro casos de la sección 6, como tests de `verificar` con documento `.md`.
- Números: "doce" frente a "12"; "treinta y dos" frente a "32"; "tres quintos" frente a "3/5"; "la mitad" frente a "1/2"; cifra en letras inventada ("quince días" con cita "treinta días") descartada.
- Cita sin palabras en común con la pregunta; afirmación sin palabras con contenido; pregunta sin palabras con contenido.
- Documento en inglés con palabras vacías inglesas.
- Código (`.py`): afirmación con cita de identificadores sigue pasando; "dos valores" frente a `return a, b` no se descarta por números.
- `es_codigo` y la igualdad de listas con el hook.
- `eval-bulk-read.py`: `acierta` usa solo las líneas de cita.

## 10. Documentación

README: los filtros de pertinencia, que solo aplican a documentos, los límites de la sección 2 y los nuevos resultados de la evaluación.
