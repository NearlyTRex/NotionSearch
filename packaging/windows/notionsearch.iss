; Inno Setup script for the NotionSearch Windows installer.
;
; Compiled in CI by .github/workflows/release.yml. To build by hand:
;   iscc /DAppVersion=0.1.0 packaging\windows\notionsearch.iss
;
; Installs per-user (no administrator rights needed) into
; %LOCALAPPDATA%\Programs\NotionSearch. That location matters: the app
; bind-mounts its own data folder into a container, so it has to be somewhere
; the user can actually write. Program Files would be read-only for them.

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

#define AppName      "NotionSearch"
#define AppPublisher "NotionSearch"
#define AppURL       "https://github.com/NearlyTRex/NotionSearch"

[Setup]
AppId={{8F3A5C21-9B4E-4D7A-A1E6-2C8B7F0D5A93}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases

; Per-user install: no UAC prompt, and the data folder stays writable.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes

OutputDir=..\..\dist
OutputBaseFilename=NotionSearch-{#AppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

; Windows 10 1809 is the floor for Docker Desktop / WSL2.
MinVersion=10.0.17763
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

LicenseFile=..\..\LICENSE
UninstallDisplayName={#AppName}
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Shortcuts:"
Name: "startupicon"; Description: "Start {#AppName} when I sign in"; \
    GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
; The application itself.
;
; Excludes matter here. Without them a developer building locally would sweep
; their own docker\.env - which can hold an APP_PASSWORD - into an installer
; handed to other people, along with stale __pycache__ from their machine.
; .env.example is still included: only the real .env is excluded.
#define Cruft "*.pyc,*.pyo,__pycache__,.pytest_cache,.env,*.db,*.db-shm,*.db-wal"

Source: "..\..\app\*";     DestDir: "{app}\app";     Excludes: "{#Cruft}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\web\*";     DestDir: "{app}\web";     Excludes: "{#Cruft}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\docker\*";  DestDir: "{app}\docker";  Excludes: "{#Cruft}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\scripts\*"; DestDir: "{app}\scripts"; Excludes: "{#Cruft}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\docs\*";    DestDir: "{app}\docs";    Excludes: "{#Cruft}"; Flags: ignoreversion recursesubdirs createallsubdirs

Source: "..\..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\pyproject.toml";   DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README.md";        DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE";          DestDir: "{app}"; Flags: ignoreversion

[Dirs]
; The container writes the database here, so it must exist and be writable.
Name: "{app}\data"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\scripts\start-windows.cmd"; \
    WorkingDir: "{app}"; Comment: "Start {#AppName} and open it in your browser"
Name: "{group}\Stop {#AppName}"; Filename: "{app}\scripts\stop-windows.cmd"; \
    WorkingDir: "{app}"; Comment: "Stop {#AppName}"
Name: "{group}\Documentation"; Filename: "{app}\docs\README.md"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

Name: "{autodesktop}\{#AppName}"; Filename: "{app}\scripts\start-windows.cmd"; \
    WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\scripts\start-windows.cmd"; \
    WorkingDir: "{app}"; Tasks: startupicon

[Run]
Filename: "{app}\scripts\start-windows.cmd"; Description: "Start {#AppName} now"; \
    WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; Stop and remove the containers before deleting the files they run from.
Filename: "{cmd}"; Parameters: "/c docker compose down"; \
    WorkingDir: "{app}\docker"; Flags: runhidden; RunOnceId: "ComposeDown"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\data"

[Code]
var
  DockerMissingPage: TOutputMsgMemoWizardPage;

function DockerInstalled(): Boolean;
begin
  { Docker Desktop's own folder is a more reliable signal than PATH, which
    isn't refreshed inside a running installer process. }
  Result := DirExists(ExpandConstant('{commonpf}\Docker\Docker')) or
            FileExists(ExpandConstant('{commonpf}\Docker\Docker\Docker Desktop.exe'));
end;

procedure InitializeWizard();
begin
  DockerMissingPage := CreateOutputMsgMemoPage(
    wpSelectTasks,
    'Docker Desktop is required',
    'NotionSearch runs inside Docker.',
    'Docker Desktop was not found on this computer:',
    'NotionSearch uses Docker Desktop to run its search engine.' + #13#10#13#10 +
    'You can continue with this installation now. Afterwards, install Docker' + #13#10 +
    'Desktop from:' + #13#10#13#10 +
    '    https://www.docker.com/products/docker-desktop/' + #13#10#13#10 +
    'Or run this from the Start Menu folder once installation finishes:' + #13#10#13#10 +
    '    scripts\install-windows.ps1' + #13#10#13#10 +
    'That script installs WSL2 and Docker Desktop for you.' + #13#10#13#10 +
    'NotionSearch will not start until Docker Desktop is installed and running.'
  );
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  { Only warn about Docker when it is genuinely absent. }
  if Assigned(DockerMissingPage) and (PageID = DockerMissingPage.ID) then
    Result := DockerInstalled();
end;
