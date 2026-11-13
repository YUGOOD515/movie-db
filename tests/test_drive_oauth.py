"""OneDrive、Google Drive、115 Open：续期、失效后重试、分页与取链接。"""

from unittest.mock import Mock

import pytest

from connectors.drives.base import AuthError, Entry
from connectors.drives.gdrive import GoogleDriveDrive
from connectors.drives.onedrive import OneDriveDrive
from connectors.drives.pan115 import Pan115Drive


def response(payload, status=200):
    item = Mock(status_code=status)
    item.json.return_value = payload
    return item


def renewed(access="a1", refresh="r2"):
    return response({"access_token": access, "refresh_token": refresh})


# ---- OneDrive ----

def test_onedrive_online_renewal_retry_pagination_and_link():
    session = Mock()
    saved = Mock()
    session.get.side_effect = [
        renewed("a1"),                                                     # 没有 access_token：先续期
        response({"error": {"code": "InvalidAuthenticationToken"}}, 401),  # 失效
        renewed("a2", "r3"),                                               # 再续期
        response({"value": [{"id": "1", "name": "电影", "folder": {}}], "@odata.nextLink": "https://next"}),
        response({"value": [{"id": "2", "name": "a.mkv", "size": 5, "file": {},
                             "fileSystemInfo": {"lastModifiedDateTime": "2026-11-12T10:00:00Z"}}]}),
        response({"@microsoft.graph.downloadUrl": "https://dl.invalid/a"}),
    ]
    driver = OneDriveDrive({"refresh_token": "r1"}, saved, session)
    entries = driver.list(driver.root())
    assert [(entry.name, entry.is_dir) for entry in entries] == [("电影", True), ("a.mkv", False)]
    assert session.get.call_args_list[0].kwargs["params"]["driver_txt"] == "onedrive_pr"
    assert saved.call_args.args[0]["refresh_token"] == "r3"
    assert "/me/drive/items/root/children" in session.get.call_args_list[3].args[0]
    link = driver.link(entries[1])
    assert link.url == "https://dl.invalid/a" and link.expires_in == 3000


def test_onedrive_own_client_and_custom_root():
    session = Mock()
    session.post.return_value = renewed("a1")
    session.get.return_value = response({"id": "ROOTID"})
    driver = OneDriveDrive({"refresh_token": "r1", "client_id": "c", "client_secret": "s", "region": "cn",
                            "root_path": "/影视"}, Mock(), session)
    assert driver.root().id == "ROOTID"
    assert session.post.call_args.args[0] == "https://login.chinacloudapi.cn/common/oauth2/v2.0/token"
    assert "root:/%E5%BD%B1%E8%A7%86" in session.get.call_args.args[0]


# ---- Google Drive ----

def test_gdrive_lists_skips_docs_resolves_shortcuts_and_links_with_token():
    session = Mock()
    session.get.side_effect = [
        renewed("a1"),
        response({"files": [
            {"id": "f", "name": "Movies", "mimeType": "application/vnd.google-apps.folder"},
            {"id": "d", "name": "notes", "mimeType": "application/vnd.google-apps.document"},
            {"id": "s", "name": "link.mkv", "mimeType": "application/vnd.google-apps.shortcut",
             "shortcutDetails": {"targetId": "t", "targetMimeType": "video/x-matroska"}},
        ], "nextPageToken": "p2"}),
        response({"files": [{"id": "v", "name": "a.mkv", "mimeType": "video/x-matroska", "size": "7"}]}),
        response({"error": {"code": 401}}, 401),
        renewed("a2"),
        response({"id": "v"}),
    ]
    driver = GoogleDriveDrive({"refresh_token": "r1"}, Mock(), session)
    entries = driver.list(driver.root())
    assert [(entry.name, entry.id, entry.is_dir) for entry in entries] == [
        ("Movies", "f", True), ("link.mkv", "t", False), ("a.mkv", "v", False)]
    assert entries[2].size == 7 and session.get.call_args_list[2].kwargs["params"]["pageToken"] == "p2"
    link = driver.link(entries[2])
    assert link.headers["Authorization"] == "Bearer a2" and "alt=media" in link.url


def test_gdrive_bad_refresh_token_is_auth_error():
    session = Mock()
    session.get.return_value = response({"text": "invalid_grant"})
    with pytest.raises(AuthError):
        GoogleDriveDrive({"refresh_token": "bad"}, Mock(), session).check()


# ---- 115 Open ----

def test_pan115_rotates_single_use_refresh_token_and_retries():
    session = Mock()
    saved = Mock()
    session.post.side_effect = [
        response({"state": 1, "code": 0, "data": {"access_token": "a1", "refresh_token": "r2"}}),
        response({"state": 1, "code": 0, "data": {"access_token": "a2", "refresh_token": "r3"}}),
    ]
    session.request.side_effect = [
        response({"state": False, "code": 40140125, "message": "access_token 无效"}),
        response({"state": True, "data": {"user_name": "yu"}}),
    ]
    driver = Pan115Drive({"refresh_token": "r1"}, saved, session)
    assert driver.check() == "已连接：yu"
    assert [call.kwargs["data"]["refresh_token"] for call in session.post.call_args_list] == ["r1", "r2"]
    assert saved.call_args.args[0] == {"refresh_token": "r3", "access_token": "a2"}

    session.post.side_effect = [response({"state": 0, "code": 40140119, "message": "refresh_token 无效"})]
    session.request.side_effect = [response({"state": False, "code": 99})]
    with pytest.raises(AuthError):
        driver.check()


def test_pan115_list_and_link_bind_user_agent():
    session = Mock()
    session.request.side_effect = [
        response({"state": True, "count": 2, "data": [
            {"fid": "1", "fn": "电影", "fc": "0"},
            {"fid": "2", "fn": "a.mkv", "fc": "1", "fs": 9, "pc": "pick", "upt": 1700000000},
        ]}),
        response({"state": True, "data": {"2": {"url": {"url": "https://cdn.invalid/a"}}}}),
    ]
    driver = Pan115Drive({"refresh_token": "r1", "access_token": "a1"}, Mock(), session)
    entries = driver.list(Entry("", True, "0"))
    assert [entry.is_dir for entry in entries] == [True, False] and entries[1].extra["pick_code"] == "pick"
    link = driver.link(entries[1])
    sent = session.request.call_args.kwargs
    assert sent["data"] == {"pick_code": "pick"} and link.headers["User-Agent"] == sent["headers"]["User-Agent"]
    assert link.url == "https://cdn.invalid/a"
