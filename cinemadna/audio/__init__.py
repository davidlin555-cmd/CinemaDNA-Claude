"""AudioDNA —— 声音性别绑定 / 真实 TTS / 口型实测 / 字幕 / 音量关系 + 专业审核。"""

from .service import (
    AudioDNAService,
    AudioReviewResult,
    audio_review,
    build_dialogue_vtt,
)
from .tts import ElevenLabsTTSBackend, TTSError, TTSResult
from .tts_edge import EdgeTTSBackend, EdgeTTSError
from .mixer import AMBIENCE_LIBRARY, AudioMixer, DialogueClip, MixError
from .registry import CharacterVoiceRegistry
from .bgm import BGM_MOODS, bed_for, mood_for_emotion
from .clone import CloneError, ElevenLabsVoiceCloner
from .lipsync import LipSyncBackend, PassthroughLipSync

__all__ = [
    "AudioDNAService", "AudioReviewResult", "audio_review", "build_dialogue_vtt",
    "ElevenLabsTTSBackend", "TTSResult", "TTSError",
    "EdgeTTSBackend", "EdgeTTSError",
    "AudioMixer", "DialogueClip", "MixError", "AMBIENCE_LIBRARY",
    "CharacterVoiceRegistry",
    "BGM_MOODS", "bed_for", "mood_for_emotion",
    "ElevenLabsVoiceCloner", "CloneError",
    "LipSyncBackend", "PassthroughLipSync",
]
