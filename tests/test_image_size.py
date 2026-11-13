"""评分图标等静态图片的固有尺寸：模板写进 <img width height>，加载前就占好位置。"""
from services import paths
from services.thumbnails import image_size


def test_svg_viewbox_and_png_header(tmp_path):
    (tmp_path / "icons").mkdir()
    (tmp_path / "icons" / "a.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 18"></svg>', encoding="utf-8")
    assert image_size("icons/a.svg", tmp_path) == (36, 18)
    assert image_size("imdb.png", tmp_path, paths.APP_STATIC) == (2000, 1001)


def test_unsafe_or_missing_paths_give_no_size(tmp_path):
    assert image_size("../app.py", paths.APP_STATIC) is None
    assert image_size("C:/Windows/win.ini", tmp_path) is None
    assert image_size("icons/missing.svg", tmp_path) is None
    assert image_size(None, tmp_path) is None


def test_every_rating_icon_has_a_size():
    for icon in (paths.APP_STATIC / "icons" / "ratings").glob("*.svg"):
        assert image_size(f"icons/ratings/{icon.name}", paths.APP_STATIC), icon.name
