"""Windows 与 macOS 打包脚本内置 mpv 的测试（全部离线使用假归档）。"""

from __future__ import annotations

import hashlib
import io
import os
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
import build_macos  # noqa: E402
import build_windows  # noqa: E402


def _write_zip(path: Path, entries: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as bundle:
        for name, data in entries.items():
            bundle.writestr(name, data)
    return path


def _make_tar_gz(entries: list[tuple[str, bytes, int]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data, mode in entries:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = mode
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _write_macos_mpv_zip(
    path: Path,
    tar_entries: list[tuple[str, bytes, int]],
    *,
    tar_name: str = "mpv.tar.gz",
) -> Path:
    return _write_zip(path, {tar_name: _make_tar_gz(tar_entries)})


def _relative_files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def _expected_source_txt(source: str, sha256: str) -> str:
    version = build_windows.MPV_VERSION
    return (
        f"mpv {version}（GPLv2+，见 LICENSE.GPL 与 Copyright）\n"
        f"下载：{source}\n"
        f"SHA-256：{sha256}\n"
        f"源码：https://github.com/mpv-player/mpv/tree/v{version}\n"
    )


def test_verify_sha256_match_and_mismatch(tmp_path):
    sample = tmp_path / "sample.zip"
    sample.write_bytes(b"mpv-payload")
    actual = hashlib.sha256(b"mpv-payload").hexdigest()
    wrong = "0" * 64

    assert build_windows.verify_sha256(sample, actual, remove_on_mismatch=True) == actual
    assert sample.exists()

    with pytest.raises(SystemExit) as keep_err:
        build_windows.verify_sha256(sample, wrong, remove_on_mismatch=False)
    assert wrong in str(keep_err.value)
    assert actual in str(keep_err.value)
    assert sample.exists()

    with pytest.raises(SystemExit) as remove_err:
        build_windows.verify_sha256(sample, wrong, remove_on_mismatch=True)
    assert wrong in str(remove_err.value)
    assert actual in str(remove_err.value)
    assert not sample.exists()


def test_windows_install_mpv_filters_entries_and_writes_extras(tmp_path, monkeypatch):
    fake_zip = _write_zip(
        tmp_path / "cache" / "download.zip",
        {
            "mpv.exe": b"fake-mpv-binary",
            "vulkan-1.dll": b"fake-vulkan-dll",
            "mpv.com": b"ignored-com",
            "mpv-register.bat": b"ignored-bat",
            "doc/readme.txt": b"ignored-doc",
            "../evil.txt": b"should-never-escape",
        },
    )
    digest = build_windows.sha256_of(fake_zip)
    downloads: list[tuple[str, Path]] = []

    def fake_download(url: str, target: Path) -> Path:
        downloads.append((url, target))
        return fake_zip

    monkeypatch.setattr(build_windows, "download", fake_download)
    monkeypatch.setattr(build_windows, "MPV_WINDOWS_SHA256", digest)

    target = tmp_path / "stage" / "runtime" / "mpv"
    cache = tmp_path / "cache"
    version = build_windows.install_mpv(target, cache)

    assert version == "0.42.0"
    assert downloads == [(build_windows.MPV_WINDOWS_URL, cache / Path(build_windows.MPV_WINDOWS_URL).name)]
    assert _relative_files(target) == {
        "mpv.exe",
        "vulkan-1.dll",
        "config/mpv.conf",
        "LICENSE.GPL",
        "Copyright",
        "SOURCE.txt",
    }
    assert not (target.parent / "evil.txt").exists()
    assert (target / "mpv.exe").read_bytes() == b"fake-mpv-binary"
    assert (target / "vulkan-1.dll").read_bytes() == b"fake-vulkan-dll"
    assert (target / "config" / "mpv.conf").read_bytes() == (build_windows.MPV_ASSETS / "mpv.conf").read_bytes()
    assert (target / "LICENSE.GPL").read_bytes() == (build_windows.MPV_ASSETS / "LICENSE.GPL").read_bytes()
    assert (target / "Copyright").read_bytes() == (build_windows.MPV_ASSETS / "Copyright").read_bytes()
    assert (target / "SOURCE.txt").read_text(encoding="utf-8") == _expected_source_txt(
        build_windows.MPV_WINDOWS_URL, digest
    )


def test_windows_install_mpv_rejects_non_root_mpv_exe(tmp_path, monkeypatch):
    fake_zip = _write_zip(
        tmp_path / "nested.zip",
        {
            "sub/mpv.exe": b"wrong-location",
            "vulkan-1.dll": b"fake-vulkan-dll",
        },
    )
    monkeypatch.setattr(build_windows, "download", lambda url, target: fake_zip)
    monkeypatch.setattr(build_windows, "MPV_WINDOWS_SHA256", build_windows.sha256_of(fake_zip))

    with pytest.raises(SystemExit) as err:
        build_windows.install_mpv(tmp_path / "mpv", tmp_path / "cache")
    assert "mpv.exe" in str(err.value)


def test_windows_install_mpv_local_archive(tmp_path, monkeypatch):
    local_zip = _write_zip(
        tmp_path / "custom-mpv-windows.zip",
        {
            "mpv.exe": b"local-mpv",
            "vulkan-1.dll": b"local-vulkan",
        },
    )
    digest = build_windows.sha256_of(local_zip)

    def fail_download(url: str, target: Path) -> Path:
        raise AssertionError(f"传入本地 archive 时不应调用 download：{url}")

    monkeypatch.setattr(build_windows, "download", fail_download)
    monkeypatch.setattr(build_windows, "MPV_WINDOWS_SHA256", digest)

    target = tmp_path / "out" / "mpv"
    assert build_windows.install_mpv(target, tmp_path / "cache", archive=local_zip) == "0.42.0"
    assert (target / "SOURCE.txt").read_text(encoding="utf-8") == _expected_source_txt(
        "custom-mpv-windows.zip", digest
    )

    # 校验失败时不删除用户提供的本地文件
    monkeypatch.setattr(build_windows, "MPV_WINDOWS_SHA256", "f" * 64)
    with pytest.raises(SystemExit):
        build_windows.install_mpv(tmp_path / "out2" / "mpv", tmp_path / "cache", archive=local_zip)
    assert local_zip.exists()


def _patch_windows_main_deps(monkeypatch, tmp_path: Path):
    mpv_calls: list[tuple[Path, Path, Path | None]] = []
    run_calls: list[list[str]] = []

    monkeypatch.setattr(build_windows, "install_python", lambda target, cache: None)
    monkeypatch.setattr(build_windows, "install_packages", lambda target, cache: None)
    monkeypatch.setattr(build_windows, "install_ffprobe", lambda target, cache: "ffmpeg-fake.zip")
    monkeypatch.setattr(build_windows, "copy_app", lambda target: 1)
    monkeypatch.setattr(build_windows, "write_icon", lambda path: None)
    monkeypatch.setattr(build_windows, "make_launcher", lambda python, icon, version, cache: True)
    monkeypatch.setattr(build_windows, "write_uninstall_list", lambda app, target: target)

    def fake_install_mpv(target: Path, cache: Path, archive: Path | None = None) -> str:
        mpv_calls.append((target, cache, archive))
        target.mkdir(parents=True, exist_ok=True)
        (target / "mpv.exe").write_bytes(b"mpv")
        return build_windows.MPV_VERSION

    monkeypatch.setattr(build_windows, "install_mpv", fake_install_mpv)
    monkeypatch.setattr(build_windows.shutil, "which", lambda name: "/fake/makensis" if name == "makensis" else None)

    def fake_run(cmd, **kwargs):
        run_calls.append(list(cmd))
        for arg in cmd:
            if isinstance(arg, str) and arg.startswith("-DOUTFILE="):
                outfile = Path(arg.split("=", 1)[1])
                outfile.parent.mkdir(parents=True, exist_ok=True)
                outfile.write_bytes(b"fake-installer")

    monkeypatch.setattr(build_windows.subprocess, "run", fake_run)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    return mpv_calls, run_calls, summary


def test_windows_main_default_includes_mpv(tmp_path, monkeypatch):
    mpv_calls, run_calls, summary = _patch_windows_main_deps(monkeypatch, tmp_path)
    out = tmp_path / "dist"
    cache = tmp_path / "cache"
    monkeypatch.setattr(sys, "argv", ["build_windows.py", "--out", str(out), "--cache", str(cache)])

    assert build_windows.main() == 0
    assert len(mpv_calls) == 1
    assert mpv_calls[0][2] is None
    assert len(run_calls) == 1
    assert "-DNO_MPV" not in run_calls[0]
    assert "mpv 0.42.0" in (out / "MovieDB" / "VERSION.txt").read_text(encoding="utf-8").splitlines()
    assert "| 其中 mpv |" in summary.read_text(encoding="utf-8")


def test_windows_main_no_mpv(tmp_path, monkeypatch):
    mpv_calls, run_calls, summary = _patch_windows_main_deps(monkeypatch, tmp_path)
    out = tmp_path / "dist"
    cache = tmp_path / "cache"
    monkeypatch.setattr(sys, "argv", ["build_windows.py", "--out", str(out), "--cache", str(cache), "--no-mpv"])

    assert build_windows.main() == 0
    assert mpv_calls == []
    assert len(run_calls) == 1
    assert "-DNO_MPV" in run_calls[0]
    assert "mpv 未内置" in (out / "MovieDB" / "VERSION.txt").read_text(encoding="utf-8").splitlines()
    assert "| 其中 mpv |" not in summary.read_text(encoding="utf-8")


def test_windows_main_rejects_no_mpv_with_mpv_zip(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["build_windows.py", "--no-mpv", "--mpv-zip", "x"])
    with pytest.raises(SystemExit) as err:
        build_windows.main()
    assert err.value.code != 0


def test_macos_install_mpv_extracts_bundle_and_extras(tmp_path, monkeypatch):
    fake_zip = _write_macos_mpv_zip(
        tmp_path / "cache" / "mpv-macos.zip",
        [
            ("mpv.app/Contents/Info.plist", b"<plist/>", 0o644),
            ("mpv.app/Contents/MacOS/mpv", b"\xcf\xfa\xed\xfe-mpv", 0o755),
            ("mpv.app/Contents/MacOS/lib/libfoo.dylib", b"\xcf\xfa\xed\xfe-foo", 0o644),
            # 官方包里真实存在的占位文件，codesign 会把它们当成未签名的代码
            ("mpv.app/Contents/MacOS/.gitkeep", b"", 0o644),
            ("mpv.app/Contents/MacOS/._.gitkeep", b"apple-double", 0o644),
            ("mpv.app/Contents/MacOS/lib/.gitkeep", b"", 0o644),
            ("mpv.app/Contents/MacOS/lib/._.gitkeep", b"apple-double", 0o644),
        ],
    )
    digest = build_windows.sha256_of(fake_zip)
    url = build_macos.MPV_MACOS["arm64"][0]

    monkeypatch.setattr(build_macos, "download", lambda u, target: fake_zip)
    monkeypatch.setattr(build_macos, "MPV_MACOS", {**build_macos.MPV_MACOS, "arm64": (url, digest)})

    target = tmp_path / "MovieDB.app" / "Contents" / "Resources" / "runtime" / "mpv"
    version = build_macos.install_mpv(target, tmp_path / "cache", "arm64")
    assert [path.name for path in (target / "mpv.app").rglob("*") if path.name.startswith(".")] == []

    assert version == "0.42.0"
    mpv_bin = target / "mpv.app" / "Contents" / "MacOS" / "mpv"
    assert (target / "mpv.app" / "Contents" / "Info.plist").read_bytes() == b"<plist/>"
    assert mpv_bin.read_bytes() == b"\xcf\xfa\xed\xfe-mpv"
    assert (target / "mpv.app" / "Contents" / "MacOS" / "lib" / "libfoo.dylib").read_bytes() == b"\xcf\xfa\xed\xfe-foo"
    assert (target / "config" / "mpv.conf").is_file()
    assert (target / "LICENSE.GPL").is_file()
    assert (target / "Copyright").is_file()
    assert (target / "SOURCE.txt").read_text(encoding="utf-8") == _expected_source_txt(url, digest)
    if os.name != "nt":
        assert mpv_bin.stat().st_mode & 0o111


def test_macos_install_mpv_allows_dot_slash_and_local_archive(tmp_path, monkeypatch):
    local_zip = _write_macos_mpv_zip(
        tmp_path / "local-mpv-macos.zip",
        [
            ("./mpv.app/Contents/Info.plist", b"<plist/>", 0o644),
            ("./mpv.app/Contents/MacOS/mpv", b"\xcf\xfa\xed\xfe-mpv", 0o755),
        ],
    )
    digest = build_windows.sha256_of(local_zip)
    url = build_macos.MPV_MACOS["arm64"][0]

    def fail_download(u: str, target: Path) -> Path:
        raise AssertionError(f"传入本地 archive 时不应调用 download：{u}")

    monkeypatch.setattr(build_macos, "download", fail_download)
    monkeypatch.setattr(build_macos, "MPV_MACOS", {**build_macos.MPV_MACOS, "arm64": (url, digest)})

    target = tmp_path / "out" / "mpv"
    assert build_macos.install_mpv(target, tmp_path / "cache", "arm64", archive=local_zip) == "0.42.0"
    assert (target / "mpv.app" / "Contents" / "MacOS" / "mpv").is_file()
    assert (target / "SOURCE.txt").read_text(encoding="utf-8") == _expected_source_txt(
        "local-mpv-macos.zip", digest
    )

    monkeypatch.setattr(build_macos, "MPV_MACOS", {**build_macos.MPV_MACOS, "arm64": (url, "0" * 64)})
    with pytest.raises(SystemExit):
        build_macos.install_mpv(tmp_path / "out2" / "mpv", tmp_path / "cache", "arm64", archive=local_zip)
    assert local_zip.exists()


def test_macos_install_mpv_rejects_unexpected_and_traversal_entries(tmp_path, monkeypatch):
    bad_prefix_zip = _write_macos_mpv_zip(
        tmp_path / "bad-prefix.zip",
        [
            ("other/file", b"nope", 0o644),
            ("mpv.app/Contents/MacOS/mpv", b"mpv", 0o755),
        ],
    )
    url = build_macos.MPV_MACOS["arm64"][0]
    monkeypatch.setattr(build_macos, "download", lambda u, target: bad_prefix_zip)
    monkeypatch.setattr(
        build_macos,
        "MPV_MACOS",
        {**build_macos.MPV_MACOS, "arm64": (url, build_windows.sha256_of(bad_prefix_zip))},
    )

    with pytest.raises(SystemExit) as err:
        build_macos.install_mpv(tmp_path / "case1" / "mpv", tmp_path / "cache", "arm64")
    assert "other/file" in str(err.value)

    traversal_zip = _write_macos_mpv_zip(
        tmp_path / "traversal.zip",
        [
            ("mpv.app/../../evil", b"escaped", 0o644),
            ("mpv.app/Contents/MacOS/mpv", b"mpv", 0o755),
        ],
    )
    monkeypatch.setattr(build_macos, "download", lambda u, target: traversal_zip)
    monkeypatch.setattr(
        build_macos,
        "MPV_MACOS",
        {**build_macos.MPV_MACOS, "arm64": (url, build_windows.sha256_of(traversal_zip))},
    )

    target = tmp_path / "case2" / "nested" / "mpv"
    with pytest.raises((SystemExit, tarfile.FilterError)):
        build_macos.install_mpv(target, tmp_path / "cache", "arm64")
    assert not (target.parent / "evil").exists()
    assert not (target.parent.parent / "evil").exists()


def test_macos_install_mpv_missing_tar_or_binary(tmp_path, monkeypatch):
    url = build_macos.MPV_MACOS["x86_64"][0]

    no_tar_zip = _write_zip(tmp_path / "no-tar.zip", {"README.txt": b"empty"})
    monkeypatch.setattr(build_macos, "download", lambda u, target: no_tar_zip)
    monkeypatch.setattr(
        build_macos,
        "MPV_MACOS",
        {**build_macos.MPV_MACOS, "x86_64": (url, build_windows.sha256_of(no_tar_zip))},
    )
    with pytest.raises(SystemExit):
        build_macos.install_mpv(tmp_path / "case1" / "mpv", tmp_path / "cache", "x86_64")

    no_bin_zip = _write_macos_mpv_zip(
        tmp_path / "no-bin.zip",
        [("mpv.app/Contents/Info.plist", b"<plist/>", 0o644)],
    )
    monkeypatch.setattr(build_macos, "download", lambda u, target: no_bin_zip)
    monkeypatch.setattr(
        build_macos,
        "MPV_MACOS",
        {**build_macos.MPV_MACOS, "x86_64": (url, build_windows.sha256_of(no_bin_zip))},
    )
    with pytest.raises(SystemExit):
        build_macos.install_mpv(tmp_path / "case2" / "mpv", tmp_path / "cache", "x86_64")


def test_macos_main_rejects_no_mpv_with_mpv_zip(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["build_macos.py", "--no-mpv", "--mpv-zip", "x"])
    with pytest.raises(SystemExit) as err:
        build_macos.main()
    assert err.value.code != 0


def test_macos_main_default_and_no_mpv(tmp_path, monkeypatch):
    def run_main(extra_args: list[str], out_dir: Path):
        mpv_calls: list[tuple[Path, Path, str, Path | None]] = []
        monkeypatch.setattr(build_macos, "IS_MACOS", False)
        monkeypatch.setattr(build_macos, "install_python", lambda target, arch, cache: None)
        monkeypatch.setattr(build_macos, "install_packages", lambda target, arch, cache: None)
        monkeypatch.setattr(build_macos, "thin_binaries", lambda root, arch: 0)
        monkeypatch.setattr(build_macos, "copy_app", lambda target: 1)
        monkeypatch.setattr(build_macos, "precompile", lambda python_root, app: None)
        monkeypatch.setattr(build_macos, "move_python_executable", lambda python, macos: macos / "MovieDB-python")
        monkeypatch.setattr(build_macos, "build_launcher", lambda target, arch: None)
        monkeypatch.setattr(build_macos, "write_icon", lambda resources: None)
        monkeypatch.setattr(build_macos, "write_info", lambda contents, version: None)

        def fake_install_mpv(target: Path, cache: Path, arch: str, archive: Path | None = None) -> str:
            mpv_calls.append((target, cache, arch, archive))
            return build_windows.MPV_VERSION

        monkeypatch.setattr(build_macos, "install_mpv", fake_install_mpv)
        monkeypatch.setattr(
            sys,
            "argv",
            ["build_macos.py", "--arch", "arm64", "--out", str(out_dir), "--cache", str(tmp_path / "cache"), *extra_args],
        )
        assert build_macos.main() == 0
        version_txt = (out_dir / "macos-arm64" / "MovieDB.app" / "Contents" / "Resources" / "VERSION.txt").read_text(
            encoding="utf-8"
        )
        return mpv_calls, version_txt.splitlines()

    calls_default, lines_default = run_main([], tmp_path / "dist-default")
    assert len(calls_default) == 1
    assert calls_default[0][2:] == ("arm64", None)
    assert "mpv 0.42.0" in lines_default

    calls_no_mpv, lines_no_mpv = run_main(["--no-mpv"], tmp_path / "dist-no-mpv")
    assert calls_no_mpv == []
    assert "mpv 未内置" in lines_no_mpv


def test_macos_sign_order_inside_out(tmp_path, monkeypatch):
    bundle = tmp_path / "MovieDB.app"
    mpv_app = bundle / "Contents" / "Resources" / "runtime" / "mpv" / "mpv.app"
    mpv_bin = mpv_app / "Contents" / "MacOS" / "mpv"
    libfoo = mpv_app / "Contents" / "MacOS" / "lib" / "libfoo.dylib"
    python_bin = bundle / "Contents" / "MacOS" / "MovieDB-python"

    for path in (mpv_bin, libfoo, python_bin):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 16)

    calls: list[list[str]] = []
    monkeypatch.setattr(build_macos.subprocess, "run", lambda cmd, **kwargs: calls.append(list(cmd)))

    build_macos.sign(bundle)

    mpv_bin_calls = [i for i, cmd in enumerate(calls) if str(mpv_bin) in cmd]
    libfoo_calls = [i for i, cmd in enumerate(calls) if str(libfoo) in cmd]
    mpv_app_calls = [i for i, cmd in enumerate(calls) if cmd[-1] == str(mpv_app) and "--force" in cmd]
    bundle_sign_calls = [i for i, cmd in enumerate(calls) if cmd[-1] == str(bundle) and "--force" in cmd]

    assert mpv_bin_calls
    assert libfoo_calls
    assert len(mpv_app_calls) == 1
    assert len(bundle_sign_calls) == 1
    assert mpv_app_calls[0] > max(mpv_bin_calls + libfoo_calls)
    assert bundle_sign_calls[0] > mpv_app_calls[0]
    assert calls[-1] == ["codesign", "--verify", "--deep", "--strict", str(bundle)]


def test_macos_sign_nested_apps_deepest_first(tmp_path, monkeypatch):
    bundle = tmp_path / "MovieDB.app"
    outer_app = bundle / "Contents" / "Resources" / "runtime" / "mpv" / "mpv.app"
    inner_app = outer_app / "Contents" / "Helpers" / "Helper.app"
    inner_app.mkdir(parents=True)
    python_bin = bundle / "Contents" / "MacOS" / "MovieDB-python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 16)

    calls: list[list[str]] = []
    monkeypatch.setattr(build_macos.subprocess, "run", lambda cmd, **kwargs: calls.append(list(cmd)))

    build_macos.sign(bundle)

    inner_idx = next(i for i, cmd in enumerate(calls) if cmd[-1] == str(inner_app) and "--force" in cmd)
    outer_idx = next(i for i, cmd in enumerate(calls) if cmd[-1] == str(outer_app) and "--force" in cmd)
    bundle_idx = next(i for i, cmd in enumerate(calls) if cmd[-1] == str(bundle) and "--force" in cmd)
    assert inner_idx < outer_idx < bundle_idx
