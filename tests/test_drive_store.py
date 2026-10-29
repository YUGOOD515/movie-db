import json

from models import drive_store


def test_drive_roundtrip_update_rename_delete_and_public_config(tmp_path):
    database = tmp_path / "library.db"
    drive_store.save_drive("quark", "quark", {"token": "secret", "root": "0"}, True, database)
    drive_store.set_status("quark", "已连接", database)
    drive_store.save_drive("local", "local", {"root": "/media"}, False, database)

    assert [row["mount"] for row in drive_store.list_drives(database)] == ["local", "quark"]
    assert drive_store.get_drive("local", database)["enabled"] is False

    drive_store.update_config("quark", {"token": "new-secret", "root": "1"}, database)
    row = drive_store.get_drive("quark", database)
    assert row["config"] == {"token": "new-secret", "root": "1"}
    assert row["enabled"] is True and row["status"] == "已连接"

    public = drive_store.public_drive(row, secret_fields=("token",))
    assert public["config"] == {"token": "", "root": "1"}
    assert "new-secret" not in json.dumps(public)
    assert drive_store.get_drive("quark", database)["config"]["token"] == "new-secret"

    drive_store.rename_mount("quark", "夸克", database)
    assert drive_store.get_drive("quark", database) is None
    assert drive_store.get_drive("夸克", database)["kind"] == "quark"
    drive_store.delete_drive("夸克", database)
    assert drive_store.get_drive("夸克", database) is None
