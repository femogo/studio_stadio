# Histórico: Estudio de agentes

Estudio de agentes (FastAPI + LangGraph + Ollama) y demo de vídeos con personajes históricos.

## Arranque del Estudio

```bash
cd demo/studio
python3 -m venv .venv-studio && .venv-studio/bin/pip install -r requirements.txt 
cp settings.example.json settings.json
./arrancar.sh
```

El token de acceso se genera solo en el primer arranque y se imprime en consola
(también queda en `settings.json`, que no se sube al repo). Por defecto escucha en `0.0.0.0`:
cambia `host` a `127.0.0.1` si no quieres exponerlo en la red.

## Demo de vídeo (opcional)

Usa [SadTalker](https://github.com/OpenTalker/SadTalker), que no se incluye aquí:

```bash
git clone https://github.com/OpenTalker/SadTalker.git demo/SadTalker
```

Las imágenes y vídeos generados con IA no se publican.
