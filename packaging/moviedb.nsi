; MovieDB Windows 安装程序（NSIS 3）。由 packaging/build_windows.py 调用：
;   makensis -INPUTCHARSET UTF8 -DVERSION=0.1.0 -DSTAGE=dist\MovieDB -DOUTFILE=... -DFILELIST=... moviedb.nsi
; 按当前用户安装到 %LOCALAPPDATA%\Programs\MovieDB，不需要管理员权限。
; 用户数据（媒体库、.env、海报缓存、日志）在 %LOCALAPPDATA%\MovieDB，升级与卸载程序都不会碰它，
; 旧版放在 app\ 里的数据由 desktop.py 首次启动时迁移过去。

Unicode true
SetCompressor /SOLID lzma
SetCompressorDictSize 64
RequestExecutionLevel user
ManifestDPIAware true

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"

!define APP "MovieDB"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}"
!define PYTHONW "$INSTDIR\runtime\python\pythonw.exe"

Name "${APP}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\${APP}"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
BrandingText "${APP} ${VERSION}"

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "${APP}"
VIAddVersionKey /LANG=2052 "FileDescription" "${APP} 安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "YUGOOD515"

!define MUI_ICON "${STAGE}\moviedb.ico"
!define MUI_UNICON "${STAGE}\moviedb.ico"
!define MUI_ABORTWARNING
!define MUI_COMPONENTSPAGE_SMALLDESC
!define MUI_WELCOMEPAGE_TEXT "将安装 ${APP} ${VERSION}：在本机运行的影视库，读取 AList 中的网盘影片并用 TMDB 补全海报与简介。$\r$\n$\r$\n安装到当前用户目录，不需要管理员权限。安装完成后会在浏览器中打开首次使用引导。"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "启动 ${APP}"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApp

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

; 只结束本安装目录里的 MovieDB 进程，不影响其他 Python 程序。
!macro StopRunning
    nsExec::Exec `powershell -NoProfile -NonInteractive -Command "Get-Process pythonw,python -ErrorAction SilentlyContinue | Where-Object { $$_.Path -like '$INSTDIR\runtime\*' } | Stop-Process -Force"`
    Pop $0
    Sleep 500
!macroend

Function LaunchApp
    SetOutPath "$INSTDIR\app"
    Exec '"${PYTHONW}" "$INSTDIR\app\desktop.py"'
FunctionEnd

Section "${APP}（必需）" SecMain
    SectionIn RO
    !insertmacro StopRunning
    ; 运行时里没有用户数据，升级时整个替换，避免残留旧版本的依赖。
    RMDir /r "$INSTDIR\runtime\python"
    SetOutPath "$INSTDIR\runtime"
    File /r "${STAGE}\runtime\python"
    ; 只覆盖程序文件：旧版的媒体库可能还在 app\ 里等待迁移。
    SetOutPath "$INSTDIR"
    File /r "${STAGE}\app"
    File "${STAGE}\moviedb.ico"
    File "${STAGE}\VERSION.txt"

    SetOutPath "$INSTDIR\app"
    CreateShortcut "$SMPROGRAMS\${APP}.lnk" "${PYTHONW}" '"$INSTDIR\app\desktop.py"' "$INSTDIR\moviedb.ico" 0 SW_SHOWNORMAL "" "打开 ${APP}"

    WriteUninstaller "$INSTDIR\Uninstall.exe"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP}"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "YUGOOD515"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "URLInfoAbout" "https://github.com/YUGOOD515/movie-db"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\moviedb.ico"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
    WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
    WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

!ifndef NO_FFPROBE
Section "ffprobe（读取分辨率、HDR 与音轨）" SecFfprobe
    SetOutPath "$INSTDIR\runtime"
    File /r "${STAGE}\runtime\ffmpeg"
SectionEnd
!endif

Section "桌面快捷方式" SecDesktop
    SetOutPath "$INSTDIR\app"
    CreateShortcut "$DESKTOP\${APP}.lnk" "${PYTHONW}" '"$INSTDIR\app\desktop.py"' "$INSTDIR\moviedb.ico" 0 SW_SHOWNORMAL "" "打开 ${APP}"
SectionEnd

Section "-Finish"
    !ifndef NO_FFPROBE
        ${IfNot} ${SectionIsSelected} ${SecFfprobe}
            RMDir /r "$INSTDIR\runtime\ffmpeg"
        ${EndIf}
    !endif
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" "$0"
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
    !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} "程序本体与内置 Python 运行环境。"
    !ifndef NO_FFPROBE
        !insertmacro MUI_DESCRIPTION_TEXT ${SecFfprobe} "导入时读取视频的分辨率、HDR、编码与音轨（约 160MB）。已自行安装 FFmpeg 可取消。"
    !endif
    !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "在桌面创建 ${APP} 快捷方式。"
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Section "Uninstall"
    !insertmacro StopRunning
    Delete "$SMPROGRAMS\${APP}.lnk"
    Delete "$DESKTOP\${APP}.lnk"
    DeleteRegKey HKCU "${UNINSTALL_KEY}"
    RMDir /r "$INSTDIR\runtime"
    Delete "$INSTDIR\moviedb.ico"
    Delete "$INSTDIR\VERSION.txt"
    Delete "$INSTDIR\Uninstall.exe"

    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "是否同时删除媒体库数据？$\r$\n$\r$\n包括媒体库（library.db）、连接配置（.env）和已下载的海报图片，位于 $LOCALAPPDATA\${APP}。选择“否”会保留它们，重新安装后可继续使用。" /SD IDNO IDYES remove_data
    ; 保留数据：只删除安装时放入的程序文件（清单由打包脚本生成），目录为空时才删除。
    !include "${FILELIST}"
    RMDir "$INSTDIR"
    Goto done
remove_data:
    RMDir /r "$LOCALAPPDATA\${APP}"
    RMDir /r "$INSTDIR\app"
    RMDir "$INSTDIR"
done:
SectionEnd
