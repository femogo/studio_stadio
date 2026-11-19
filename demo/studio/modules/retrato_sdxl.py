"""Retrato: usa una imagen existente o genera una con SDXL."""
import shutil
import sys

META = {
    "name": "Retrato (SDXL)", "category": "Imagen", "icon": "🖼", "desc": "Imagen existente o retrato generado con SDXL.",
    "kind": "task", "exec": "proceso", "interprete": "pesado", "gpu": "exclusive",
    "inputs": [], "outputs": ["imagen", "semilla"], "weight": 10,
    "defaults": {"retry": {"max_attempts": 1}, "timeout": {"run": 1200, "idle": 300}},
}

CONFIG = [
    {"key": "modo", "label": "Modo", "type": "select", "default": "existente", "options": ["existente", "sdxl"]},
    {"key": "imagen", "label": "Imagen", "type": "path", "default": "cleo_0.png",
     "help": "Ruta absoluta o relativa a /opt/historico/demo (modo existente)"},
    {"key": "prompt", "label": "Prompt", "type": "textarea",
     "default": "photorealistic portrait of a historical figure, looking directly at camera, frontal, head and shoulders, neutral closed mouth, soft cinematic key light, 85mm, sharp focus"},
    {"key": "negativo", "label": "Prompt negativo", "type": "textarea",
     "default": "cartoon, painting, illustration, open mouth, teeth, profile, side view, hands, text, watermark, blurry, deformed, celebrity"},
    {"key": "semilla", "label": "Semilla", "type": "number", "default": 7},
    {"key": "pasos", "label": "Pasos", "type": "number", "default": 30},
    {"key": "guidance", "label": "Guidance", "type": "number", "default": 6.5},
]


def run(state, cfg, ctx):
    out = ctx.run_dir / "retrato.png"
    if cfg.get("modo", "existente") == "existente":
        src = ctx.path(cfg.get("imagen") or "cleo_0.png")
        if not src.exists():
            sys.exit(f"No existe la imagen {src}")
        shutil.copy(src, out)
        ctx.log(f"Usando retrato existente {src}")
        ctx.progress(1)
        return {"imagen": str(out)}
    ctx.progress(0.02)
    ctx.log("Cargando SDXL…")
    import torch
    from diffusers import StableDiffusionXLPipeline
    pipe = StableDiffusionXLPipeline.from_pretrained(
        "stabilityai/stable-diffusion-xl-base-1.0", torch_dtype=torch.float16, variant="fp16", use_safetensors=True)
    pipe.enable_model_cpu_offload()
    ctx.progress(0.2)
    pasos = int(cfg["pasos"])

    def cb(_p, i, _t, kw):
        ctx.progress(0.2 + 0.78 * (i + 1) / pasos)
        return kw

    img = pipe(prompt=cfg["prompt"], negative_prompt=cfg["negativo"], num_inference_steps=pasos,
               guidance_scale=float(cfg["guidance"]), width=1024, height=1024,
               generator=torch.Generator("cuda").manual_seed(int(cfg["semilla"])),
               callback_on_step_end=cb).images[0]
    img.save(out)
    ctx.log(f"Retrato guardado en {out}")
    ctx.progress(1)
    return {"imagen": str(out), "semilla": int(cfg["semilla"])}
