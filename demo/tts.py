import sys, soundfile as sf, numpy as np
from kokoro import KPipeline
p = KPipeline(lang_code='e')
text = open('guion.txt').read().strip()
audio = np.concatenate([a for _,_,a in p(text, voice='ef_dora', speed=0.95)])
sf.write('voz.wav', audio, 24000); print(len(audio)/24000, 's')
