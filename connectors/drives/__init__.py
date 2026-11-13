from connectors.drives.alist import AListDrive
from connectors.drives.aliyun import AliyunDrive
from connectors.drives.baidu import BaiduDrive
from connectors.drives.gdrive import GoogleDriveDrive
from connectors.drives.local import LocalDrive
from connectors.drives.onedrive import OneDriveDrive
from connectors.drives.pan115 import Pan115Drive
from connectors.drives.pan123 import Pan123Drive
from connectors.drives.pan123_web import Pan123WebDrive
from connectors.drives.quark import QuarkDrive, UCDrive
from connectors.drives.quark_tv import QuarkTVDrive, UCTVDrive
from connectors.drives.webdav import WebDAVDrive

# 顺序即网盘页“网盘类型”下拉的顺序
DRIVERS = {
    LocalDrive.kind: LocalDrive,
    QuarkDrive.kind: QuarkDrive,
    QuarkTVDrive.kind: QuarkTVDrive,
    UCDrive.kind: UCDrive,
    UCTVDrive.kind: UCTVDrive,
    AliyunDrive.kind: AliyunDrive,
    BaiduDrive.kind: BaiduDrive,
    Pan115Drive.kind: Pan115Drive,
    Pan123WebDrive.kind: Pan123WebDrive,
    Pan123Drive.kind: Pan123Drive,
    OneDriveDrive.kind: OneDriveDrive,
    GoogleDriveDrive.kind: GoogleDriveDrive,
    WebDAVDrive.kind: WebDAVDrive,
    AListDrive.kind: AListDrive,
}
