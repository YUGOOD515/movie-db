from flask import Flask
import pytest

from viewmodels.cards import build_source
from viewmodels.source_comparison import build_media_versions, build_source_options


@pytest.fixture
def source():
    app = Flask(__name__)
    app.add_url_rule("/play/<int:file_id>", endpoint="play_media_file")
    with app.test_request_context():
        def make(file_id=1, **changes):
            return build_source({
                "file_id": file_id, "alist_path": f"/TV/version-{file_id}.mkv",
                "duration_ms": 2_712_000, "video_width": 1920, "video_height": 1080,
                "video_codec": "hevc", "video_frame_rate": 23.976,
                "audio_codec": "aac", "audio_channel_layout": "stereo",
                "audio_channels": 2, "container_format": "matroska", **changes,
            })
        yield make


def test_shared_values_are_not_repeated_and_rows_use_same_difference_fields(source):
    options = build_source_options([
        source(), source(2, duration_ms=2_828_000, audio_codec="eac3", audio_channel_layout="5.1"),
        source(3, duration_ms=None, audio_codec=None, audio_channel_layout=None, audio_channels=None),
    ])
    assert [field["key"] for field in options[0]["comparison_fields"]] == ["duration", "audio_codec", "channels"]
    assert [[field["value"] for field in option["comparison_fields"]] for option in options] == [
        ["45:12", "AAC", "2.0"], ["47:08", "E-AC-3", "5.1"], ["未知", "未知", "未知"],
    ]
    assert [field["value"] for field in options[0]["common_metadata"]] == ["1080P", "HEVC", "23.976 fps"]


def test_seconds_and_frame_rates_differ_but_same_resolution_tier_is_shared(source):
    options = build_source_options([
        source(duration_ms=2_712_000),
        source(2, duration_ms=2_713_000, video_height=800, video_frame_rate=24),
    ])
    assert [field["value"] for field in options[0]["comparison_fields"]] == ["45:12", "23.976 fps"]
    assert [field["value"] for field in options[1]["comparison_fields"]] == ["45:13", "24 fps"]
    assert '1080P' in [field['value'] for field in options[0]['common_metadata']]


def test_equivalent_codecs_and_channel_counts_do_not_create_false_differences(source):
    options = build_source_options([
        source(video_codec="h265"), source(2, audio_channel_layout=None),
    ])
    assert all(not option["comparison_fields"] for option in options)
    assert "HEVC" in [field["value"] for field in options[0]["common_metadata"]]
    assert options[0]["source_id"] != options[1]["source_id"]
    assert options[0]["alist_path"] != options[1]["alist_path"]


def test_file_size_is_a_fallback_for_identical_technical_parameters(source):
    options = build_source_options([source(file_size_bytes=1024), source(2, file_size_bytes=2048)])
    assert [(field['key'],field['value']) for field in options[0]['comparison_fields']] == [('size','1.00 KiB')]
    assert options[1]["comparison_fields"][0]["value"] == "2.00 KiB"


def test_same_filenames_keep_their_source_directories_visible(source):
    options = build_source_options([
        source(alist_path="/A/S01E01.mkv"), source(2, alist_path="/B/S01E01.mkv"),
    ])
    assert [option["source_name"] for option in options] == ["/A/S01E01.mkv", "/B/S01E01.mkv"]


def test_unknown_fields_are_omitted_only_when_unknown_for_every_version(source):
    options = build_source_options([source(), source(2, video_codec=None)])
    assert [(field['key'],field['value']) for field in options[1]['comparison_fields']] == [('video_codec','未知')]
    assert "动态范围" not in [field["label"] for field in options[0]["common_metadata"]]
    assert build_source_options([]) == []
    assert build_source_options([source()])[0]["comparison_fields"] == []


@pytest.mark.parametrize('width,height,label',[(7680,4320,'8K'),(6144,3160,'6K'),(5120,2700,'5K'),(3840,1600,'4K'),(2560,1080,'2K'),(1920,800,'1080P'),(720,1280,'720P'),(640,480,'低清'),(None,None,'')])
def test_resolution_tiers_include_cropped_and_portrait_sources(width,height,label):
    from viewmodels.media_labels import resolution_label
    assert resolution_label(width,height)==label


def test_only_special_formats_use_nard_badges(source):
    fields = build_source_options([source(video_dynamic_range='Dolby Vision')])[0]['common_metadata']
    assert next(field for field in fields if field['key']=='dynamic_range')['asset'].endswith('/vis-dv.png')
    assert next(field for field in fields if field['key']=='dynamic_range')['icon'] == 'sun'
    assert next(field for field in fields if field['key']=='video_codec')['asset'] is None
    assert next(field for field in fields if field['key']=='channels')['icon'] == 'volume'


def test_comparison_columns_have_matching_rows_including_unknowns_and_file_size(source):
    options = build_source_options([
        source(video_codec=None, file_size_bytes=1024),
        source(2, video_codec='hevc', video_dynamic_range='HDR10', file_size_bytes=2048),
    ])
    keys = [[field['key'] for field in option['parameters']] for option in options]
    assert keys[0] == keys[1]
    assert 'size' in keys[0] and 'container' in keys[0]
    first = {field['key']: field['value'] for field in options[0]['parameters']}
    assert first['video_codec'] == '未知'
    assert first['dynamic_range'] == '未知'
    assert first['size'] == '1.00 KiB'


def test_full_versions_keep_common_values_exact_pixels_and_missing_probe_fields(source):
    options = build_source_options([
        source(video_height=800, video_pixel_format='yuv420p10le', bit_rate=8_000_000,
               probed_at='2026-10-28 12:00:00'),
        source(2, video_width=None, video_height=None, file_size_bytes=None),
    ])
    versions = build_media_versions(options, options[0])
    summaries = [{field['key']: field for field in version['fields']} for version in versions]
    assert list(summaries[0]) == list(summaries[1])
    assert summaries[0]['resolution']['value'] == '1080P · 1920 × 800'
    assert summaries[1]['resolution']['value'] == '未知'
    assert all(summary['video_codec']['value'] == 'HEVC' for summary in summaries)
    assert all(summary['dynamic_range']['value'] == '未知' for summary in summaries)
    assert summaries[1]['size']['value'] == '未知'
    details = {field['key']: field['value'] for field in versions[0]['fields']}
    assert details['pixel_format'] == 'yuv420p10le'
    assert details['probed_at'] == '2026-10-28 12:00:00'
    assert details['path'] == '/TV/version-1.mkv'
    assert [version['status'] for version in versions] == ['默认播放', '']


def test_full_version_recent_marker_and_brand_artwork_follow_the_correct_file(source):
    options = build_source_options([
        source(watched_at='2026-10-27 12:00:00'),
        source(2, watched_at='2026-10-28 12:00:00', video_dynamic_range='Dolby Vision', audio_codec='truehd'),
    ])
    versions = build_media_versions(options, options[1])
    assert [version['status'] for version in versions] == ['', '最近播放']
    fields = {field['key']: field for field in versions[1]['fields']}
    assert fields['dynamic_range']['asset'].endswith('/vis-dv.png')
    assert fields['audio_codec']['asset'].endswith('/aud-truehd.png')
    assert [version['play_url'] for version in versions] == [option['play_url'] for option in options]
    assert build_media_versions([], None) == []
