# Vídeo explicativo

Material para reproducir el vídeo de presentación de `paradoxdb` (4 min, 1080p,
español). El vídeo y los audios no se versionan; aquí está todo lo demás.

| Fichero | Qué es |
|---|---|
| `guion.json` | Escenas: tipo (diapositiva / terminal), contenido en pantalla y narración literal |
| `GUION.md` | El mismo guion en formato legible |
| `subtitulos.es.srt` | Subtítulos generados a partir del guion y la duración real de cada audio |
| `demo/*.py` | Los cinco scripts que se ejecutan en las escenas de terminal, sobre tablas sintéticas |
| `run_demo.sh` | Ejecuta la demo de verdad en un entorno limpio y captura la transcripción |
| `render.js` | Renderiza diapositivas (PNG) y graba las escenas de terminal (WebM) con Playwright |
| `build_video.py` | Monta los clips con ffmpeg, añade la voz y los subtítulos y produce el MP4 |

## Reproducir

```bash
# 1. Narración: un MP3 por escena (scene01.mp3 … scene08.mp3) con la voz que prefieras.
#    El original usa la voz clonada del autor en ElevenLabs (modelo eleven_v4), una toma por escena.
# 2. Demo real
./run_demo.sh /tmp/demo            # escribe /tmp/demo/transcript.txt
# 3. Montaje (requiere ffmpeg, Node y Playwright con Chromium)
python build_video.py --audio-dir AUDIO --transcript /tmp/demo/transcript.txt --work /tmp/build --out video.mp4
```

Las escenas de terminal no son una grabación de pantalla: `run_demo.sh` ejecuta
los comandos y guarda su salida real, y `render.js` la reproduce con animación de
escritura ajustada a la duración de la narración. Nada de lo que aparece procede
de datos reales: la tabla la genera `paradoxdb.synthetic`.
