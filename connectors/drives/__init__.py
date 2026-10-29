from connectors.drives.alist import AListDrive
from connectors.drives.aliyun import AliyunDrive
from connectors.drives.baidu import BaiduDrive
from connectors.drives.local import LocalDrive
from connectors.drives.pan123 import Pan123Drive
from connectors.drives.quark import QuarkDrive, UCDrive

DRIVERS = {
    AListDrive.kind: AListDrive,
    AliyunDrive.kind: AliyunDrive,
    BaiduDrive.kind: BaiduDrive,
    LocalDrive.kind: LocalDrive,
    Pan123Drive.kind: Pan123Drive,
    QuarkDrive.kind: QuarkDrive,
    UCDrive.kind: UCDrive,
}
