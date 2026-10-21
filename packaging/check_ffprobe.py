"""核对精简版 ffprobe 与完整版读出的媒体参数是否一致（CI 用）。

    python packaging/check_ffprobe.py samples <目录>                 # 用完整版 ffmpeg 生成测试小样
    python packaging/check_ffprobe.py expect <目录> <expected.json>  # 用 FFPROBE_PATH（完整版）记录基准
    python packaging/check_ffprobe.py verify <目录> <expected.json> [--https]
        # 用 FFPROBE_PATH（精简版）逐个比对；--https 时经本地 HTTPS 服务（支持 Range）读取，
        # 与读取 AList 签名链接的方式一致。目录中需有 cert.pem / key.pem（自签名，ffprobe 默认不校验证书）。
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from connectors.media_probe import probe_video  # noqa: E402

VIDEO = "-f lavfi -i testsrc2=size=320x180:rate=24000/1001:duration=2"
AUDIO = "-f lavfi -i sine=frequency=440:duration=2:sample_rate=48000"
HDR = ("log-level=error:hdr10=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:"
       "master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400")
SAMPLES = {
    "h264_aac.mkv": "-c:v libx264 -pix_fmt yuv420p -c:a aac -ac 2",
    "h264_10bit_flac.mkv": "-c:v libx264 -pix_fmt yuv420p10le -c:a flac",
    "hevc10_hdr_eac3.mp4": f"-c:v libx265 -pix_fmt yuv420p10le -x265-params {HDR} -color_primaries bt2020 "
                           "-color_trc smpte2084 -colorspace bt2020nc -tag:v hvc1 -c:a eac3 -ac 6",
    "hevc_dts.mkv": "-c:v libx265 -x265-params log-level=error -pix_fmt yuv420p10le -c:a dca -strict -2 -ac 6",
    "hevc_truehd.mkv": "-c:v libx265 -x265-params log-level=error -c:a truehd -strict -2 -ac 6",
    "h264_pcm.mov": "-c:v libx264 -c:a pcm_s24le -ac 6",
    "mpeg2_ac3.ts": "-c:v mpeg2video -c:a ac3 -ac 6",
    "mpeg2.mpg": "-c:v mpeg2video -c:a mp2",
    "vp9_vorbis.webm": "-c:v libvpx-vp9 -deadline realtime -c:a libvorbis",
    "mpeg4_mp3.avi": "-c:v mpeg4 -c:a libmp3lame",
    "wmv2.wmv": "-c:v wmv2 -c:a wmav2",
    "rv20.rm": "-c:v rv20 -c:a ac3",
    "h264.flv": "-c:v libx264 -c:a aac",
}


def make_samples(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, codec in SAMPLES.items():
        command = f"ffmpeg -hide_banner -loglevel error -y {VIDEO} {AUDIO} {codec} {folder / name}"
        subprocess.run(command.split(), check=True)
    # AV1 编码慢，用更小的画面。
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=128x72:rate=24:duration=1", *AUDIO.split(), "-t", "1",
                    "-c:v", "libaom-av1", "-cpu-used", "8", "-c:a", "libopus", str(folder / "av1_opus.webm")], check=True)
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30", "-subj", "/CN=localhost",
                    "-keyout", str(folder / "key.pem"), "-out", str(folder / "cert.pem")], check=True,
                   capture_output=True)
    print(f"已生成 {len(SAMPLES) + 1} 个测试小样")


def media_files(folder: Path) -> list[Path]:
    return sorted(path for path in folder.iterdir() if path.suffix not in (".pem", ".json"))


class RangeHandler(SimpleHTTPRequestHandler):
    """支持单段 Range 请求，模拟网盘直链的随机读取。"""

    def log_message(self, *args):
        pass

    def send_head(self):
        header = self.headers.get("Range", "")
        path = Path(self.translate_path(self.path))
        if not header.startswith("bytes=") or not path.is_file():
            return super().send_head()
        size = path.stat().st_size
        start_text, _, end_text = header[6:].partition("-")
        start = int(start_text or 0)
        end = min(int(end_text) if end_text else size - 1, size - 1)
        if start >= size:
            self.send_error(416)
            return None
        handle = path.open("rb")
        handle.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self._remaining = end - start + 1
        return handle

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining > 0:
            chunk = source.read(min(65536, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)


def serve_https(folder: Path) -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(RangeHandler, directory=str(folder)))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(folder / "cert.pem", folder / "key.pem")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"https://127.0.0.1:{server.server_address[1]}/"


def probe_all(folder: Path, base: str | None = None) -> dict:
    results = {}
    for path in media_files(folder):
        started = time.monotonic()
        target = f"{base}{path.name}" if base else str(path)
        try:
            results[path.name] = probe_video(target)
        except ValueError as error:
            results[path.name] = {"error": str(error)}
        print(f"{path.name}: {time.monotonic() - started:.2f}s")
    return results


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("samples", "expect", "verify"):
        print(__doc__)
        return 2
    folder = Path(argv[1])
    if argv[0] == "samples":
        make_samples(folder)
        return 0
    if argv[0] == "expect":
        Path(argv[2]).write_text(json.dumps(probe_all(folder), ensure_ascii=False, indent=1), encoding="utf-8")
        return 0
    expected = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    base = serve_https(folder) if "--https" in argv else None
    actual = probe_all(folder, base)
    problems = []
    for name, want in expected.items():
        got = actual.get(name) or {}
        for key, value in want.items():
            if got.get(key) != value:
                problems.append(f"{name} · {key}：期望 {value!r}，实际 {got.get(key)!r}")
    for line in problems:
        print(line)
    print(f"{len(expected)} 个文件，{len(problems)} 处不一致")
    return 1 if problems else 0


if __name__ == "__main__":
    os.environ.setdefault("FFPROBE_TIMEOUT", "60")
    sys.exit(main(sys.argv[1:]))
