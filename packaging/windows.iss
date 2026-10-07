#define AppVersion "1.0.0"
[Setup]
AppId={{A7B9CB17-9629-4E9F-815B-2AF36D7D2E90}
AppName=Media Organizer
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\MediaOrganizer
DefaultGroupName=Media Organizer
PrivilegesRequired=lowest
OutputDir=..\dist\installers
OutputBaseFilename=MediaOrganizer-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\MediaOrganizer.exe
[Files]
Source: "..\dist\desktop\MediaOrganizer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Media Organizer"; Filename: "{app}\MediaOrganizer.exe"
Name: "{autodesktop}\Media Organizer"; Filename: "{app}\MediaOrganizer.exe"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Create desktop shortcut"; Flags: unchecked
[Run]
Filename: "{app}\MediaOrganizer.exe"; Description: "Open Media Organizer"; Flags: nowait postinstall skipifsilent
