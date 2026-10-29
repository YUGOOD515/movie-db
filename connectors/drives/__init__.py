from connectors.drives.alist import AListDrive
from connectors.drives.local import LocalDrive

DRIVERS = {
    AListDrive.kind: AListDrive,
    LocalDrive.kind: LocalDrive,
}
