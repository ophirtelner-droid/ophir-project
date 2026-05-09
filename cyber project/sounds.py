"""
sounds.py — Cross-platform audio for Quizy.
Generates tones entirely in Python (no sound files needed).
Silently disabled if pygame is not installed.
"""
import math
import struct

try:
    import pygame
    pygame.mixer.init(frequency=44100, size=-16, channels=1, buffer=512)
    _AVAILABLE = True
except Exception:
    _AVAILABLE = False

_cache: dict = {}


def _tone(freq: float, duration: float, volume: float = 0.45):
    """Build and cache a pygame Sound at the given frequency."""
    key = (freq, duration, volume)
    if key in _cache:
        return _cache[key]
    sample_rate = 44100
    n = int(sample_rate * duration)
    buf = bytearray(n * 2)
    fade = int(sample_rate * 0.03)   # 30 ms fade-out to remove clicks
    for i in range(n):
        t = i / sample_rate
        env = min(1.0, (n - i) / fade) if (n - i) < fade else 1.0
        val = int(volume * env * 32767 * math.sin(2 * math.pi * freq * t))
        struct.pack_into('<h', buf, i * 2, max(-32767, min(32767, val)))
    sound = pygame.mixer.Sound(buffer=bytes(buf))
    _cache[key] = sound
    return sound


def _play(freq: float, duration: float, volume: float = 0.45):
    if not _AVAILABLE:
        return
    try:
        _tone(freq, duration, volume).play()
    except Exception:
        pass


# ── Public API ────────────────────────────────────────────────────────────────

def login():        _play(660, 0.12)          # pleasant ding on sign-in
def success():      _play(880, 0.18)          # bright high tone
def error():        _play(200, 0.30)          # low buzz
def click():        _play(550, 0.05, 0.25)    # subtle tick
def submit_pass():  _play(1000, 0.28)         # bright — good score
def submit_fail():  _play(180, 0.40)          # dull  — low score
def delete():       _play(300, 0.20)          # mid-low thud
