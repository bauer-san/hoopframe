#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Render the officials' mic audio for a hoopframe take, offline, from its ref_call events.

One 16 kHz mono WAV per official (ref_REF-1.wav, ref_REF-2.wav), sample 0 at the take's frame 0, and ref_audio.json
(voices, levels, and where each call actually landed). The browser's speech synthesis cannot be recorded, so the
speech is made here with a local neural voice: a Piper ONNX model (https://github.com/rhasspy/piper; the multi-speaker
LibriTTS voice, CC BY 4.0, gives each official a different speaker) run with onnxruntime, its phonemes from espeak-ng.

Each mic hears, besides its official:
  - their whistle (a pea whistle: about 3 kHz with a trill), very loud, and the other official's, quieter;
  - the other official's voice, quieter and later (distance / speed of sound);
  - the gym: a crowd bed (pink noise, slowly varying, louder for a few seconds after a made basket), sneaker squeaks
    from players changing speed, ball bounces (from the ground truth's ball), all quieter with distance from the mic;
  - the official's own running (footfalls) and breath.
The gym's level is set so that the speech is --snr dB above everything that is not a whistle, measured over the
calls.

Needs numpy, scipy, onnxruntime and the espeak-ng program. Synthesized sounds only; no recordings are used.

  python tools/ref_audio.py <take folder> --voice en-us-libritts-high.onnx [--snr 10] [--seed 0] [--speakers 92,573]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import wave
from pathlib import Path

import numpy as np
from scipy import signal

SR = 16000
C_SOUND = 343.0
BALL_R = 0.12


# ---------------------------------------------------------------- speech
class PiperVoice:
    """A Piper ONNX voice, phonemized with espeak-ng (as Piper does: IPA, clause punctuation kept)."""

    def __init__(self, onnx: Path):
        import onnxruntime as ort
        self.cfg = json.loads(Path(str(onnx) + ".json").read_text())
        self.sess = ort.InferenceSession(str(onnx), providers=["CPUExecutionProvider"])
        self.ids = self.cfg["phoneme_id_map"]
        self.sr = self.cfg["audio"]["sample_rate"]
        self.inf = self.cfg.get("inference", {})

    def phonemes(self, text: str) -> list[str]:
        out = subprocess.run(["espeak-ng", "-q", "--ipa=3", "-v", self.cfg["espeak"]["voice"], text],
                             capture_output=True, text=True, check=True).stdout
        clauses = [c.strip() for c in out.splitlines() if c.strip()]
        return list(", ".join(clauses).replace("‍", ""))

    def speak(self, text: str, speaker: int, length_scale: float = 1.0) -> np.ndarray:
        ids = [*self.ids["^"]]
        for ph in self.phonemes(text):
            if ph in self.ids:
                ids += self.ids[ph] + self.ids["_"]
        ids += self.ids["$"]
        x = np.array([ids], dtype=np.int64)
        feeds = {"input": x, "input_lengths": np.array([x.shape[1]], dtype=np.int64),
                 "scales": np.array([self.inf.get("noise_scale", 0.667), length_scale, self.inf.get("noise_w", 0.8)],
                                    dtype=np.float32)}
        if self.cfg.get("num_speakers", 1) > 1:
            feeds["sid"] = np.array([speaker], dtype=np.int64)
        audio = self.sess.run(None, feeds)[0].squeeze().astype(np.float64)
        g = np.gcd(SR, self.sr)
        return signal.resample_poly(audio, SR // g, self.sr // g)


# ---------------------------------------------------------------- sounds
def pink(n: int, rng) -> np.ndarray:
    w = rng.standard_normal(n)
    f = np.fft.rfft(w)
    k = np.arange(len(f)); k[0] = 1
    x = np.fft.irfft(f / np.sqrt(k), n)
    return x / (np.std(x) + 1e-12)


def whistle(dur: float, rng) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    f0 = rng.uniform(2900, 3400)
    trill = rng.uniform(22, 32)                                # the pea: frequency and level wobble together
    phase = 2 * np.pi * np.cumsum(f0 + 160 * np.sin(2 * np.pi * trill * t)) / SR
    am = 0.75 + 0.25 * np.sin(2 * np.pi * trill * t)
    x = (np.sin(phase) + 0.3 * np.sin(2 * phase) + 0.1 * np.sin(3 * phase)) * am
    x += 0.15 * rng.standard_normal(len(t))                    # breath noise through it
    env = np.minimum(1, t / 0.02) * np.minimum(1, (dur - t) / 0.05)
    return x * env


def squeak(rng) -> np.ndarray:
    dur = rng.uniform(0.05, 0.16)
    t = np.arange(int(dur * SR)) / SR
    f = rng.uniform(1500, 2600) + rng.uniform(-900, 1200) * t / dur
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.sin(np.pi * t / dur) ** 2
    return x * (1 + 0.3 * rng.standard_normal(len(t)))


def thump(rng, low=140.0, dur=0.12) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    x = np.sin(2 * np.pi * rng.uniform(0.85, 1.15) * low * t) * np.exp(-t / 0.025)
    x[: int(0.003 * SR)] += rng.standard_normal(int(0.003 * SR)) * 0.6        # the slap
    return x


def place(buf: np.ndarray, x: np.ndarray, t: float, gain: float) -> None:
    i = int(round(t * SR))
    if i >= len(buf) or i + len(x) <= 0:
        return
    a, b = max(i, 0), min(i + len(x), len(buf))
    buf[a:b] += gain * x[a - i:b - i]


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0


# ---------------------------------------------------------------- take
def load_take(take: Path):
    ev = [json.loads(x) for x in open(take / "events.jsonl")]
    man = json.loads((take / "manifest.json").read_text())
    fps = man.get("fps", 30)
    gt = [json.loads(x) for x in open(take / "groundtruth.jsonl")]
    frames = man.get("frames") or len(gt)
    t_first = float(np.median([e["t"] - e["frame"] / fps for e in ev])) if ev else 0.0   # game time of frame 0
    return ev, gt, fps, frames, t_first


def positions(gt):
    """Per frame: officials {id: (x, y)}, players [(x, y, speed)], ball (x, y, z) or None (our axes: y = -z)."""
    out = []
    for g in gt:
        refs = {r["id"]: (r["pos"][0], -r["pos"][2]) for r in g.get("referees", [])}
        pl = [(p["pos"][0], -p["pos"][2], p.get("speed", 0.0)) for p in g["players"]]
        b = g.get("ball")
        bp = None
        if b is not None:
            q = b["pos"] if isinstance(b, dict) else b
            bp = (q[0], -q[2], q[1])
        out.append((refs, pl, bp))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("take", type=Path)
    ap.add_argument("--voice", type=Path, required=True, help="Piper .onnx (its .onnx.json next to it)")
    ap.add_argument("--speakers", default="92,573", help="speaker ids for REF-1,REF-2 (multi-speaker voices)")
    ap.add_argument("--snr", type=float, default=10.0, help="speech over the gym and the other official, dB")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, help="default: the take folder")
    a = ap.parse_args()
    out = a.out or a.take
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(a.seed)
    ev, gt, fps, frames, t_first = load_take(a.take)
    pos = positions(gt)
    n = int(frames / fps * SR)
    calls = [e for e in ev if e["type"] == "ref_call"]
    ref_ids = sorted({c["ref"] for c in calls} | {r for p in pos[:1] for r in p[0]})
    spk = dict(zip(ref_ids, [int(s) for s in a.speakers.split(",")]))
    pace = {rid: ls for rid, ls in zip(ref_ids, (1.0, 0.9))}       # REF-2 talks a little faster

    def at(t):                                                    # frame index of game time t
        return int(np.clip(round((t - t_first) * fps), 0, len(pos) - 1))

    def ref_xy(rid, t):
        return pos[at(t)][0].get(rid, (0.0, 0.0))

    voice = PiperVoice(a.voice)
    speech = {r: np.zeros(n) for r in ref_ids}      # each official's own voice, at their own mic
    whist = {r: np.zeros(n) for r in ref_ids}
    placed = []
    for c in calls:
        x = voice.speak(c["text"], spk[c["ref"]], pace[c["ref"]])
        x = x / (rms(x) + 1e-9) * 0.1
        t0 = c["t0"] - t_first
        place(speech[c["ref"]], x, t0, 1.0)
        w = None
        if c.get("whistle") is not None:
            w = whistle(rng.uniform(0.35, 0.8), rng)
            place(whist[c["ref"]], w, c["whistle"] - t_first, 0.6)
        placed.append({"seq": c["seq"], "ref": c["ref"], "t0": round(c["t0"], 4), "t1": round(c["t0"] + len(x) / SR, 4),
                       "whistle": c.get("whistle"), "text": c["text"]})

    # the gym, heard at each mic
    gym = {r: np.zeros(n) for r in ref_ids}
    crowd = pink(n, rng)
    b, aa = signal.butter(2, [150 / (SR / 2), 4000 / (SR / 2)], "band")
    crowd = signal.lfilter(b, aa, crowd)
    lvl = 1 + 0.35 * np.sin(2 * np.pi * np.arange(n) / SR / rng.uniform(20, 40))
    for e in ev:                                                  # a cheer after a make
        if e["type"] == "shot_result" and e.get("made"):
            i = int((e["t"] - t_first) * SR)
            k = np.arange(max(i, 0), min(i + 3 * SR, n))
            lvl[k] += 1.5 * np.exp(-(k - i) / SR / 1.0)
    crowd *= lvl / (rms(crowd) + 1e-9)
    # sneakers: a squeak when a player changes speed sharply; the ball: each bounce on the floor
    squeaks, bounces = [], []
    for k in range(1, len(pos)):
        t = t_first + k / fps
        for (x, y, s), (_, _, s0) in zip(pos[k][1], pos[k - 1][1]):
            if abs(s - s0) * fps > 6 and rng.random() < 0.15:
                squeaks.append((t, x, y))
        bp, b0 = pos[k][2], pos[k - 1][2]
        if bp and b0 and bp[2] < BALL_R + 0.06 <= b0[2]:
            bounces.append((t, bp[0], bp[1]))
    for r in ref_ids:
        g = gym[r]
        g += 0.5 * crowd
        for t, x, y in squeaks:
            rx, ry = ref_xy(r, t)
            d = max(np.hypot(x - rx, y - ry), 1.0)
            place(g, squeak(rng), t - t_first + d / C_SOUND, 0.6 / d)
        for t, x, y in bounces:
            rx, ry = ref_xy(r, t)
            d = max(np.hypot(x - rx, y - ry), 1.0)
            place(g, thump(rng), t - t_first + d / C_SOUND, 1.2 / d)
        steps = np.zeros(n)                                       # own footfalls while running, and breath
        for k in range(1, len(pos)):
            p1, p0 = pos[k][0].get(r), pos[k - 1][0].get(r)
            if p1 and p0 and np.hypot(p1[0] - p0[0], p1[1] - p0[1]) * fps > 2.0 and k % 9 == 0:
                place(steps, thump(rng, 90, 0.08), k / fps, 0.4)
        g += steps + 0.05 * pink(n, rng)

    # each mic: own speech + own whistle + the other official (voice and whistle) + the gym, at the asked SNR
    meta = {"take": str(a.take), "sample_rate": SR, "t_frame0": t_first, "snr_db": a.snr, "seed": a.seed,
            "voice": {"model": a.voice.name, "speakers": spk, "length_scale": pace,
                      "license": "see the voice's MODEL_CARD (LibriTTS: CC BY 4.0)"},
            "calls": placed, "mics": {}}
    for r in ref_ids:
        others = [o for o in ref_ids if o != r]
        leak = np.zeros(n)
        for o in others:
            d = np.median([np.hypot(*(np.array(pos[k][0].get(r, (0, 0))) - np.array(pos[k][0].get(o, (0, 0)))))
                           for k in range(0, len(pos), fps)]) if pos else 10.0
            dd = max(float(d), 2.0)
            leak += np.roll(speech[o], int(dd / C_SOUND * SR)) * (0.5 / dd)
            leak += np.roll(whist[o], int(dd / C_SOUND * SR)) * (1.0 / dd)
        active = np.zeros(n, bool)
        for c in placed:
            if c["ref"] == r:
                active[int((c["t0"] - t_first) * SR):int((c["t1"] - t_first) * SR)] = True
        noise = gym[r] + leak
        s_pow = rms(speech[r][active]) if active.any() else 0.1
        n_pow = rms(noise[active] if active.any() else noise)
        gain = s_pow / (n_pow + 1e-12) / 10 ** (a.snr / 20)
        mix = speech[r] + whist[r] + gain * noise
        snr = 20 * np.log10(rms(speech[r][active]) / (rms((gain * noise)[active]) + 1e-12)) if active.any() else None
        peak = np.max(np.abs(mix)) + 1e-9
        mix = mix / peak * 0.9 if peak > 0.9 else mix
        with wave.open(str(out / f"ref_{r}.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
            w.writeframes((np.clip(mix, -1, 1) * 32767).astype("<i2").tobytes())
        meta["mics"][r] = {"file": f"ref_{r}.wav", "seconds": n / SR, "calls": int(sum(c["ref"] == r for c in placed)),
                           "snr_measured_db": None if snr is None else round(float(snr), 2)}
    (out / "ref_audio.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(json.dumps(meta["mics"]))


if __name__ == "__main__":
    main()
