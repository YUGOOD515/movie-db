#!/usr/bin/env bash
# 编译只含常见影视格式的精简 ffprobe（LGPL，静态单文件，约 8 MB）。
#
#   packaging/build_ffprobe.sh <FFmpeg 源码目录> <输出目录> [win64|macos-arm64|macos-x86_64|native]
#
# win64：在 Linux 上用 mingw-w64 交叉编译 Windows 版，https 走系统自带的 Schannel，不依赖 OpenSSL；
# macos-arm64 / macos-x86_64：在 macOS 上用 clang 编译（两种架构都能在 Apple 芯片上编译），
#   https 走系统自带的 Secure Transport，最低支持 macOS 11；
# native：编译本机版本，仅用于在 Linux 上核对格式覆盖（不带 TLS）。
#
# 项目只用 `ffprobe -show_format -show_streams -of json <链接>` 读取时长、码率、分辨率、帧率、
# 像素格式、HDR / 杜比视界、音频编码与声道，因此只保留：
# - 协议：file、http、https（AList 播放链接）
# - 封装：mkv/webm、mp4/mov、ts/m2ts、avi、flv、wmv/asf、mpg/vob、ogg、rmvb、原始 H.264/HEVC
# - 解析器与解码器：常见视频与音频编码（AV1 只用解析器：FFmpeg 自带的 AV1 解码器依赖硬件加速）
set -euo pipefail

SRC=$(cd "${1:?FFmpeg 源码目录}" && pwd)
OUT=${2:?输出目录}
TARGET=${3:-win64}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)

DEMUXERS=matroska,mov,mpegts,avi,flv,asf,mpegps,mpegvideo,ogg,rm,h264,hevc
PARSERS=h264,hevc,av1,vp8,vp9,mpeg4video,mpegvideo,vc1,rv34,aac,aac_latm,ac3,dca,mlp,flac,opus,vorbis,mpegaudio,cook
DECODERS=h264,hevc,av1,vp8,vp9,mpeg1video,mpeg2video,mpeg4,msmpeg4v3,vc1,wmv1,wmv2,wmv3,rv10,rv20,rv30,rv40,\
aac,aac_latm,ac3,eac3,dca,truehd,mlp,flac,opus,vorbis,mp2,mp3,alac,wmav2,wmapro,cook,\
pcm_s16le,pcm_s24le,pcm_bluray,pcm_dvd

flags=(
  --disable-everything --disable-autodetect --disable-doc --disable-debug
  --disable-programs --enable-ffprobe
  --disable-avdevice --disable-avfilter --disable-swscale --disable-swresample
  --disable-x86asm --enable-small --enable-static --disable-shared
  --enable-network --enable-protocol=file,http,https,tcp,tls
  --enable-demuxer="$DEMUXERS" --enable-parser="$PARSERS" --enable-decoder="$DECODERS"
  --extra-version=moviedb-slim
)
case "$TARGET" in
  win64)
    flags+=(--target-os=mingw32 --arch=x86_64 --cross-prefix=x86_64-w64-mingw32-
            --enable-schannel --extra-ldflags=-static --extra-libs="-lws2_32 -lsecur32 -lcrypt32 -lbcrypt")
    ;;
  macos-arm64|macos-x86_64)
    arch=${TARGET#macos-}
    flags+=(--target-os=darwin --arch="$arch" --enable-cross-compile --cc=clang
            --extra-cflags="-arch $arch -mmacosx-version-min=11.0" --extra-ldflags="-arch $arch -mmacosx-version-min=11.0"
            --enable-securetransport)
    ;;
  native) ;;
  *) echo "未知目标：$TARGET" >&2; exit 2 ;;
esac

build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT
cd "$build"
"$SRC/configure" "${flags[@]}" > configure.log || { tail -40 configure.log; tail -60 ffbuild/config.log; exit 1; }
exe=ffprobe; [ "$TARGET" = win64 ] && exe=ffprobe.exe
make -j"$(nproc 2>/dev/null || sysctl -n hw.ncpu)" "$exe" > make.log 2>&1 || { tail -60 make.log; exit 1; }
strip_tool=strip; [ "$TARGET" = win64 ] && strip_tool=x86_64-w64-mingw32-strip
"$strip_tool" -o "$OUT/$exe" "$exe"
# LGPL：随二进制附上许可证、源码地址与完整编译参数，便于他人重新编译替换。
cp "$SRC/COPYING.LGPLv2.1" "$OUT/LICENSE.txt"
version=$(cat "$SRC/VERSION" 2>/dev/null || cat "$SRC/RELEASE" 2>/dev/null || echo unknown)
{
  echo "精简版 ffprobe，基于 FFmpeg $version（LGPL 2.1+），由 MovieDB 的 packaging/build_ffprobe.sh 编译。"
  echo "源码：https://ffmpeg.org/releases/ffmpeg-$version.tar.xz"
  echo "编译参数：${flags[*]}"
} > "$OUT/SOURCE.txt"
ls -l "$OUT/$exe"
