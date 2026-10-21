"""macOS 打包：通用二进制只保留目标架构。"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
import build_macos  # noqa: E402


def _fat(slices: dict[int, bytes]) -> bytes:
    header = struct.pack(">II", 0xCAFEBABE, len(slices))
    offset, body, entries = 4096, b"", b""
    for cpu, data in slices.items():
        entries += struct.pack(">iiIII", cpu, 0, offset + len(body), len(data), 12)
        body += data
    return (header + entries).ljust(offset, b"\0") + body


def test_thin_binaries_keeps_target_slice(tmp_path):
    arm = b"\xcf\xfa\xed\xfe" + b"arm" * 100
    intel = b"\xcf\xfa\xed\xfe" + b"x86" * 200
    library = tmp_path / "lib" / "_objc.so"
    library.parent.mkdir()
    library.write_bytes(_fat({build_macos.CPU_TYPES["x86_64"]: intel, build_macos.CPU_TYPES["arm64"]: arm}))
    java = tmp_path / "Example.class"
    java.write_bytes(b"\xca\xfe\xba\xbe\x00\x00\x00\x34rest")  # 版本号 52，不是通用二进制
    saved = build_macos.thin_binaries(tmp_path, "arm64")
    assert library.read_bytes() == arm
    assert saved == len(_fat({1: intel, 2: arm})) - len(arm)
    assert java.read_bytes().endswith(b"rest")
