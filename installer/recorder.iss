; The installer owns app files and shortcuts, never the recorder's data folder.
#ifndef RecorderVersion
  #error RecorderVersion is required
#endif
#ifndef RecorderAppId
  #define RecorderAppId "{{6C1F7441-28F1-44C7-9E9E-B63270C585D0}"
#endif
#ifndef RecorderName
  #define RecorderName "AMS League Recorder"
#endif

[Setup]
AppId={#RecorderAppId}
AppName={#RecorderName}
AppVersion={#RecorderVersion}
AppPublisher=AMS League
AppPublisherURL=https://ams2.ioresearch.ca
DefaultDirName={localappdata}\Programs\AMSLeagueRecorder
DefaultGroupName={#RecorderName}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
AppMutex=Local\AMSLeagueRecorder.Running
SetupMutex=AMSLeagueRecorder.Setup
; Never terminate a recorder, including when Setup is run silently.
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\AMSLeagueRecorder.exe
OutputDir=..\dist
OutputBaseFilename=AMSLeagueRecorder-Setup-Windows-x64-Intel-AMD
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\AMSLeagueRecorder-Windows-x64-Intel-AMD.exe"; DestDir: "{app}"; DestName: "AMSLeagueRecorder.exe"
Source: "..\docs\windows-quickstart.txt"; DestDir: "{app}"; DestName: "README.txt"; Flags: ignoreversion
; Personalized ZIPs carry this single-use setup file beside the installer.
Source: "{src}\AMSLeagueRecorder.enroll.json"; DestDir: "{app}"; Flags: external skipifsourcedoesntexist ignoreversion

[Icons]
Name: "{autoprograms}\{#RecorderName}"; Filename: "{app}\AMSLeagueRecorder.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\{#RecorderName}"; Filename: "{app}\AMSLeagueRecorder.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\AMSLeagueRecorder.exe"; Description: "Open {#RecorderName}"; Flags: nowait postinstall skipifsilent

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Target: String;
  InstalledVersion, SetupVersion: Int64;
begin
  Result := '';
  Target := ExpandConstant('{app}\AMSLeagueRecorder.exe');
  if FileExists(Target) then
  begin
    if not GetPackedVersion(Target, InstalledVersion) then
      Result := 'The installed recorder version could not be checked. The existing recorder has been kept. Please use a different installation folder or contact support.'
    else if not StrToVersion('{#RecorderVersion}', SetupVersion) then
      Result := 'The installer version could not be checked.'
    else if ComparePackedVersion(InstalledVersion, SetupVersion) > 0 then
      Result := 'A newer recorder is already installed. Open {#RecorderName} from the Start menu or desktop shortcut. This older installer will not replace it.';
  end;
end;
