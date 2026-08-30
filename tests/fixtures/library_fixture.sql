INSERT INTO title_basic (
    title_id, tmdb_id, title_type, primary_title, original_title,
    original_language, display_title, start_year, end_year,
    runtime_minutes, genres, overview, tagline, average_rating,
    num_votes, added_at, updated_at
) VALUES
    ('tt0816692', 157336, 'movie', 'Interstellar', 'Interstellar',
     'en', '星际穿越', 2014, NULL, 169, 'Adventure,Drama,Sci-Fi',
     '一支探险队穿越虫洞寻找人类的新家园。', '爱是一种力量。', 8.7,
     2300000, '2026-08-20 10:00:00', '2026-08-20 10:00:00'),
    ('tt3783958', 313369, 'movie', 'La La Land', 'La La Land',
     'en', '爱乐之城', 2016, NULL, 128, 'Comedy,Drama,Music',
     '爵士钢琴家与演员在洛杉矶相遇。', NULL, 8.0,
     700000, '2026-08-19 10:00:00', '2026-08-19 10:00:00'),
    ('tt6226232', 71728, 'series', 'Young Sheldon', 'Young Sheldon',
     'en', '小谢尔顿', 2017, 2024, 22, 'Comedy',
     '少年谢尔顿与家人的成长故事。', NULL, 7.7,
     120000, '2026-08-18 10:00:00', '2026-08-18 10:00:00'),
    ('tt6719334', 1, 'episode', 'Pilot', 'Pilot', 'en', '试播集',
     2017, NULL, 22, 'Comedy', '谢尔顿开始高中生活。', NULL, 7.5,
     2000, '2026-08-18 10:00:00', '2026-08-18 10:00:00'),
    ('tt6914022', 2, 'episode', 'Rockets', 'Rockets', 'en', '火箭、共产主义与科学',
     2017, NULL, 22, 'Comedy', '谢尔顿研究火箭。', NULL, 7.6,
     1900, '2026-08-18 10:00:00', '2026-08-18 10:00:00'),
    ('tt11555014', 312, 'episode', 'Body Glitter', 'Body Glitter', 'en',
     '身体闪光器和商场安全装备', 2019, NULL, 20, 'Comedy',
     '一次商场里的家庭故事。', NULL, 7.8, 1300,
     '2026-08-18 10:00:00', '2026-08-18 10:00:00'),
    ('tt11591954', 313, 'episode', 'Contracts', 'Contracts', 'en',
     '合同，规则和一点猪脑', 2019, NULL, 20, 'Comedy',
     '谢尔顿学习合同与规则。', NULL, 8.0, 1500,
     '2026-08-18 10:00:00', '2026-08-18 10:00:00'),
    ('tt11599999', 314, 'episode', 'Next Episode', 'Next Episode', 'en',
     '下一集', 2019, NULL, 20, 'Comedy', '故事继续。', NULL, 8.1, 1400,
     '2026-08-18 10:00:00', '2026-08-18 10:00:00');

INSERT INTO title_relation (episode_id, series_id, season_number, episode_number)
VALUES
    ('tt6719334', 'tt6226232', 1, 1),
    ('tt6914022', 'tt6226232', 1, 2),
    ('tt11555014', 'tt6226232', 3, 12),
    ('tt11591954', 'tt6226232', 3, 13),
    ('tt11599999', 'tt6226232', 3, 14);

INSERT INTO title_artwork (title_id, artwork_type, position, local_path)
VALUES
    ('tt0816692', 'poster', 1, 'imdb.png'),
    ('tt0816692', 'poster', 2, 'DolbyVision.png'),
    ('tt0816692', 'landscape_poster', 1, 'imdb.png'),
    ('tt0816692', 'landscape_poster', 2, 'DolbyVision.png'),
    ('tt0816692', 'logo', 1, 'DolbyVision_white.png'),
    ('tt0816692', 'logo', 2, 'DolbyVision.png'),
    ('tt3783958', 'poster', 1, 'imdb.png'),
    ('tt3783958', 'landscape_poster', 1, 'imdb.png'),
    ('tt3783958', 'logo', 1, 'DolbyVision_white.png'),
    ('tt6226232', 'poster', 1, 'imdb.png'),
    ('tt6226232', 'landscape_poster', 1, 'imdb.png'),
    ('tt6226232', 'logo', 1, 'DolbyVision_white.png'),
    ('tt6719334', 'still', 1, 'imdb.png'),
    ('tt6914022', 'still', 1, 'imdb.png'),
    ('tt11555014', 'still', 1, 'imdb.png'),
    ('tt11591954', 'still', 1, 'imdb.png'),
    ('tt11599999', 'still', 1, 'imdb.png');

INSERT INTO media_file (
    file_id, title_id, alist_path, duration_ms, file_size_bytes, bit_rate,
    container_format, video_codec, video_width, video_height,
    video_frame_rate, video_pixel_format, video_dynamic_range,
    audio_codec, audio_channels, audio_channel_layout, probed_at
) VALUES
    (3, 'tt0816692', '/Movies/Interstellar.mkv', 10140000, 1000000000,
     8000000, 'matroska', 'hevc', 3840, 2160, 23.976, 'yuv420p10le',
     'Dolby Vision', 'eac3', 6, '5.1', '2026-08-20 10:00:00'),
    (11, 'tt6719334', '/TV/Young Sheldon/S01E01.mkv', 1200000, 200000000,
     3000000, 'matroska', 'hevc', 1920, 1080, 23.976, 'yuv420p', NULL,
     'aac', 2, 'stereo', '2026-08-20 10:00:00'),
    (12, 'tt6914022', '/TV/Young Sheldon/S01E02.mkv', 1200000, 200000000,
     3000000, 'matroska', 'hevc', 1920, 1080, 23.976, 'yuv420p', NULL,
     'aac', 2, 'stereo', '2026-08-20 10:00:00'),
    (13, 'tt11555014', '/TV/Young Sheldon/S03E12.mkv', 1200000, 200000000,
     3000000, 'matroska', 'hevc', 1920, 1080, 23.976, 'yuv420p', NULL,
     'aac', 2, 'stereo', '2026-08-20 10:00:00'),
    (14, 'tt11591954', '/TV/Young Sheldon/S03E13.mkv', 1200000, 200000000,
     3000000, 'matroska', 'hevc', 1920, 1080, 23.976, 'yuv420p', NULL,
     'aac', 2, 'stereo', '2026-08-20 10:00:00'),
    (15, 'tt11599999', '/TV/Young Sheldon/S03E14.mkv', 1200000, 200000000,
     3000000, 'matroska', 'hevc', 1920, 1080, 23.976, 'yuv420p', NULL,
     'aac', 2, 'stereo', '2026-08-20 10:00:00');

INSERT INTO playback_progress (file_id, position_ms, watched_at)
VALUES
    (13, 300000, '2026-08-29 10:00:00'),
    (14, 600000, '2026-08-29 12:00:00');

INSERT INTO collection (
    collection_id, name, collection_type, title_type, icon_name, "order"
) VALUES
    (1, '喜爱的电影', 'system', 'movie', 'film', 1),
    (2, '喜爱的剧集', 'system', 'series', 'tv', 2),
    (3, '稍后观看', 'custom', NULL, 'bookmark', 3);
