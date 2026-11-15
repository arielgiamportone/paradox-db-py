# paradox-db-py

[![CI](https://github.com/arielgiamportone/paradox-db-py/actions/workflows/ci.yml/badge.svg)](https://github.com/arielgiamportone/paradox-db-py/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/paradoxdb.svg)](https://pypi.org/project/paradoxdb/)
![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![Licencia MIT](https://img.shields.io/badge/license-MIT-green)

**Lector y escritor en Python puro de tablas Paradox** (`.DB` datos, `.MB`
memos y BLOB, `.PX` índice primario), versiones 3.0 a 7. Sin código nativo, sin
dependencias copyleft, sin conversiones silenciosas: cada celda vuelve como
`OK`, `NULL` o `INVALID` junto con sus bytes crudos.

*English version: [README.md](https://github.com/arielgiamportone/paradox-db-py/blob/main/README.md).*

> Patrones desarrollados durante mi trabajo en Intervalor Data y publicados con autorización. Implementación independiente (clean-room) con datos sintéticos; no contiene código ni datos de la empresa ni de sus clientes.

---

## 1. El problema

Un sistema de gestión de activos fijos escrito en Delphi sobre el Borland
Database Engine guardaba décadas de histórico en tablas Paradox. Modernizarlo
exigía dos cosas a la vez:

1. **Extraer** todos los registros sin pérdida. No «casi todos»: el motor
   antiguo dejaba registros en los bloques de datos que la cabecera nunca
   contó, y columnas enteras de importes en BCD que ya no se decodificaban.
2. **Devolver tablas** a la aplicación antigua durante una transición larga,
   para que el sistema viejo y el nuevo convivieran meses.

El ecosistema Python solo ofrecía *bindings* sobre una librería en C (GPL) que
en la práctica es de solo lectura, impone una codificación global de texto,
se salta los registros que la cabecera no declara y corrompe su *heap* tras
unas decenas de escrituras. Para un producto *on-premise* nada de eso servía.

## 2. Restricciones

| Restricción | Consecuencia en el diseño |
|---|---|
| Se distribuye *on-premise* dentro de un producto comercial | Python puro, licencia MIT, cero dependencias en tiempo de ejecución |
| Las tablas reales contienen datos personales y patrimoniales de terceros | Todas las *fixtures* las genera `paradoxdb.synthetic`; el repo no contiene ningún `.DB` |
| El cifrado solo está implementado en código GPL | El descifrado se delega a un `Decryptor` externo opcional; la librería solo decodifica bytes en claro |
| El motor antiguo debe seguir abriendo lo que escribimos | El escritor copia una cabecera existente (plantilla) y solo reescribe contadores; produce ficheros nuevos, nunca modifica en sitio |
| Páginas de códigos mezcladas en un mismo conjunto (núcleo CP437, tablas posteriores CP1252) | Codepage por tabla, leída de la cabecera, nunca global |
| Importes contables y BCD de 32 dígitos | `Decimal` en todas partes; `float` solo como representación en disco |

## 3. Arquitectura

```
paradoxdb/
├── fields.py     FieldType, FieldDef, páginas de códigos, tamaños físicos
├── numeric.py    codificaciones que preservan el orden: entero y doble Paradox, BCD
├── values.py     CellValue(state, value, raw_bytes, reason); decode_value; encode_*
├── header.py     TableHeader, parse_header, patch_counters (0x06, 0x0A-0x10, 0x49)
├── blocks.py     recorrido de la cadena de bloques (todos los registros, anomalías) y empaquetado
├── memo.py       MemoPointer, MemoFile (bloques de clase 2 y 3), MemoWriter (clase 2)
├── table.py      open_table, ParadoxTable, Record, protocolo Decryptor
├── writer.py     write_table sobre plantilla, WriteRecord, legacy_sort_key
├── index.py      DataBlock, read_data_blocks, build_index, verify_index
├── profile.py    profile_table, export_csv, export_json
├── cli.py        paradoxdb profile | export | verify-index
└── synthetic.py  fábrica de tablas usada por tests y ejemplos
```

Lectura:

```
bytes .DB ──► parse_header ──► iter_blocks ──► decode_value por campo ──► Record
                 │                                   ▲
                 └── codepage, descriptores          └── MemoFile (.MB hermano, cualquier mayúscula)
```

Escritura:

```
cabecera .DB plantilla ──copia──► parchear contadores ──► pack_blocks(registros) ──► .DB nuevo
primer bloque del .MB plantilla ──► MemoWriter (una entrada de bloque único por memo) ──► .MB nuevo
cabecera .PX plantilla ──► build_index(read_data_blocks(.DB nuevo)) ──► .PX nuevo
```

### Inicio rápido

```python
from paradoxdb import open_table

table = open_table("ACTIVOS.DB")  # encuentra también activos.mb / ACTIVOS.MB
print(table.header.version_label, table.header.codec)

for record in table.records():  # todos los registros de todos los bloques
    amount = record["Importe"]  # búsqueda de campo sin distinguir mayúsculas
    if amount.is_ok:
        total = amount.value  # Decimal, date, int, bool, str, bytes
    else:
        print(amount.state, amount.reason, amount.raw_bytes.hex())

print(table.block_record_count(), table.declared_record_count, table.header_mismatch)
```

```python
from paradoxdb import WriteRecord, write_table, verify_index, read_data_blocks

records = [
    WriteRecord(r.raw_bytes, {3: r["Nota"].value.encode(table.codec)}) for r in table.records()
]
result = write_table("ACTIVOS.DB", "salida/ACTIVOS.DB", records, sort=False)
assert verify_index(result.px_path, read_data_blocks(result.db_path)) == []
```

```bash
paradoxdb profile ./tablas --output perfil.json    # agregados, SHA-256, descuadres
paradoxdb export ./tablas/ACTIVOS.DB --format csv  # valor, estado y motivo por celda
paradoxdb verify-index ./tablas/ACTIVOS.DB         # salida 1 si el .PX es incoherente
```

## 4. Decisiones y alternativas descartadas

| Decisión | Alternativa descartada | Motivo |
|---|---|---|
| Lector propio en Python puro | *Bindings* de la librería C | Licencia GPL, codificación global, salta registros no declarados, sin garantía de 64 bits en Linux |
| Descifrado delegado a un componente opcional | Reimplementar el cifrado | La única implementación pública es GPL; las tablas cifradas son una minoría |
| Estado explícito por celda más bytes crudos | `None` / `0` ante la duda | La versión anterior convertía importes basura y fechas centinela en `None`: pérdida silenciosa a gran escala |
| `Decimal` para dobles y BCD | `float` | Importes contables y BCD de 32 dígitos; la paridad aritmética con el legado la hace el consumidor |
| Codepage por tabla | `latin1` o `cp1252` global | `latin1` acepta cualquier byte pero lee mal `ñ` / `º` de tablas CP437; los conjuntos mezclan páginas |
| Cabecera copiada de una plantilla | Construir cabeceras desde cero | Conserva versión, descriptores, clave y codepage, también en tablas vacías, sin modelar campos oscuros |
| Siempre ficheros nuevos | Modificar en sitio | La librería C corrompía memoria tras ~50 escrituras en sitio |
| Una entrada de bloque único por memo | Bloques subasignados | Correctitud primero; el coste en tamaño se mide abajo y se planifica para v2 |
| Orden de origen por defecto, orden legado bajo demanda | Reordenar siempre | El motor antiguo acepta el orden original; la colación reproducida coincidió con la real en la mayoría de tablas, no en todas |
| Fichero completo en memoria | Lectura por bloques | Tablas de decenas de MB; revisar si aparecen cientos |

Las decisiones que la especificación dejaba abiertas (idioma de los mensajes,
definición del recuento de entradas del `.PX`, cómo compara palabras el test de
fugas, etc.) están en [docs/DECISIONES.md](https://github.com/arielgiamportone/paradox-db-py/blob/main/docs/DECISIONES.md) con su motivo.

## 5. Qué se midió y cómo reproducirlo

Todo lo siguiente se ejecuta sobre datos sintéticos generados por el propio
repositorio.

```bash
pip install paradoxdb           # paquete publicado, sin dependencias en tiempo de ejecución
pip install -e ".[dev]"        # o una copia de desarrollo
pytest                          # más de 100 tests, con propiedades de ida y vuelta (Hypothesis)
python examples/benchmark.py    # tiempos y crecimiento del .MB con una tabla de 50 000 filas
```

Resultados en un contenedor de clase portátil (Python 3.12):

| Medición | Valor |
|---|---|
| Abrir y decodificar 50 000 registros de varios campos | 1,3 s |
| Escribir `.DB` + `.PX` de esos registros sobre plantilla | 0,09 s |
| Verificar el `.PX` resultante de dos niveles | < 0,01 s |
| `.MB` escrito para 5 000 memos de ~150 bytes | 20 MiB para 0,7 MiB de contenido (×27) |

La batería de tests cubre todos los casos de la especificación original:
cabeceras con y sin extensión 4.0+, texto en CP437 y CP1252, cada clase de
campo con signo, nulo frente a cero, BCD ilegible, registros más allá del
recuento declarado, memos en sus tres formas, `.MB` ausente, tablas cifradas
con y sin descifrador, cabeceras incoherentes, ida y vuelta con memos e
índice, índices de varios niveles, colación legada (`NA, nz, Ñu, Oa, ob`),
claves repetidas, tablas vacías, autoincremento, índices secundarios sin
tocar, el verificador de índices y la salida del perfil y la CLI.

En el proyecto original el mismo diseño se validó contra unas 120 tablas
reales (unas 225 000 filas) con cero diferencias de bytes o de valor frente a
la librería C, y el verificador aceptó 106 de 108 índices originales (los otros
dos tenían recuentos corrompidos por escrituras interrumpidas). Esas tablas no
pueden publicarse; las cifras se dan solo como contexto.

## 6. Límites conocidos

- **Crecimiento del `.MB`.** Cada memo ocupa al menos un bloque de 4 KiB. Los
  bloques subasignados (clase 3) se leen pero no se escriben. Previsto para v2.
- **Aún no abierto en un BDE real.** Las idas y vueltas se validan con el
  propio lector; abrir los ficheros escritos en el motor antiguo está pendiente.
- **Índices secundarios** (`.Xnn`, `.Ynn`, `.XGn`, `.YGn`): se detectan e
  informan, nunca se regeneran.
- **Las tablas cifradas no se escriben**, y descifrarlas exige un componente
  externo.
- Recuento de índice ≤ 32 767 por entrada, BCD ≤ 32 dígitos; el texto que no
  cabe en un campo es un error, no un truncado.
- Los ficheros se cargan íntegros en memoria.

## 7. Cómo se construyó

Proceso *clean-room* en tres fases. La fase 1 produjo una especificación del
formato, el comportamiento y los invariantes a partir del código original
(privado), sin copiar código, identificadores ni datos. La fase 2, este
repositorio, se implementó en una sesión separada con acceso solo a esa
especificación y a documentación pública, usando un asistente de programación
con IA como pareja de programación; cada decisión no fijada por la
especificación está documentada. La fase 3 es una revisión independiente que
compara este repositorio con el original antes de publicarlo. La CI ejecuta
un test de fugas (`tests/test_no_leaks.py`) sobre el árbol y el historial de
git, además de `gitleaks`.

## 8. Atribución y licencia

Patrones desarrollados durante mi trabajo en Intervalor Data y publicados con autorización. Implementación independiente (clean-room) con datos sintéticos; no contiene código ni datos de la empresa ni de sus clientes.

Licencia MIT. Véanse [LICENSE](https://github.com/arielgiamportone/paradox-db-py/blob/main/LICENSE), [CHANGELOG.md](https://github.com/arielgiamportone/paradox-db-py/blob/main/CHANGELOG.md) y
[CONTRIBUTING.md](https://github.com/arielgiamportone/paradox-db-py/blob/main/CONTRIBUTING.md).
