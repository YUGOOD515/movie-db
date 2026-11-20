"""首页：新装还没有作品时只显示一张引导卡，引向关联页。"""
import unittest

from viewmodels.home import build_home_viewmodel

EMPTY = {"featured": [], "recent_watch": [], "collections": [], "recent_added": [], "movie_genres": [],
         "series_genres": [], "unwatched": [], "top_movies": []}


class EmptyHomeTest(unittest.TestCase):
    def test_empty_library_flag(self):
        self.assertTrue(build_home_viewmodel(EMPTY)["empty_library"])

    def test_empty_home_page_links_to_association(self):
        import app as module
        with module.app.test_request_context("/"):
            html = module.render_template("index.html", page=build_home_viewmodel(EMPTY))
        self.assertIn("媒体库还是空的", html)
        self.assertIn("/media/sources", html)
        self.assertNotIn("尚无播放记录", html)


if __name__ == "__main__":
    unittest.main()


class EmptyOverviewTest(unittest.TestCase):
    def render(self, query="", title_type="all"):
        import app as module
        from viewmodels.manager import build_manager_viewmodel
        overview = build_manager_viewmodel({"titles": [], "artwork": [], "query": query, "title_type": title_type})
        self.assertEqual(overview["filtered"], bool(query) or title_type != "all")
        return overview

    def test_filter_flag(self):
        self.render()
        self.render(query="沙丘")
        self.render(title_type="movie")
