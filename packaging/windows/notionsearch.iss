; Inno Setup script for the NotionSearch Windows installer.
;
; Compiled in CI by .github/workflows/release.yml. To build by hand:
;   iscc /DAppVersion=0.1.0 packaging\windows\notionsearch.iss
;
; Installs per-user (no administrator rights needed) into
; %LOCALAPPDATA%\Programs\NotionSearch. That location matters: the app
; bind-mounts its own data folder into a container, so it has to be somewhere
; the user can actually write. Program Files would be read-only for them.
;
; The installer also detects, downloads and installs Docker Desktop, so someone
; on a brand new PC only has to run this one file.

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

#define AppName      "NotionSearch"
#define AppPublisher "NotionSearch"
#define AppURL       "https://github.com/NearlyTRex/NotionSearch"

; Docker's own stable download URL. There is no published checksum tracking the
; current release, so the download is verified by its Authenticode signature
; instead - see VerifySignedByDocker below.
#define DockerUrl "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"

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
; Installing Docker Desktop does need administrator rights, so that one step
; asks for elevation on its own.
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

; Windows 10 1809 is the floor for Docker Desktop and WSL2. These guards are
; unconditional on purpose: an installer that runs where Docker cannot is worse
; than one that declines politely.
MinVersion=10.0.17763
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

LicenseFile=..\..\LICENSE
UninstallDisplayName={#AppName}
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Offered only when Docker Desktop is genuinely absent, and checked by default
; because nothing works without it.
Name: "installdocker"; Description: "Download and install &Docker Desktop (required)"; \
    GroupDescription: "Prerequisites:"; Check: NeedsDockerDesktop

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
  DownloadPage: TDownloadWizardPage;
  PrereqPage: TOutputMsgMemoWizardPage;
  DockerWasInstalled: Boolean;
  RebootNeeded: Boolean;

{ ---------- detection ---------- }

function DockerDesktopInstalled(): Boolean;
begin
  { The executable is a more reliable signal than PATH, which is not refreshed
    inside a running installer process. }
  Result := FileExists(ExpandConstant('{commonpf}\Docker\Docker\Docker Desktop.exe'))
         or FileExists(ExpandConstant('{commonpf}\Docker\Docker\resources\bin\docker.exe'))
         or RegKeyExists(HKLM, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop');
end;

function NeedsDockerDesktop(): Boolean;
begin
  Result := not DockerDesktopInstalled();
end;

{ Hardware virtualisation. Docker Desktop cannot run without it, and it is
  disabled in firmware on a surprising number of machines. We can detect that
  but not fix it, so this only ever warns. }
function VirtualizationLikelyEnabled(): Boolean;
var
  Locator, Services, Items, Item: Variant;
begin
  { Assume fine unless WMI positively says otherwise: a false alarm here would
    frighten someone off a machine that works perfectly well. }
  Result := True;
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Services := Locator.ConnectServer('localhost', 'root\CIMV2');
    Items := Services.ExecQuery('SELECT VirtualizationFirmwareEnabled FROM Win32_Processor');
    { ItemIndex is an Inno extension for SWbemObjectSet; Pascal Script has no
      IEnumVariant, so the usual COM enumeration is not available here. }
    if Items.Count > 0 then
    begin
      Item := Items.ItemIndex(0);
      if not VarIsNull(Item.VirtualizationFirmwareEnabled) then
        Result := Item.VirtualizationFirmwareEnabled;
    end;
  except
    Result := True;
  end;
end;

{ ---------- Docker Desktop install ---------- }

{ Verify the download really came from Docker before running it. Docker
  publishes no checksum tracking the current release, so the Authenticode
  signature is the available guarantee - and it is a strong one. }
function VerifySignedByDocker(const FileName: String): Boolean;
var
  ResultCode: Integer;
  Command: String;
begin
  Command :=
    '$ErrorActionPreference=''Stop'';' +
    '$s = Get-AuthenticodeSignature -LiteralPath ''' + FileName + ''';' +
    'if ($s.Status -ne ''Valid'') { exit 2 };' +
    'if ($s.SignerCertificate.Subject -notmatch ''Docker Inc'') { exit 3 };' +
    'exit 0';

  Result := Exec('powershell.exe',
                 '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' + Command + '"',
                 '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
            and (ResultCode = 0);

  if not Result then
    Log('Authenticode verification failed for ' + FileName +
        ' (exit ' + IntToStr(ResultCode) + ')');
end;

function InstallDockerDesktop(const Installer: String): Boolean;
var
  ResultCode: Integer;
begin
  { Docker Desktop needs administrator rights, so this one step asks for
    elevation even though the rest of the install does not. }
  Result := ShellExec('runas', Installer,
                      'install --quiet --accept-license --backend=wsl-2',
                      '', SW_SHOW, ewWaitUntilTerminated, ResultCode);

  if not Result then
  begin
    Log('Could not launch the Docker Desktop installer (elevation refused?)');
    Exit;
  end;

  { 3010 means installed successfully but a restart is required. }
  if ResultCode = 3010 then
  begin
    RebootNeeded := True;
    Log('Docker Desktop installed; a restart is required.');
  end
  else if ResultCode <> 0 then
  begin
    Log('Docker Desktop installer exited with ' + IntToStr(ResultCode));
    Result := False;
    Exit;
  end;

  DockerWasInstalled := True;
end;

{ ---------- wizard ---------- }

function OnDownloadProgress(const Url, FileName: String;
                            const Progress, ProgressMax: Int64): Boolean;
begin
  if ProgressMax <> 0 then
    Log(Format('Downloaded %d of %d bytes', [Progress, ProgressMax]));
  Result := True;
end;

procedure InitializeWizard();
var
  Summary: String;
begin
  DownloadPage := CreateDownloadPage(
    SetupMessage(msgWizardPreparing), SetupMessage(msgPreparingDesc),
    @OnDownloadProgress);

  Summary :=
    'NotionSearch runs inside Docker Desktop, which is free for personal use.' + #13#10#13#10;

  if DockerDesktopInstalled() then
    Summary := Summary + '  [ok]  Docker Desktop is already installed.' + #13#10
  else
    Summary := Summary +
      '  [  ]  Docker Desktop is not installed.' + #13#10 +
      '        This installer can download and install it for you.' + #13#10 +
      '        It is a large download, so allow several minutes, and' + #13#10 +
      '        Windows will ask your permission part way through.' + #13#10;

  if not VirtualizationLikelyEnabled() then
    Summary := Summary + #13#10 +
      '  [!]   Hardware virtualisation appears to be turned off.' + #13#10 +
      '        Docker cannot run without it. It is switched on in your' + #13#10 +
      '        computer''s BIOS or UEFI settings, usually listed as' + #13#10 +
      '        "Intel VT-x", "AMD-V" or "SVM Mode".' + #13#10 +
      '        Installation will continue, but Docker will not start' + #13#10 +
      '        until that is enabled.' + #13#10;

  Summary := Summary + #13#10 +
    'Nothing here sends your Notion content anywhere. It all stays on this' + #13#10 +
    'computer.';

  PrereqPage := CreateOutputMsgMemoPage(wpLicense,
    'Before we start', 'What NotionSearch needs',
    'Please read this, then continue:', Summary);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Installer: String;
begin
  Result := '';

  { A silent install must not pull down hundreds of megabytes unasked. This is
    also what stops the CI smoke test downloading Docker Desktop every run. }
  if WizardSilent() then
    Exit;

  if not WizardIsTaskSelected('installdocker') then
    Exit;

  if DockerDesktopInstalled() then
    Exit;

  DownloadPage.Clear();
  DownloadPage.Add('{#DockerUrl}', 'DockerDesktopInstaller.exe', '');
  DownloadPage.Show();
  try
    try
      DownloadPage.Download();
    except
      Result := 'Could not download Docker Desktop: ' + GetExceptionMessage + #13#10#13#10 +
                'You can install it yourself from' + #13#10 +
                'https://www.docker.com/products/docker-desktop/' + #13#10 +
                'and then start NotionSearch from the Start Menu.';
      Exit;
    end;

    Installer := ExpandConstant('{tmp}\DockerDesktopInstaller.exe');

    if not VerifySignedByDocker(Installer) then
    begin
      Result := 'The downloaded Docker Desktop installer is not correctly signed ' +
                'by Docker Inc, so it was not run.' + #13#10#13#10 +
                'That can mean the download was corrupted or tampered with. ' +
                'Please install Docker Desktop yourself from' + #13#10 +
                'https://www.docker.com/products/docker-desktop/';
      Exit;
    end;

    DownloadPage.SetText('Installing Docker Desktop...',
                         'This takes a few minutes. Windows will ask for permission.');

    if not InstallDockerDesktop(Installer) then
    begin
      Result := 'Docker Desktop could not be installed automatically.' + #13#10#13#10 +
                'NotionSearch itself will still be installed. To finish, install ' +
                'Docker Desktop from' + #13#10 +
                'https://www.docker.com/products/docker-desktop/' + #13#10 +
                'and then start NotionSearch from the Start Menu.';
      Exit;
    end;
  finally
    DownloadPage.Hide();
  end;

  NeedsRestart := RebootNeeded;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  { Nothing useful to say when Docker is present and virtualisation is fine. }
  if Assigned(PrereqPage) and (PageID = PrereqPage.ID) then
    Result := DockerDesktopInstalled() and VirtualizationLikelyEnabled();
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if DockerWasInstalled and RebootNeeded then
      SuppressibleMsgBox(
        'Docker Desktop was installed and needs a restart to finish.' + #13#10#13#10 +
        'Restart your computer, then open NotionSearch from the Start Menu.',
        mbInformation, MB_OK, IDOK)
    else if DockerWasInstalled then
      SuppressibleMsgBox(
        'Docker Desktop was installed.' + #13#10#13#10 +
        'The first time NotionSearch starts it waits for Docker to be ready, ' +
        'which can take a minute.',
        mbInformation, MB_OK, IDOK);
  end;
end;
