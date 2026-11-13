import base64
from unittest.mock import Mock

import pytest

from connectors.drives.base import AuthError, DriveError, Entry
from connectors.drives.webdav import WebDAVDrive

MULTISTATUS = b"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/dav/%E5%BD%B1%E8%A7%86/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
  <d:response><d:href>/dav/%E5%BD%B1%E8%A7%86/Season%201/</d:href>
    <d:propstat><d:prop><d:displayname>Season 1</d:displayname><d:resourcetype><d:collection/></d:resourcetype>
    <d:getlastmodified>Wed, 12 Nov 2026 10:00:00 GMT</d:getlastmodified></d:prop></d:propstat></d:response>
  <d:response><d:href>/dav/%E5%BD%B1%E8%A7%86/A%20B.mkv</d:href>
    <d:propstat><d:prop><d:getcontentlength>42</d:getcontentlength><d:resourcetype/>
    <d:getlastmodified>Wed, 12 Nov 2026 10:00:00 GMT</d:getlastmodified></d:prop></d:propstat></d:response>
</d:multistatus>"""


def response(content, status=207):
    return Mock(status_code=status, content=content)


def drive(session, **config):
    return WebDAVDrive({"url": "https://nas.local:5006/dav", "username": "u", "password": "p",
                        "root_path": "/影视", **config}, Mock(), session)


def test_list_parses_multistatus_and_skips_self():
    session = Mock()
    session.request.return_value = response(MULTISTATUS)
    driver = drive(session)
    assert driver.root().id == "/dav/影视"
    entries = driver.list(driver.root())
    assert [(entry.name, entry.is_dir, entry.size) for entry in entries] == [("Season 1", True, 0), ("A B.mkv", False, 42)]
    assert entries[1].id == "/dav/影视/A B.mkv" and entries[1].modified.startswith("2026-11-12T10:00:00")
    method, url = session.request.call_args.args
    assert method == "PROPFIND" and url == "https://nas.local:5006/dav/%E5%BD%B1%E8%A7%86/"
    headers = session.request.call_args.kwargs["headers"]
    assert headers["Depth"] == "1" and headers["Authorization"] == "Basic " + base64.b64encode(b"u:p").decode()


def test_errors_and_entity_declarations_are_rejected():
    session = Mock()
    session.request.return_value = response(b"", 401)
    with pytest.raises(AuthError):
        drive(session).check()
    session.request.return_value = response(b"", 404)
    with pytest.raises(DriveError, match="不存在"):
        drive(session).check()
    session.request.return_value = response(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>')
    with pytest.raises(DriveError, match="无法识别"):
        drive(session).check()
    with pytest.raises(ValueError):
        WebDAVDrive({"url": "nas.local/dav"}, Mock(), session)


def test_link_quotes_path_and_carries_basic_auth():
    link = drive(Mock()).link(Entry("A B.mkv", False, "/dav/影视/A B.mkv"))
    assert link.url == "https://nas.local:5006/dav/%E5%BD%B1%E8%A7%86/A%20B.mkv"
    assert link.headers["Authorization"].startswith("Basic ") and link.proxy
