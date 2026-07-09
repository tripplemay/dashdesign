#define AppName "DashDesign"
#ifndef AppVersion
#define AppVersion "0.1.0"
#endif
#ifndef SourceDir
#define SourceDir "..\..\dist\DashDesign"
#endif
#ifndef OutputDir
#define OutputDir "..\..\dist"
#endif

[Setup]
AppId={{6C452533-8E46-4D31-88E8-19C138665504}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=DashDesign
DefaultDirName={autopf}\DashDesign
DefaultGroupName=DashDesign
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=DashDesign-{#AppVersion}-windows-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
; In-app auto-update runs this installer while an old DashDesign may still be
; open. Let the Restart Manager close it, then relaunch it after the upgrade.
CloseApplications=yes
RestartApplications=yes

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\DashDesign"; Filename: "{app}\DashDesign.exe"
Name: "{autodesktop}\DashDesign"; Filename: "{app}\DashDesign.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Run]
Filename: "{app}\DashDesign.exe"; Description: "Launch DashDesign"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; channel.txt is written at install time by [Code], not tracked by [Files].
Type: files; Name: "{app}\channel.txt"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  // Mark this copy as the *installed* build so the client knows it may launch
  // the installer for auto-update. The portable zip ships without this file.
  if CurStep = ssPostInstall then
    SaveStringToFile(ExpandConstant('{app}\channel.txt'), 'installed', False);
end;
