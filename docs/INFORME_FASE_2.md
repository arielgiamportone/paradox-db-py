# Informe de cierre · Fase 2 · `paradox-db-py`

**Fecha:** 2026-11-15 · **Paquete:** `paradoxdb` 0.1.0 · **Rama:** `ccr-b1febd6b-zg8wvk` (sin publicar, sin *releases*)

## 1. Qué partes de la especificación se implementaron

| Sección de la spec | Estado | Dónde |
|---|---|---|
| §3.1 Cabecera 3.x–7, extensión, codepage 0x6A, clave 0x25/0x5C, descriptores 0x58/0x78, área de nombre 79/261, coherencia | Completo | `header.py` |
| §3.2 Bloques: cadena desde 0x0E, cabecera de 6 bytes, capacidad, ciclos y bloques fuera de fichero como anomalías, lectura de todos los registros | Completo | `blocks.py`, `table.py` |
| §3.3 Todos los códigos de campo (0x01–0x18) con estado explícito por valor | Completo | `fields.py`, `values.py` |
| §3.4 Entero y doble Paradox, BCD de 17 bytes con complemento en negativos, preservación de orden | Completo, con propiedades Hypothesis | `numeric.py`, `tests/test_encoding.py` |
| §3.5 `.MB`: leader + puntero de 10 bytes, incrustado, bloque único (clase 2), subasignado (clase 3), fichero ausente, punteros incoherentes | Completo (lectura de las tres formas; escritura solo bloque único, como fija la spec) | `memo.py` |
| §3.6 `.PX`: cabecera, entradas clave + 3 cortos, hojas y nodos, numeración por generación con raíz al final, tabla vacía | Completo | `index.py` |
| §4 API pública: `FieldType`, `FieldDef`, `TableHeader`, `ValueState`, `CellValue`, `Record`, `Anomaly`, `open_table`, `ParadoxTable`, `MemoFile.read`, `decode_value`, `encode_*`, `Decryptor`, `WriteRecord`, `write_table`, `legacy_sort_key`, `build_index`, `verify_index`, `read_data_blocks`, `profile_table`, `export_csv/json`, CLI | Completo | `paradoxdb/__init__.py` reexporta todo |
| §5 Reglas e invariantes 1–15 | Completo; cada regla tiene al menos un test | `tests/` |
| §6 Casos de prueba (23 filas de la tabla) | Completo: los 23 casos tienen test | `tests/test_*.py` |
| §7 Decisiones de diseño | Recogidas en el README (las de la spec) y en `docs/DECISIONES.md` (las propias) | — |
| §8 Rendimiento | Medido con `examples/benchmark.py` sobre datos sintéticos | README §5 |
| §9 Riesgos de fuga | `tests/test_no_leaks.py` (lista en base64, barrido del árbol y de `git log -p`) + `gitleaks` en CI | — |

Generador sintético (`paradoxdb.synthetic`): cualquier combinación de campos,
versiones 4 (sin extensión) y 12 (con extensión), páginas 437/850/1252, bloques
de 1–2 KiB, contador 0x06 distinto del real, clave primaria, `.MB` con memos
incrustados/únicos/subasignados, plantilla `.PX` vacía, tablas vacías, filas
aleatorias con claves únicas.

## 2. Qué se dejó fuera y por qué

| Fuera | Motivo |
|---|---|
| Índices secundarios (`.Xnn`, `.Ynn`, `.XGn`, `.YGn`) | Fuera de alcance en la spec (§2). Se detectan y se devuelven en `WriteResult.skipped_indexes` y `ParadoxTable.secondary_indexes`. |
| Escritura de bloques subasignados en el `.MB` | Fuera de alcance (§2, §8). Coste medido: ×27 en el benchmark con memos de ~150 bytes. |
| Escritura de tablas cifradas y reimplementación del cifrado | Fuera de alcance; `write_table` lanza `EncryptedTableError`. |
| dBase, `.VAL`, `.TV`, `.SCH`, modificación en sitio | Fuera de alcance. |
| Lectura por bloques (streaming) | La spec fija fichero íntegro en memoria. |
| Validación en un BDE real | Imposible en este entorno; la spec lo marca como pendiente. Los round-trips se validan con el propio lector. |
| Comparación contra la librería C | No se instala ninguna dependencia GPL en el repo público; la spec aporta el resultado del proyecto original como contexto. |
| Ejecución local de `gitleaks` | El entorno de la sesión denegó ejecutar el binario descargado. La CI (`gitleaks/gitleaks-action@v2`, configurada desde el primer commit) lo ejecutó y pasó en verde. |

## 3. Decisiones propias

Las 21 decisiones no fijadas por la spec están en `docs/DECISIONES.md` con la
alternativa descartada y el motivo. Las más relevantes para la revisión:

- **D1** Mensajes en inglés; el caso «mencionando recuento» se comprueba con `count`.
- **D2** Autor en `pyproject.toml` = handle de GitHub, porque nombre y apellido están en la lista prohibida. **Pendiente de decisión del autor** antes de publicar.
- **D3** El test de fugas usa coincidencia de palabra completa sin distinguir mayúsculas y filtra las líneas `Author:` / `Co-Authored-By:` / `Claude-Session:` del historial. Motivo: varios términos de la lista son sustantivos comunes en español; como subcadena generarían falsos positivos en cualquier documentación. Consecuencia práctica: los documentos en español evitan deliberadamente esas palabras.
- **D4–D6** Coherencia de cabecera `.PX` (clave + 6), 0x06 del `.PX` = entradas de todos los niveles, bloques del `.PX` encadenados secuencialmente.
- **D7** El `.MB` se escribe siempre que la estructura tiene campos BLOB.
- **D11** El codificador BCD rechaza valores inexactos; el texto que no cabe es error.
- **D14** `--decrypt [MODULE:ATTR]` con *entry point* `paradoxdb.decryptors` como alternativa.
- **D18** La fábrica sintética forma parte del paquete publicado.

Además, durante la implementación: una cabecera `.MB` truncada no aborta
`open_table` (se expone en `ParadoxTable.memo_problem` y las celdas BLOB salen
`INVALID`).

## 4. Resultados de tests y calidad

| Comprobación | Resultado |
|---|---|
| `pytest` en Python 3.12 | 104 tests en verde (2,1 s) |
| `pytest` en Python 3.11 | 104 tests en verde |
| `ruff check .` y `ruff format --check .` | Sin incidencias |
| `tests/test_no_leaks.py` sobre árbol + `git log -p --all` | En verde (114 términos, 2 frases de atribución permitidas) |
| `gitleaks` | En verde en la CI (ejecución 1 de la rama); la ejecución local no fue posible en esta sesión |
| `python examples/benchmark.py` | 50 000 filas: lectura 1,3 s; escritura `.DB`+`.PX` 0,09 s; `.PX` de 2 niveles verifica; `.MB` ×27 con memos cortos |
| Paquete | `pip install -e ".[dev]"` con `hatchling`; `paradoxdb` y `python -m paradoxdb` funcionan; `py.typed` incluido |

Tamaño: ~3 000 líneas en `src/paradoxdb/` y ~1 650 en `tests/`.

Historial (un commit por paso del plan, más una corrección de robustez):

1. Esqueleto: `pyproject.toml`, LICENSE, CI, CONTRIBUTING, CHANGELOG, test de fugas.
2. Fábrica sintética y codificadores de bajo nivel.
3. Núcleo: lector, escritor, índice primario, perfil y exportación.
4. CLI: `python -m paradoxdb` y tests del parser (la CLI en sí entró con el núcleo).
5. README caso de estudio en inglés y español, `examples/benchmark.py`.
6. `.MB` ilegible no aborta la apertura.

Nota: el commit 3 se enmendó antes de publicar nada porque `docs/DECISIONES.md`
contenía una palabra de la lista prohibida (nombre de tabla del legado que
coincide con un sustantivo común en español); el propio test de fugas la
detectó.

## 5. Qué revisar en la fase 3

1. **Lista prohibida vs. vocabulario común.** Confirmar que la coincidencia por
   palabra completa (D3) es aceptable. Si se prefiere `grep -i` de subcadenas,
   hay que reescribir partes de la documentación en español y asumir falsos
   positivos en palabras inglesas corrientes.
2. **Autoría en `pyproject.toml` y `LICENSE`** (D2): el handle de GitHub figura
   como autor y titular del copyright. Decidir si se sustituye por el nombre
   real (y entonces excluirlo del test de fugas) antes de pasar a público.
3. **Constantes del formato.** Todas proceden de la spec (offsets de cabecera,
   0xFF00FF00, 0xC0/0x40 del BCD, 0x150 de los subasignados, 261/79 del área
   de nombre). Comprobar que ninguna constante «mágica» adicional coincida con
   la fuente sin estar en una norma pública.
4. **Nombres de identificadores.** Todos nuevos; comprobar coincidencias no
   triviales con la fuente (en especial `legacy_sort_key`, `read_data_blocks`,
   `build_index`, `verify_index`, `profile_table`, `WriteRecord`, que vienen
   literalmente de la pseudofirma de la spec §4).
5. **Cifras del proyecto original en el README** (§5): «unas 120 tablas»,
   «unas 225 000 filas», «106 de 108 índices». Están redondeadas y sin nombres,
   como permite §9; confirmar que son publicables.
6. **Frase de atribución**: aparece dos veces por README (cabecera y §8) en
   ambos idiomas, literal según la regla 8.
7. **Historial de git**: los *trailers* `Co-Authored-By` y `Claude-Session`
   identifican el asistente usado; la regla 11 habla de ficheros del repo
   (`CLAUDE.md`, `AGENTS.md`), no de mensajes de commit. Decidir si se
   mantienen o se reescribe el historial antes de publicar.
8. **Definición de 0x06 en el `.PX`** (D5): si la fase 3 tiene acceso a un
   `.PX` real, confirmar si el motor cuenta solo hojas o todas las entradas;
   cambiar `build_index` y `verify_index` es trivial (una línea cada uno).
9. **Compatibilidad real con BDE**: sigue pendiente, como en la spec.
