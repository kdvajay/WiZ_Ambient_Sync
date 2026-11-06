#define MyAppName "WiZ Ambient Sync Beta"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "WiZ Ambient Sync"
#define MyAppExeName "WiZAmbientSync.exe"

[Setup]
AppId={{D9F6B9C1-2A74-4B9A-A0D4-5E1B6C3F8A21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
SetupIconFile=app\app_icon.ico
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\WiZ Ambient Sync Beta
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer-output
OutputBaseFilename=WiZ-Ambient-Sync-Setup-{#MyAppVersion}-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no

[Files]
Source: "dist\2.0.0\WiZAmbientSync.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\2.0.0\wiz-engine.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "app\app_icon.png"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch WiZ Ambient Sync"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Keep the user's configuration in %APPDATA%\WiZAmbientSync.
; User settings are deliberately not removed by uninstall.
