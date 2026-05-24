import os
import mimetypes
import requests

GATEWAY_URL = "http://127.0.0.1:8001"
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "")


def transcribe(path: str, user_id=None) -> str:
    """Transcribe an audio file via OpenAI Whisper, routed through the AI gateway."""
    content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        r = requests.post(
            f"{GATEWAY_URL}/proxy/openai/v1/audio/transcriptions",
            headers={
                "X-Internal-Token": INTERNAL_TOKEN,
                "X-App": "voice-journal",
                **({"X-User": str(user_id)} if user_id is not None else {}),
            },
            files={"file": (os.path.basename(path), f, content_type)},
            data={"model": "whisper-1", "response_format": "verbose_json"},
            timeout=120,
        )
    r.raise_for_status()
    return r.json().get("text", "").strip()
