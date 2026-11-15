# Guion · paradox-db-py · lector y escritor Paradox en Python puro

Idioma: español · Voz: ElevenLabs, voz clonada del autor (eleven_v4) · Duración: 4 min 07 s · 8 escenas.

Cada escena indica el tipo de plano (diapositiva o terminal), lo que se ve y la narración literal que se envió a ElevenLabs.


## 1. paradox-db-py (diapositiva)

*Lector y escritor de tablas Paradox (.DB / .MB / .PX) en Python puro*

- Sin dependencias nativas ni copyleft · MIT
- Estado explícito por celda: OK / NULL / INVALID
- Primer caso de estudio clean-room publicado

**Narración:** Hola. En este vídeo os presento paradox-db-py: una librería en Python puro para leer y escribir tablas Paradox, el formato de base de datos de escritorio que todavía sostiene muchas aplicaciones Delphi de los años noventa. Es el primero de una serie de casos de estudio clean-room que publico con autorización de la empresa en la que trabajo.


## 2. El problema (diapositiva)

- Modernizar un sistema de activos fijos con el histórico en Paradox
- Extraer todos los registros sin pérdida y devolver tablas al sistema antiguo durante la transición
- Las opciones en Python: librería en C (GPL), codificación global, registros no declarados perdidos, memoria corrupta al escribir
- Producto instalado en las oficinas del cliente: nada de eso valía

**Narración:** Todo empieza con un problema real: modernizar un sistema de activos fijos cuyo histórico vivía en Paradox. Había que extraer todos los registros sin pérdida, y además devolver tablas al sistema antiguo durante la transición. Las opciones existentes en Python envuelven una librería en C con licencia GPL, imponen una codificación global, se saltan registros que la cabecera no declara y corrompen memoria al escribir. Para un producto que se instala en las oficinas del cliente, nada de eso valía.


## 3. Demo · instalar, generar una tabla sintética y leerla (terminal)

- `pip install paradoxdb`
- `python 01_make_table.py`
- `python 02_read.py`

**Narración:** Vamos a verlo funcionar. Instalo el paquete desde PyPI; no tiene dependencias. Como el repositorio no contiene ninguna tabla real, uso la fábrica sintética que incluye la propia librería para crear una tabla con varios campos. Después la abro con open_table y recorro sus registros: cada celda llega con su estado, su valor en Python y sus bytes crudos.


## 4. Demo · nulo no es cero; inválido conserva los bytes (terminal)

- `cat 03_cell_state.py`
- `python 03_cell_state.py`

**Narración:** Aquí está la idea central: ningún nulo ni valor corrupto se convierte en cero o en cadena vacía por el camino. Un entero codificado como cero es un cero válido; una fecha a ceros es nulo; y un decimal BCD con un dígito ilegal se devuelve como inválido, con el motivo y los bytes originales. Limpiar es decisión de quien consume los datos, no de la librería.


## 5. Demo · todos los bloques y el perfil de la tabla (terminal)

- `paradoxdb profile demo_tables --output profile.json`
- `python 04_summary.py`

**Narración:** Las tablas antiguas suelen tener registros que la cabecera no cuenta, porque una escritura interrumpida los dejó en los bloques sin actualizar el contador. En esta tabla la cabecera declara quinientos noventa y ocho registros, pero los bloques contienen seiscientos. La librería los lee todos y expone el descuadre. El comando profile resume cada tabla y cada campo: cuántos valores correctos, nulos e inválidos, sumas, rangos y una huella SHA-256, para conciliar sin exponer datos.


## 6. Demo · escribir sobre plantilla y verificar el índice (terminal)

- `python 05_write.py`
- `paradoxdb verify-index out/ASSETS.DB`

**Narración:** La escritura funciona sobre una plantilla: copia la cabecera de una tabla existente, cambia solo los contadores y genera ficheros nuevos, nunca modifica el original. Si la tabla tiene clave primaria y una plantilla de índice, reconstruye el .PX como árbol B. Y el verificador comprueba que cada hoja apunta al bloque correcto, con su primera clave y su recuento exactos.


## 7. Arquitectura y decisiones (diapositiva)

- fields · numeric · values → header · blocks · memo → table · writer · index → profile · cli
- Decimal en vez de float para importes y BCD
- Página de códigos por tabla, nunca global
- Cabecera plantilla en vez de construirla desde cero
- Descifrado delegado: sin copiar un algoritmo GPL

**Narración:** Por dentro son quince módulos pequeños: decodificadores con estado explícito, cabecera, cadena de bloques, memos, escritor, índice, perfil y línea de comandos. Las decisiones importantes están documentadas con la alternativa descartada: Decimal en vez de float para importes y BCD, página de códigos por tabla en vez de global, cabecera plantilla en vez de construirla desde cero, y descifrado delegado a un componente externo para no copiar un algoritmo GPL.


## 8. Clean-room en tres fases (diapositiva)

- 1 · Especificación sin código, escrita desde el original
- 2 · Implementación en una sesión sin acceso a la fuente, con un asistente de IA
- 3 · Revisión independiente contra la fuente: 0 fragmentos copiados
- 104 tests · CI en Python 3.11 y 3.12 · MIT · README ES/EN
- github.com/arielgiamportone/paradox-db-py · pip install paradoxdb

**Narración:** Y el método: proceso clean-room en tres fases. Primero se escribió una especificación del formato y del comportamiento, sin código. Después, en una sesión sin acceso al original, se implementó la librería a partir de esa especificación, con un asistente de programación con IA como pareja. Y por último una revisión independiente la comparó contra la fuente: cero fragmentos copiados. Ciento cuatro tests, integración continua en Python tres once y tres doce, licencia MIT y README en español e inglés. Está en GitHub y en PyPI: pip install paradoxdb. Gracias por ver el vídeo.

