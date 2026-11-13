[Setup]
AppName=Sistem Monitor CCTV (SMC)
AppVersion=1.0.0
AppPublisher=SMC
DefaultDirName={autopf}\SMC
DefaultGroupName=Sistem Monitor CCTV
OutputBaseFilename=Setup_SMC_v1.0
SetupIconFile=smc.ico
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\run\run.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\run\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Sistem Monitor CCTV"; Filename: "{app}\run.exe"
Name: "{autodesktop}\Sistem Monitor CCTV"; Filename: "{app}\run.exe"; Tasks: desktopicon

[Run]
; run.exe wajib Administrator (uac_admin=True di run.spec). Entri postinstall dijalankan sebagai user biasa,
; jadi pakai shellexec supaya Windows memunculkan prompt UAC (tanpa ini: "CreateProcess failed; code 740").
Filename: "{app}\run.exe"; Description: "Jalankan Controller SMC"; Flags: nowait postinstall skipifsilent shellexec