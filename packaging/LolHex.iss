; 海克斯大乱斗助手安装包。先运行 packaging/build.ps1（它会调用本脚本）。
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef BuildChannel
  #define BuildChannel "user"
#endif
#if BuildChannel == "user"
  #define ChannelLabel "用户版"
#else
  #error Unknown BuildChannel
#endif

[Setup]
AppId={{1107079E-6C48-46FD-938D-04B1C3EA9A72}
AppName=AramHex 海克斯大乱斗助手（{#ChannelLabel}）
AppVersion={#AppVersion}
AppPublisher=Local custom build (upstream: czgreat)
AppPublisherURL=https://github.com/czgreat/aramhex
; 原项目链接用于保留来源；定制版本不把报错入口指向原作者。
DefaultDirName={localappdata}\Programs\AramHex
DefaultGroupName=海克斯大乱斗助手
; 装到用户目录，不需要管理员权限；始终"只为当前用户安装"（卸载信息写在 HKCU），在线更新才能改「应用」列表里的版本号
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
OutputDir=..\build\installer\{#BuildChannel}
OutputBaseFilename=AramHex-Setup-{#AppVersion}-{#BuildChannel}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=海克斯大乱斗助手
UninstallDisplayIcon={app}\AramHex.exe
SetupIconFile=lolhex.ico
LicenseFile=..\LICENSE

[Languages]
Name: "chs"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
; LicenseFile 仅控制安装时展示，以下四项保证安装后仍能在程序目录阅读。
Source: "..\build\dist\{#BuildChannel}\AramHex\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\dist\{#BuildChannel}\AramHex\NOTICE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\dist\{#BuildChannel}\AramHex\PRIVACY.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\dist\{#BuildChannel}\AramHex\DATA-LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\dist\{#BuildChannel}\AramHex\*"; DestDir: "{app}"; Excludes: "\LICENSE,\NOTICE.md,\PRIVACY.md,\DATA-LICENSE.txt"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; 升级时先清掉旧的程序文件（onedir 目录合并安装，否则旧版本多出来的文件会留下）
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\海克斯大乱斗助手"; Filename: "{app}\AramHex.exe"
Name: "{group}\卸载海克斯大乱斗助手"; Filename: "{uninstallexe}"
Name: "{userdesktop}\海克斯大乱斗助手"; Filename: "{app}\AramHex.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AramHex.exe"; Description: "立即启动"; Flags: nowait postinstall skipifsilent
; 助手里点「立即更新」走的是 /SILENT：装完自动重新打开
Filename: "{app}\AramHex.exe"; Flags: nowait; Check: WizardSilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c taskkill /im AramHex.exe /f"; Flags: runhidden; RunOnceId: "KillAramHex"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    { Only a completed full installation can retire an incomplete rollback. }
    DeleteFile(ExpandConstant('{app}\update-rollback-incomplete.txt'));
    DeleteFile(ExpandConstant('{app}\update-failed.txt'));
    DelTree(ExpandConstant('{app}\_update_old'), True, True, True);
    DelTree(ExpandConstant('{app}\_update_new'), True, True, True);
  end;
end;
