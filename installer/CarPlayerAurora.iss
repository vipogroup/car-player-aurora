; Inno Setup 6 — build from repo root:
;   & "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" "installer\CarPlayerAurora.iss"
; Installs to %LOCALAPPDATA%\CarPlayer-Aurora (no admin). Requires Python 3.10+ on PATH.

#define MyAppName "Car Player · Aurora"
#define MyAppVersion "1.0.3"
#define RepoRoot ".."

[Setup]
AppId={{B4E8C9D1-2F3A-4E5B-9C0D-1A2B3C4D5E6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=vipogroup
DefaultDirName={localappdata}\CarPlayer-Aurora
DefaultGroupName=Car Player
DisableWelcomePage=no
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=CarPlayerAurora-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\car-music-icon.png
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; בלי Flags: unchecked — שתי המשימות מסומנות כברירת מחדל בהתקנה ראשונה (שולחן עבודה). נעיצה לשורת המשימות נשארת פעולה ידנית ב-Windows.
Name: "desktopserver"; Description: "Desktop shortcut: start server"; GroupDescription: "Shortcuts:"
Name: "desktopbrowser"; Description: "Desktop shortcut: open Aurora"; GroupDescription: "Shortcuts:"

[Files]
; Excludes: רשימה מופרדת בפסיקים בלבד (פסיק-עלית מפרק את כל ההחרגות — Inno מתעלם מהשאר).
Source: "{#RepoRoot}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs; \
  Excludes: ".git\*,.github\*,offline_library\*,*.mp4,*.pyc,__pycache__\*,.cursor\*,.claude\*,.venv\*,terminals\*,mcps\*,installer\Output\*,car-music-player.zip,local-server-unblocked.zip,CarPlayerAurora-Setup.exe"

[Icons]
Name: "{group}\Start Car Player server"; Filename: "{app}\start-server-lan.bat"; WorkingDir: "{app}"
Name: "{group}\Open Aurora"; Filename: "{app}\open-aurora.bat"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Car Player server"; Filename: "{app}\start-server-lan.bat"; WorkingDir: "{app}"; Tasks: desktopserver
Name: "{autodesktop}\Aurora"; Filename: "{app}\open-aurora.bat"; WorkingDir: "{app}"; Tasks: desktopbrowser

[Run]
Filename: "{app}\installer-postinstall.cmd"; WorkingDir: "{app}"; StatusMsg: "Installing Python (.venv)..."; Flags: waituntilterminated
Filename: "{app}\start-server-lan.bat"; Description: "Start local server now"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent shellexec

[Code]
var
  BroughtWizardToFront: Boolean;

function PythonOnPath: Boolean;
var
  R: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/c python --version', '', SW_HIDE, ewWaitUntilTerminated, R) and (R = 0);
  if not Result then
    Result := Exec(ExpandConstant('{cmd}'), '/c py -3 --version', '', SW_HIDE, ewWaitUntilTerminated, R) and (R = 0);
end;

function InitializeSetup: Boolean;
begin
  { אל תעצור כאן — אחרת נראה כאילו "שום דבר לא נפתח" (הודעה קטנה מאחורי חלונות / SmartScreen). }
  Result := true;
end;

procedure InitializeWizard;
begin
  BroughtWizardToFront := False;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { אחרי שהאשף כבר מוצג — מביאים לחזית (דפדפן/הורדות מסתירים מאחור). }
  if not BroughtWizardToFront then
  begin
    BroughtWizardToFront := True;
    try
      WizardForm.BringToFront;
    except
    end;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := true;
  if CurPageID = wpReady then
  begin
    if not PythonOnPath then
    begin
      MsgBox('חסר Python ב-PATH (גרסה 3.10 ומעלה).'#13#10#13#10 +
             'ב-PowerShell:'#13#10 +
             '  winget install Python.Python.3.13'#13#10 +
             'סמני "Add python.exe to PATH", סגרי את PowerShell, ואז הריצי שוב את המתקין.'#13#10#13#10 +
             'אם לחיצה על הקובץ לא פותחת חלון:'#13#10 +
             '  לחצי ימין → מאפיינים → אם יש "שחרור חסימה" — סמני ואשרי.'#13#10 +
             '  אם מופיע SmartScreen — "מידע נוסף" → "הרץ בכל זאת".',
             mbError, MB_OK);
      Result := false;
    end;
  end;
end;
