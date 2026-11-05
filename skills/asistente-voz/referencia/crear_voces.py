"""Crea voces ORIGINALES para Talia: mezcla 2-3 voces base (sintéticas), las sube de tono para que
suene más joven, genera una frase y la usa como nueva referencia (así la voz final es una sola,
estable, que no es ninguna de las bases). Corre en el procesador, una voz a la vez."""
import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
import torch

torch.set_num_threads(4)
from chatterbox.mtl_tts import ChatterboxMultilingualTTS, Conditionals, T3Cond

AQUI = Path(__file__).parent
BASES = AQUI / "bases"
SALIDA = AQUI / "chatterbox"
SALIDA.mkdir(exist_ok=True)

SEMILLA = "Bueno, a ver. Esto es lo que hay: lo hacemos bien, lo hacemos rápido, y nadie se queja. ¿Listo? Vamos."
TALIA = ("Ey... ¿volviste? Bueno, va rápido: la impresora uno terminó la carcasa, y quedó impecable. "
         "Mmm... la impresora dos sí me tiene pensando, va lenta. ¿La reviso, o la dejamos correr? "
         "Tú decides... pero yo que tú, la reviso.")

# nombre: (bases, semitonos más agudo, actitud 0.25-2)
VOCES = {
    "A": (["MX_Dalia", "US_Paloma"], 2.0, 0.7),
    "B": (["PE_Camila", "CO_Salome"], 3.5, 0.75),
    "C": (["US_Paloma", "VE_Paola", "AR_Elena"], 2.5, 0.85),
    "D": (["MX_Dalia", "CL_Catalina"], 3.0, 0.65),
    "E": (["VE_Paola", "US_Paloma"], 3.0, 1.0),
}

modelo = ChatterboxMultilingualTTS.from_pretrained("cuda" if torch.cuda.is_available() else "cpu")
SR = modelo.sr


def referencia(nombre: str, semitonos: float) -> Path:
    destino = SALIDA / f"ref_{nombre}_{semitonos:+.1f}.wav"
    if not destino.exists():
        y, sr = librosa.load(BASES / f"{nombre}.mp3", sr=24000)
        sf.write(destino, librosa.effects.pitch_shift(y, sr=sr, n_steps=semitonos), sr)
    return destino


def mezcla(bases: list[str], semitonos: float, actitud: float) -> Conditionals:
    conds = []
    for b in bases:
        modelo.prepare_conditionals(str(referencia(b, semitonos)), exaggeration=actitud)
        conds.append(modelo.conds)
    hablante = torch.stack([c.t3.speaker_emb for c in conds]).mean(0)
    hablante = hablante / hablante.norm() * conds[0].t3.speaker_emb.norm()
    gen = dict(conds[0].gen)
    gen["embedding"] = torch.stack([c.gen["embedding"] for c in conds]).mean(0)
    t3 = T3Cond(speaker_emb=hablante, cond_prompt_speech_tokens=conds[0].t3.cond_prompt_speech_tokens,
                emotion_adv=actitud * torch.ones(1, 1, 1))
    return Conditionals(t3, gen).to(modelo.device)


for clave in (sys.argv[1:] or VOCES):
    bases, semitonos, actitud = VOCES[clave]
    final = SALIDA / f"talia_{clave}.wav"
    if final.exists():
        continue
    modelo.conds = mezcla(bases, semitonos, actitud)
    semilla = SALIDA / f"semilla_{clave}.wav"
    sf.write(semilla, modelo.generate(SEMILLA, "es", exaggeration=actitud, cfg_weight=0.4).squeeze(0).cpu().numpy(), SR)
    # La frase generada con la mezcla pasa a ser LA voz (una sola, estable).
    modelo.prepare_conditionals(str(semilla), exaggeration=actitud)
    modelo.conds.save(SALIDA / f"voz_{clave}.pt")
    wav = modelo.generate(TALIA, "es", exaggeration=actitud, cfg_weight=0.35, temperature=0.8)
    sf.write(final, wav.squeeze(0).cpu().numpy(), SR)
    print("lista", clave, flush=True)
print("todas listas", flush=True)
