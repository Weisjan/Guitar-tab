import subprocess
import wave
from io import BytesIO

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool

app = FastAPI()

MAX_WAV_BYTES = 10_000_000
MAX_AUDIO_SECONDS = 30
CONVERSION_TIMEOUT_SECONDS = 10


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/convert", response_class=Response)
async def convert(request: Request) -> Response:
    wav_data = bytearray()
    async for chunk in request.stream():
        if len(wav_data) + len(chunk) > MAX_WAV_BYTES:
            raise HTTPException(413, "WAV exceeds 10 MB")
        wav_data.extend(chunk)

    mp3_data = await run_in_threadpool(wav_to_mp3, bytes(wav_data))
    return Response(content=mp3_data, media_type="audio/mpeg")


def wav_to_mp3(wav_data: bytes) -> bytes:
    try:
        with wave.open(BytesIO(wav_data), "rb") as wav:
            frames = wav.getnframes()
            if frames == 0:
                raise HTTPException(422, "WAV contains no audio")
            if frames > MAX_AUDIO_SECONDS * wav.getframerate():
                raise HTTPException(422, "WAV exceeds 30 seconds")

            expected_bytes = frames * wav.getnchannels() * wav.getsampwidth()
            if len(wav.readframes(frames)) != expected_bytes:
                raise HTTPException(422, "WAV audio data is incomplete")
    except (wave.Error, EOFError) as exc:
        raise HTTPException(422, "Expected a valid PCM WAV file") from exc

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-loglevel", "error",
                "-f", "wav", "-i", "pipe:0",
                "-c:a", "libmp3lame", "-f", "mp3", "pipe:1",
            ],
            input=wav_data,
            capture_output=True,
            check=True,
            timeout=CONVERSION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "Conversion timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(422, "FFmpeg could not convert this WAV") from exc

    return result.stdout