; 信息差 Windows 安装器（Inno Setup 6）
; 构建：powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
; 或手动：ISCC packaging\installer.iss（需先跑完 PyInstaller，产物在 dist\infogap）

#define MyAppName "信息差 InfoGap"
#define MyAppNameEn "InfoGap"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "InfoGap"
#define MyAppExeName "InfoGap.exe"

[Setup]
AppId={{7A3F9C2E-1B4D-4E8A-9C6F-2D5E8A1B3C7D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
UninstallDisplayName={#MyAppName}
OutputDir=..\dist
OutputBaseFilename=InfoGap-Setup-{#MyAppVersion}
SetupIconFile=icons\InfoGap.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
; 中文界面（未装语言包的英文系统自动回落英文）
ShowLanguageDialog=auto

[Languages]
; 简中是「非官方翻译」，Inno 官方安装不含该文件，由构建脚本下载到本目录
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\infogap\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 数据在 %APPDATA%\infogap，卸载不删用户数据，仅删程序目录
Type: filesandordirs; Name: "{app}"

[Code]
var
  DownloadPage: TDownloadWizardPage;

function WebView2Installed(): Boolean;
begin
  // WebView2 运行时（Edge Chromium 内核）Win10/11 大多预装；未装则安装器代为下载
  Result := RegKeyExists(HKEY_LOCAL_MACHINE, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}')
    or RegKeyExists(HKEY_LOCAL_MACHINE, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}')
    or RegKeyExists(HKEY_CURRENT_USER, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpReady) and (not WebView2Installed()) then begin
    DownloadPage.Clear;
    DownloadPage.Add('https://go.microsoft.com/fwlink/p/?LinkId=2124703', 'MicrosoftEdgeWebview2Setup.exe', '');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
      except
        SuppressibleMsgBox(AddPeriod(GetExceptionMessage), mbError, MB_OK, IDOK);
        Result := False;
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  RetCode: Integer;
begin
  if (CurStep = ssInstall) and (not WebView2Installed()) and FileExists(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe')) then begin
    Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '', SW_SHOW, ewWaitUntilTerminated, RetCode);
  end;
end;

procedure InitializeWizard();
begin
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), SetupMessage(msgPreparingDesc), nil);
end;
