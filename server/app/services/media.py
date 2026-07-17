import json
import struct
import subprocess
import zlib
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True)
class PreviewShot:
    id: str
    code: str
    title: str
    dialogue: str
    duration_sec: int
    image_path: Path


@dataclass(frozen=True)
class PreviewFiles:
    mp4: Path
    srt: Path
    vtt: Path
    manifest: Path
    duration_ms: int
    width: int
    height: int
    probe: dict[str, object]


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload))
    )


def deterministic_png_bytes(width: int, height: int, seed: str) -> bytes:
    digest = sha256(seed.encode()).digest()
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            mix = (x * 3 + y * 5) % 96
            rows.extend(
                (
                    (digest[0] + mix) % 256,
                    (digest[7] + (x * 2) // max(1, width // 64)) % 256,
                    (digest[15] + (y * 2) // max(1, height // 64)) % 256,
                )
            )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
        + _png_chunk(b"IEND", b"")
    )


def write_deterministic_png(path: Path, width: int, height: int, seed: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(deterministic_png_bytes(width, height, seed))


def build_candidate_images(tmp_dir: Path, seed: str) -> list[Path]:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for ordinal in (1, 2):
        output = tmp_dir / f"character-candidate-{ordinal}.png"
        write_deterministic_png(output, 480, 640, f"{seed}:candidate:{ordinal}")
        outputs.append(output)
    return outputs


def _timestamp(milliseconds: int, separator: str) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def _subtitle_files(tmp_dir: Path, shots: list[PreviewShot]) -> tuple[Path, Path]:
    srt_lines: list[str] = []
    vtt_lines = ["WEBVTT", ""]
    cursor = 0
    for index, shot in enumerate(shots, start=1):
        end = cursor + shot.duration_sec * 1000
        text = shot.dialogue.strip() or f"{shot.code} · {shot.title}（演示字幕）"
        srt_lines.extend(
            [
                str(index),
                f"{_timestamp(cursor, ',')} --> {_timestamp(end, ',')}",
                text,
                "",
            ]
        )
        vtt_lines.extend(
            [
                f"{_timestamp(cursor, '.')} --> {_timestamp(end, '.')}",
                text,
                "",
            ]
        )
        cursor = end
    srt = tmp_dir / "preview.srt"
    vtt = tmp_dir / "preview.vtt"
    srt.write_text("\n".join(srt_lines), encoding="utf-8")
    vtt.write_text("\n".join(vtt_lines), encoding="utf-8")
    return srt, vtt


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:] or "媒体命令失败")


def build_preview_files(
    tmp_dir: Path,
    *,
    project_id: str,
    project_name: str,
    aspect_ratio: str,
    shots: list[PreviewShot],
    hero_evidence: object | None = None,
) -> PreviewFiles:
    if not shots:
        raise ValueError("小样至少需要一个镜头")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    duration_sec = sum(shot.duration_sec for shot in shots)
    width, height = (720, 1280) if aspect_ratio == "9:16" else (1280, 720)
    concat = tmp_dir / "storyboards.ffconcat"
    concat_lines = ["ffconcat version 1.0"]
    for shot in shots:
        concat_lines.append(f"file '{shot.image_path.as_posix()}'")
        concat_lines.append(f"duration {shot.duration_sec}")
    concat_lines.append(f"file '{shots[-1].image_path.as_posix()}'")
    concat.write_text("\n".join(concat_lines), encoding="utf-8")

    mp4 = tmp_dir / "preview.mp4"
    scale = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    )
    _run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t",
            str(duration_sec),
            "-vf",
            scale,
            "-r",
            "24",
            "-c:v",
            "libx264",
            "-threads:v",
            "1",
            "-x264-params",
            "threads=1:lookahead_threads=1:sliced_threads=0",
            "-preset",
            "ultrafast",
            "-crf",
            "27",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-threads:a",
            "1",
            "-ar",
            "48000",
            "-b:a",
            "96k",
            "-shortest",
            "-fflags",
            "+bitexact",
            "-flags:v",
            "+bitexact",
            "-flags:a",
            "+bitexact",
            "-map_metadata",
            "-1",
            "-max_interleave_delta",
            "0",
            "-muxdelay",
            "0",
            "-muxpreload",
            "0",
            "-video_track_timescale",
            "24000",
            str(mp4),
        ]
    )
    probe_result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate",
            "-of",
            "json",
            str(mp4),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe_result.returncode != 0:
        raise RuntimeError(probe_result.stderr[-2000:] or "ffprobe 失败")
    probe = json.loads(probe_result.stdout)
    actual_duration = float(probe["format"]["duration"])
    if abs(actual_duration - duration_sec) >= 0.5:
        raise RuntimeError(f"小样时长校验失败：预期 {duration_sec} 秒，实际 {actual_duration} 秒")
    streams = probe.get("streams", [])
    if not any(
        item.get("codec_type") == "video" and item.get("codec_name") == "h264" for item in streams
    ):
        raise RuntimeError("小样缺少 H.264 视频流")
    if not any(
        item.get("codec_type") == "audio" and item.get("codec_name") == "aac" for item in streams
    ):
        raise RuntimeError("小样缺少 AAC 音频流")

    srt, vtt = _subtitle_files(tmp_dir, shots)
    manifest = tmp_dir / "preview-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "preview-manifest-v1",
                "project_id": project_id,
                "project_name": project_name,
                "timeline": [
                    {
                        "ordinal": index,
                        "shot_id": shot.id,
                        "code": shot.code,
                        "duration_ms": shot.duration_sec * 1000,
                        "temporary": True,
                    }
                    for index, shot in enumerate(shots, start=1)
                ],
                "provider": "mock",
                "is_temporary": True,
                "hero_shot": hero_evidence or {"requested": 0, "rendered": 0, "fallback": None},
                "media": {
                    "width": width,
                    "height": height,
                    "fps": 24,
                    "video_codec": "h264",
                    "audio_codec": "aac",
                    "duration_ms": round(actual_duration * 1000),
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return PreviewFiles(
        mp4=mp4,
        srt=srt,
        vtt=vtt,
        manifest=manifest,
        duration_ms=round(actual_duration * 1000),
        width=width,
        height=height,
        probe=probe,
    )
