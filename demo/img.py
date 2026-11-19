import torch
from diffusers import StableDiffusionXLPipeline
p = StableDiffusionXLPipeline.from_pretrained("stabilityai/stable-diffusion-xl-base-1.0", torch_dtype=torch.float16, variant="fp16", use_safetensors=True)
p.enable_model_cpu_offload()
prompt = ("photorealistic portrait of Cleopatra VII, ancient Egyptian queen, looking directly at camera, frontal, "
 "head and shoulders, neutral closed mouth, olive skin, dark braided hair with gold diadem and cobra uraeus, "
 "gold broad collar necklace, inside dim palace TV debate studio, soft cinematic key light, 85mm, sharp focus, detailed skin")
neg = "cartoon, painting, illustration, open mouth, teeth, profile, side view, hands, text, watermark, blurry, deformed, celebrity"
for i,s in enumerate([7, 21, 42]):
    img = p(prompt, negative_prompt=neg, num_inference_steps=30, guidance_scale=6.5, width=1024, height=1024,
            generator=torch.Generator("cuda").manual_seed(s)).images[0]
    img.save(f"cleo_{i}.png")
