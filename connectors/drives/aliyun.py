from __future__ import annotations

import threading

from connectors import throttle
from connectors.drives.base import AuthError, Drive, DriveError, Entry, Field, Link

BASE = "https://openapi.alipan.com"
QR_STATUS = "https://openapi.aliyundrive.com/oauth/qrcode/{sid}/status"
INVALID_TOKEN = {"AccessTokenInvalid", "AccessTokenExpired", "I400JD"}


class AliyunDrive(Drive):
    kind = "aliyun"
    label = "阿里云盘 Open"
    qr_app = "阿里云盘 App"
    fields = (
        Field("refresh_token", "refresh_token", "secret", required=True, manual=True),
        Field("drive_type", "盘类型", "select", default="resource",
              options=(("resource", "资源库"), ("default", "备份盘（默认盘）"), ("backup", "备份盘"))),
        Field("alipan_type", "客户端类型", "select", default="default",
              options=(("default", "阿里云盘 Open"), ("alipanTV", "阿里云盘 TV")), advanced=True),
        Field("client_id", "client_id", "secret", advanced=True, hint="留空则通过 OpenList 的令牌服务续期"),
        Field("client_secret", "client_secret", "secret", advanced=True, hint="留空则通过 OpenList 的令牌服务续期"),
        Field("renew_api", "续期服务", default="https://api.oplist.org/alicloud/renewapi", advanced=True),
        Field("root_id", "根目录 ID", default="root", advanced=True),
    )

    def __init__(self, config, save, session=None):
        super().__init__(config, save, session)
        self.root_id = str(config.get("root_id") or "root")
        self.drive_id = config.get("drive_id", "")
        self.user_id = config.get("user_id", "")
        self.name = ""
        self._refresh_lock = threading.Lock()
        self._init_lock = threading.Lock()

    def _refresh(self):
        with self._refresh_lock:
            last_error = None
            for _ in range(3):
                try:
                    if self.config.get("client_id") and self.config.get("client_secret"):
                        response = throttle.call("aliyun", lambda: self.session.post(
                            BASE + "/oauth/access_token",
                            json={"client_id": self.config["client_id"], "client_secret": self.config["client_secret"],
                                  "grant_type": "refresh_token", "refresh_token": self.config.get("refresh_token", "")},
                            timeout=20,
                        ))
                    else:
                        response = throttle.call("aliyun", lambda: self.session.get(
                            self.config.get("renew_api") or "https://api.oplist.org/alicloud/renewapi",
                            params={"refresh_ui": self.config.get("refresh_token", ""), "server_use": "true",
                                    "driver_txt": "alicloud_tv" if self.config.get("alipan_type") == "alipanTV" else "alicloud_qr"},
                            timeout=20,
                        ))
                    payload = response.json()
                    refresh = payload.get("refresh_token") or ""
                    access = payload.get("access_token") or ""
                    if not refresh or not access:
                        raise AuthError("阿里云盘 refresh_token 已失效，请重新获取")
                    self.config.update(refresh_token=refresh, access_token=access)
                    self.save(self.config)
                    return
                except AuthError as error:
                    last_error = error
                    break
                except (ValueError, TypeError, KeyError) as error:
                    last_error = error
            raise AuthError("阿里云盘 refresh_token 已失效，请重新获取") from last_error

    # ---- 扫码登录：经 OpenList 令牌服务生成官方二维码（用它的应用密钥），与默认续期方式是同一个应用 ----
    def _oplist(self):
        renew = self.config.get("renew_api") or "https://api.oplist.org/alicloud/renewapi"
        return renew.rsplit("/", 2)[0]

    def start_login(self):
        # 同一个 session 保存 OpenList 下发的 Cookie，换令牌时它据此识别登录方式
        response = throttle.call("aliyun", lambda: self.session.get(
            self._oplist() + "/alicloud/requests", params={"driver_txt": "alicloud_qr", "server_use": "true"},
            timeout=20))
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not payload.get("sid") or not payload.get("text"):
            raise DriveError(str(payload.get("text") or "无法获取登录二维码，请稍后重试"))
        return {"qr": payload["text"], "state": {"sid": payload["sid"]}}

    def finish_login(self, state):
        sid = state["sid"]
        response = throttle.call("aliyun", lambda: self.session.get(QR_STATUS.format(sid=sid), timeout=20))
        try:
            status = response.json().get("status") or ""
        except ValueError:
            status = ""
        if status == "QRCodeExpired":
            raise AuthError("二维码已过期，请重新获取")
        if status != "LoginSuccess":
            return False
        response = throttle.call("aliyun", lambda: self.session.get(self._oplist() + "/alicloud/callback", params={
            "server_use": "true", "grant_type": "authorization_code", "code": sid, "sid": sid,
            "client_id": "", "client_secret": "",
        }, timeout=20))
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not payload.get("refresh_token"):
            raise AuthError(str(payload.get("text") or "阿里云盘登录失败，请重新扫码"))
        self.config.update(refresh_token=payload["refresh_token"], access_token=payload.get("access_token") or "",
                           alipan_type="default")
        self.save(self.config)
        return True

    def _request(self, path, body, *, service="aliyun"):
        for attempt in range(2):
            if not self.config.get("access_token"):
                self._refresh()
            response = throttle.call(service, lambda: self.session.post(
                BASE + path, json=body, headers={"Authorization": f"Bearer {self.config['access_token']}"}, timeout=20,
            ))
            try:
                payload = response.json()
            except ValueError:
                raise DriveError("阿里云盘返回了无法识别的响应") from None
            code = payload.get("code")
            if response.status_code == 401 or code in INVALID_TOKEN:
                if attempt == 0:
                    self._refresh()
                    continue
                raise AuthError("阿里云盘 access_token 已失效，请重新授权")
            if response.status_code >= 400 or code:
                raise DriveError("阿里云盘请求失败，请检查配置")
            return payload
        raise DriveError("阿里云盘请求失败，请检查配置")

    def _initialize(self, force=False):
        if self.drive_id and not force:
            return
        with self._init_lock:
            if self.drive_id and not force:
                return
            payload = self._request("/adrive/v1.0/user/getDriveInfo", {})
            drive_type = self.config.get("drive_type") or "resource"
            self.drive_id = payload.get(f"{drive_type}_drive_id") or ""
            if not self.drive_id:
                raise DriveError("该账号没有资源库" if drive_type == "resource" else "该账号没有备份盘")
            self.user_id = payload.get("user_id") or ""
            self.name = payload.get("name") or ""
            self.config.update(drive_id=self.drive_id, user_id=self.user_id)
            self.save(self.config)

    def check(self):
        self._initialize(force=True)
        return f"已连接：{self.name}"

    def list(self, directory):
        self._initialize()
        result, marker = [], ""
        while True:
            payload = self._request("/adrive/v1.0/openFile/list", {
                "drive_id": self.drive_id, "parent_file_id": directory.id, "limit": 200, "marker": marker,
            }, service="aliyun-list")
            for item in payload.get("items") or []:
                result.append(Entry(
                    name=item.get("name") or "", id=str(item.get("file_id") or ""),
                    is_dir=item.get("type") == "folder", size=item.get("size") or 0,
                    modified=item.get("updated_at") or "", created=item.get("created_at") or "",
                ))
            if len(result) > 10000:
                raise DriveError("目录文件过多")
            marker = payload.get("next_marker") or ""
            if not marker:
                return result

    def link(self, file):
        self._initialize()
        payload = self._request("/adrive/v1.0/openFile/getDownloadUrl", {
            "drive_id": self.drive_id, "file_id": file.id, "expire_sec": 14400,
        }, service="aliyun-link")
        if not payload.get("url"):
            raise DriveError("无法获取播放链接")
        return Link(url=payload["url"], proxy=False, expires_in=60)
