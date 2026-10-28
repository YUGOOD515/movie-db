"""Shared technical labels and locally bundled badge artwork."""

def resolution_label(width, height):
    long, short = sorted((max(0, int(width or 0)), max(0, int(height or 0))), reverse=True)
    for name, wide, tall in (("8K", 7680, 4320), ("6K", 5760, 3240), ("5K", 5120, 2880),
                             ("4K", 3840, 2160), ("2K", 2560, 1440), ("1080P", 1920, 1080), ("720P", 1280, 720)):
        if long >= wide or short >= tall:
            return name
    return "低清" if long else ""


def codec(value):
    name = str(value or "").strip().upper()
    return {"H264": "H.264", "AVC": "H.264", "AVC1": "H.264", "H265": "HEVC", "H.265": "HEVC",
            "EAC3": "E-AC-3", "AC3": "AC-3"}.get(name, name)


def channels(source):
    layout = str(source.get("audio_channel_layout") or "").strip().lower()
    if layout:
        return {"stereo": "2.0", "mono": "1.0", "5.1(side)": "5.1", "7.1(wide)": "7.1"}.get(layout, layout)
    count = source.get("audio_channels")
    return {1: "1.0", 2: "2.0"}.get(count, f"{count} ch" if count else "")


def dynamic_range(value):
    value = str(value or "").strip().upper()
    return "DOLBY VISION" if "DOLBY" in value or value in ("DV", "DOVI") else value


BADGES = {
    "audio_codec": {"DTS": "aud-dts", "DTS-HD": "aud-dtshd", "DTS-HD MA": "aud-dtshdma",
                    "DTS:X": "aud-dtsx", "TRUEHD": "aud-truehd", "ATMOS": "aud-atmos"},
    "dynamic_range": {"DOLBY VISION": "vis-dv", "HDR": "vis-hdr", "HDR10": "vis-hdr10",
                      "HDR10+": "vis-hdr10plus"},
}


def chip(key, label, value):
    badge = BADGES.get(key, {}).get(value)
    return {"key": key, "label": label, "value": value or "未知",
            "asset": f"vendor/nard-badges/{badge}.png" if badge else None,
            "brand": badge,
            "icon": {"duration": "clock", "channels": "volume", "resolution": "monitor",
                     "video_codec": "file-code", "frame_rate": "gauge", "audio_codec": "music",
                     "dynamic_range": "sun", "size": "database", "container": "file"}.get(key)}
