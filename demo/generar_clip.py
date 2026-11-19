#!/usr/bin/env python
"""Genera un clip de un personaje histórico hablando a cámara.

Cadena: guion (texto fijo u Ollama) -> voz (Kokoro) -> retrato (imagen dada o SDXL)
        -> lip-sync (SadTalker) -> montaje 16:9 con rótulos (FFmpeg).

Ejemplos:
  python generar_clip.py --texto "Napoleón, tú llegaste a Egipto con cañones..." \
      --imagen cleo_0.png --nombre "CLEOPATRA VII" --subtitulo "Reina de Egipto (51–30 a.C.)"

  python generar_clip.py --tema "responde a Napoleón sobre quién fue mejor estratega" \
      --personaje "Cleopatra VII" --imagen cleo_0.png --nombre "CLEOPATRA VII" --revisar

  python generar_clip.py --texto "..." --prompt-imagen "photorealistic portrait of Napoleon..." \
      --voz em_alex --nombre "NAPOLEÓN BONAPARTE" --salida napoleon.mp4
"""
import argparse, json, shutil, subprocess, sys, tempfile, urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
SADTALKER = BASE / "SadTalker"
FONT_SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
FONT_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
NEG_PROMPT = ("cartoon, painting, illustration, open mouth, teeth, profile, side view, hands, "
              "text, watermark, blurry, deformed, celebrity")


def log(msg):
    print(f"\n==> {msg}", flush=True)


def guion_ollama(tema, personaje, palabras, modelo):
    prompt = (f"Eres {personaje} en un debate histórico televisado. {tema}. "
              f"Responde con UNA intervención de unas {palabras} palabras, en español, en primera persona, "
              "con datos históricos reales y tono propio del personaje. Solo el texto hablado, sin comillas ni acotaciones.")
    req = urllib.request.Request("http://localhost:11434/api/generate",
                                 data=json.dumps({"model": modelo, "prompt": prompt, "stream": False,
                                                  "keep_alive": 0}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)["response"].strip().strip('"«»')


def revisar(texto):
    print(f"\nGuion propuesto:\n  {texto}\n")
    nuevo = input("Enter para aceptar, o escribe el texto corregido: ").strip()
    return nuevo or texto


def voz_kokoro(texto, voz, velocidad, salida):
    import numpy as np, soundfile as sf
    from kokoro import KPipeline
    pipe = KPipeline(lang_code="e")  # español
    audio = np.concatenate([a for _, _, a in pipe(texto, voice=voz, speed=velocidad)])
    sf.write(salida, audio, 24000)
    return len(audio) / 24000


def retrato_sdxl(prompt, semilla, salida):
    import torch
    from diffusers import StableDiffusionXLPipeline
    pipe = StableDiffusionXLPipeline.from_pretrained(
        "stabilityai/stable-diffusion-xl-base-1.0", torch_dtype=torch.float16,
        variant="fp16", use_safetensors=True)
    pipe.enable_model_cpu_offload()
    prompt += ", looking directly at camera, frontal, head and shoulders, neutral closed mouth, photorealistic, sharp focus"
    img = pipe(prompt, negative_prompt=NEG_PROMPT, num_inference_steps=30, guidance_scale=6.5,
               width=1024, height=1024, generator=torch.Generator("cuda").manual_seed(semilla)).images[0]
    img.save(salida)
    del pipe
    torch.cuda.empty_cache()


def lipsync(imagen, audio, tmp, tam, mejorar, quieto):
    res = tmp / "sadtalker"
    cmd = [sys.executable, "inference.py", "--driven_audio", str(audio), "--source_image", str(imagen),
           "--result_dir", str(res), "--preprocess", "full", "--size", str(tam), "--expression_scale", "1.1"]
    if quieto:
        cmd.append("--still")
    if mejorar:
        cmd += ["--enhancer", "gfpgan"]
    subprocess.run(cmd, cwd=SADTALKER, check=True)
    videos = sorted(res.glob("*.mp4"))
    if not videos:
        sys.exit("SadTalker no produjo vídeo")
    return videos[-1]


def esc(t):  # escapar texto para drawtext
    return t.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")


def montaje(video, nombre, subtitulo, aviso, salida):
    f = ("[0:v]split[a][b];"
         "[a]scale=1920:1920,crop=1920:1080,boxblur=30:3,eq=brightness=-0.15[bg];"
         "[b]scale=1080:1080[fg];[bg][fg]overlay=420:0")
    if nombre:
        f += (",drawbox=x=0:y=850:w=1100:h=130:color=black@0.65:t=fill"
              ",drawbox=x=0:y=850:w=12:h=130:color=0xC9A227:t=fill"
              f",drawtext=fontfile={FONT_SERIF}:text='{esc(nombre)}':fontcolor=0xE8C766:fontsize=52:x=40:y=868")
        if subtitulo:
            f += f",drawtext=fontfile={FONT_SANS}:text='{esc(subtitulo)}':fontcolor=white:fontsize=30:x=42:y=932"
    if aviso:
        f += (f",drawtext=fontfile={FONT_SANS}:text='{esc(aviso)}':fontcolor=white@0.8:fontsize=24"
              ":x=w-tw-30:y=30:box=1:boxcolor=black@0.5:boxborderw=10")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-filter_complex", f,
                    "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                    "-movflags", "+faststart", str(salida)], check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--texto", help="Texto exacto que dirá el personaje")
    g.add_argument("--tema", help="Instrucción para que Ollama escriba la intervención")
    ap.add_argument("--personaje", default="Cleopatra VII", help="Personaje (para --tema)")
    ap.add_argument("--palabras", type=int, default=14, help="Longitud aprox. (≈2.6 palabras/s)")
    ap.add_argument("--modelo", default="qwen2.5:7b", help="Modelo Ollama")
    ap.add_argument("--revisar", action="store_true", help="Pedir confirmación/corrección del guion")
    i = ap.add_mutually_exclusive_group()
    i.add_argument("--imagen", type=Path, help="Retrato existente (frontal, boca cerrada)")
    i.add_argument("--prompt-imagen", help="Generar retrato con SDXL a partir de este prompt")
    ap.add_argument("--semilla", type=int, default=7)
    ap.add_argument("--voz", default="ef_dora", help="Voz Kokoro: ef_dora (mujer), em_alex / em_santa (hombre)")
    ap.add_argument("--velocidad", type=float, default=0.95)
    ap.add_argument("--nombre", default="", help="Rótulo principal (vacío = sin rótulo)")
    ap.add_argument("--subtitulo", default="")
    ap.add_argument("--aviso", default="Recreación ficticia generada con IA")
    ap.add_argument("--tam", type=int, choices=[256, 512], default=512)
    ap.add_argument("--sin-mejora", action="store_true", help="No usar GFPGAN (más rápido)")
    ap.add_argument("--mover-cabeza", action="store_true", help="Quitar --still (más movimiento, más artefactos)")
    ap.add_argument("--salida", type=Path, default=Path("clip.mp4"))
    a = ap.parse_args()

    if not a.imagen and not a.prompt_imagen:
        a.imagen = BASE / "cleo_0.png"
    a.salida = a.salida.resolve()

    tmp = Path(tempfile.mkdtemp(prefix="clip_", dir=BASE))
    try:
        if a.texto:
            texto = a.texto
        else:
            log(f"Guion con {a.modelo}")
            texto = guion_ollama(a.tema, a.personaje, a.palabras, a.modelo)
        if a.revisar:
            texto = revisar(texto)
        print(f"Guion: {texto}")
        (a.salida.with_suffix(".txt")).write_text(texto + "\n")

        log(f"Voz ({a.voz})")
        audio = tmp / "voz.wav"
        print(f"Duración: {voz_kokoro(texto, a.voz, a.velocidad, audio):.2f} s")

        if a.prompt_imagen:
            log("Retrato con SDXL")
            imagen = a.salida.with_suffix(".png")
            retrato_sdxl(a.prompt_imagen, a.semilla, imagen)
        else:
            imagen = a.imagen.resolve()

        log("Lip-sync con SadTalker")
        bruto = lipsync(imagen, audio.resolve(), tmp.resolve(), a.tam, not a.sin_mejora, not a.mover_cabeza)

        log("Montaje")
        montaje(bruto, a.nombre, a.subtitulo, a.aviso, a.salida)
        print(f"\nListo: {a.salida.resolve()}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
