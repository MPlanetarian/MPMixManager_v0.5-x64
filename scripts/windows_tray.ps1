<#
.SYNOPSIS
    Notification-area icon for MP Mix Manager, MP Gui, and MP Liquid Player.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Root,
    [switch]$Stay
)

$ErrorActionPreference = "Stop"

function Resolve-MpRoot {
    param([string]$Path)
    $value = $Path.Trim().Trim('"')
    if ($value -match '^/mnt/([A-Za-z])/(.*)$') {
        $value = "{0}:\{1}" -f $Matches[1].ToUpper(), ($Matches[2] -replace '/', '\')
    } elseif ($value -match '^/([A-Za-z])/(.*)$') {
        $value = "{0}:\{1}" -f $Matches[1].ToUpper(), ($Matches[2] -replace '/', '\')
    }
    return [System.IO.Path]::GetFullPath($value)
}

$Root = Resolve-MpRoot $Root

if ([System.Threading.Thread]::CurrentThread.GetApartmentState() -ne [System.Threading.ApartmentState]::STA) {
    $exe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = "-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File `"$PSCommandPath`" -Root `"$Root`""
    if ($Stay) { $arguments += " -Stay" }
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $exe
    $info.Arguments = $arguments
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    [System.Diagnostics.Process]::Start($info) | Out-Null
    exit 0
}

function Write-MpTrayLog {
    param([string]$Message)
    try {
        $dir = Join-Path $env:USERPROFILE ".config\mix-manager"
        if (-not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        $log = Join-Path $dir "mp_system_tray.log"
        [System.IO.File]::AppendAllText($log, "$(Get-Date -Format o) $Message`r`n")
    } catch {
    }
}

$mutex = New-Object System.Threading.Mutex($false, "Local\MPMixManagerTray")
$owned = $false
try {
    $owned = $mutex.WaitOne(0)
} catch [System.Threading.AbandonedMutexException] {
    $owned = $true
}
if (-not $owned) {
    exit 0
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

if (-not ("MpWindowFocus" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class MpWindowFocus {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
}
"@
}

function Get-MpInstalledComponents {
    # The shared selection file is a single space-separated line of ids. When it
    # is absent this is a full install, so every menu entry stays.
    param([string]$InstallRoot)
    $full = @("manager", "gui", "liquid", "visualizer")
    $file = Join-Path $env:USERPROFILE ".config\mix-manager\installed_apps"
    if (-not (Test-Path -LiteralPath $file)) { return $full }
    try {
        $text = (Get-Content -LiteralPath $file -ErrorAction Stop | Select-Object -First 1)
    } catch {
        return $full
    }
    if (-not $text -or -not $text.Trim()) { return $full }
    $found = @()
    foreach ($token in ($text.Trim() -split "\s+")) {
        if ($token) { $found += $token }
    }
    if ($found.Count -eq 0) { return $full }
    return $found
}

function Show-MpWindow {
    param([string]$TitlePart)
    $match = Get-Process | Where-Object {
        $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -like ("*" + $TitlePart + "*")
    } | Select-Object -First 1
    if (-not $match) { return $false }
    $handle = $match.MainWindowHandle
    if ([MpWindowFocus]::IsIconic($handle)) {
        [MpWindowFocus]::ShowWindow($handle, 9) | Out-Null
    }
    [MpWindowFocus]::ShowWindow($handle, 5) | Out-Null
    [MpWindowFocus]::BringWindowToTop($handle) | Out-Null
    [MpWindowFocus]::SetForegroundWindow($handle) | Out-Null
    return $true
}

function Get-MpCommandLines {
    $rows = @()
    foreach ($proc in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        if ($proc.ProcessId -eq $PID) { continue }
        $command = [string]$proc.CommandLine
        if (-not $command) { continue }
        if ($command -like "*windows_tray.ps1*") { continue }
        if ($command -like "*windows_user_integration.ps1*") { continue }
        $rows += [pscustomobject]@{ ProcessId = $proc.ProcessId; CommandLine = $command }
    }
    return $rows
}

function Test-MpRoleRunning {
    param([string]$Role)
    foreach ($row in @(Get-MpCommandLines)) {
        $command = $row.CommandLine
        switch ($Role) {
            "gui" {
                if ($command -like "*MP_Gui.sh*") { return $true }
                if ($command -like "*launch_mp_gui*") { return $true }
                if ($command -like "*MP_Audio_Player_Liquid_Glass*" -and $command -like "*--manager*") { return $true }
            }
            "liquid" {
                if ($command -like "*MP_Audio_Player_Liquid_Glass*" -and $command -notlike "*--manager*") { return $true }
                if ($command -like "*launch_liquid_glass*" -or $command -like "*launch-liquid-glass*") { return $true }
            }
            "manager" {
                if ($command -like "*Mix_Archive_Manager.sh*") { return $true }
                if ($command -like "*manager.ps1*") { return $true }
            }
        }
    }
    return $false
}

function Test-MpSuiteRunning {
    foreach ($role in @("gui", "liquid", "manager")) {
        if (Test-MpRoleRunning $role) { return $true }
    }
    foreach ($row in @(Get-MpCommandLines)) {
        if ($row.CommandLine -like "*MP_Liquid_Visualizer*" -or $row.CommandLine -like "*mp_liquid_visualizer*") {
            return $true
        }
    }
    return $false
}

# MP Gui and MP Liquid Player may have at most four windows. The count here
# matches the launch cap in scripts\mp_instance_limit.py; the .bat launchers
# and the Liquid Glass hosts enforce the same number.
$script:maxCappedInstances = 4

function Get-MpRoleCount {
    param([string]$Role)
    $hosts = 0
    foreach ($row in @(Get-MpCommandLines)) {
        $command = [string]$row.CommandLine
        if ($command -like "*mp_instance_limit*") { continue }
        if ($command -like "*--self-test*" -or $command -like "*--embed-check*") { continue }
        if ($command -like "*--tracklist*") { continue }
        if ($command -notlike "*MP_Audio_Player_Liquid_Glass*") { continue }
        if ($Role -eq "gui") {
            if ($command -like "*--manager*") { $hosts++ }
        } elseif ($command -notlike "*--manager*") {
            $hosts++
        }
    }
    return $hosts
}

function Test-MpRoleAtLimit {
    param([string]$Role)
    if ($Role -notin @("gui", "liquid")) { return $false }
    return (Get-MpRoleCount -Role $Role) -ge $script:maxCappedInstances
}

function Show-MpLimitBalloon {
    param([string]$Role)
    $label = if ($Role -eq "gui") { "MP Gui" } else { "MP Liquid Player" }
    if ($notify) {
        try {
            $notify.ShowBalloonTip(6000, "MP Mix Manager", "$label is already open $($script:maxCappedInstances) times. Close a window first.", [System.Windows.Forms.ToolTipIcon]::Warning)
        } catch {
        }
    }
    Write-MpTrayLog "$label launch refused: $($script:maxCappedInstances) instances already open"
}

function Get-MpBash {
    $candidates = @(
        (Join-Path $env:ProgramFiles "Git\bin\bash.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Git\bin\bash.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Git\bin\bash.exe"),
        "C:\msys64\usr\bin\bash.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    $found = Get-Command bash.exe -ErrorAction SilentlyContinue
    if ($found -and ($found.Source -notlike "*WindowsApps*")) { return $found.Source }
    return $null
}

function Get-MpBash {
    $candidates = @(
        (Join-Path $env:ProgramFiles "Git\bin\bash.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Git\bin\bash.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Git\bin\bash.exe"),
        "C:\msys64\usr\bin\bash.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    $found = Get-Command bash.exe -ErrorAction SilentlyContinue
    if ($found -and ($found.Source -notlike "*WindowsApps*")) { return $found.Source }
    return $null
}

function Get-MpSuiteCloseFile {
    return (Join-Path $env:USERPROFILE ".config\mix-manager\instances\suite.closing")
}

function Get-MpPlayerStateFile {
    return (Join-Path $env:USERPROFILE ".config\mix-manager\mp_player_state.json")
}

function Get-MpPlayerMuted {
    # The player rewrites its state file continuously, and it is this file the
    # player window, the Web Companion and the Mac menu bar all read. Reading
    # it here keeps one notion of "muted" for every front end.
    $path = Get-MpPlayerStateFile
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    try {
        $state = Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json
        return [bool]$state.muted
    } catch {
        return $false
    }
}

function Get-MpMuteLabel {
    if (Get-MpPlayerMuted) { return "Unmute MP Player Audio" }
    return "Mute MP Player Audio"
}

function Send-MpPlayerCommand {
    # Talk to the player's own IPC socket. The tray must not import the player
    # (that pulls in PortAudio and the whole engine), so it speaks the same
    # small JSON protocol the headset buttons and Web Companion use.
    #
    # A modern Python on Windows exposes AF_UNIX, so the player may be
    # listening on a Unix-domain socket instead of a TCP port, and Windows
    # PowerShell 5.1 cannot open one. When the port path cannot reach it, fall
    # back to the player's own `--cmd` entry point, which uses whatever
    # transport the player actually started.
    param([string]$Command)
    $cfg = Join-Path $env:USERPROFILE ".config\mix-manager"
    $portFile = Join-Path $cfg "mp_audio_player.port"
    if (Test-Path -LiteralPath $portFile) {
        $payload = [System.Text.Encoding]::UTF8.GetBytes((@{ cmd = $Command } | ConvertTo-Json -Compress))
        try {
            $port = [int]((Get-Content -LiteralPath $portFile -Raw).Trim())
            $client = New-Object System.Net.Sockets.TcpClient
            $client.Connect("127.0.0.1", $port)
            $stream = $client.GetStream()
            $stream.Write($payload, 0, $payload.Length)
            $stream.Flush()
            $buffer = New-Object byte[] 4096
            $read = $stream.Read($buffer, 0, $buffer.Length)
            $client.Close()
            if ($read -gt 0) { return $true }
        } catch {
        }
    }
    $player = Join-Path $Root "MP_Audio_Player.py"
    if (-not (Test-Path -LiteralPath $player)) { return $false }
    $python = $null
    foreach ($name in @("python.exe", "pythonw.exe")) {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        if ($found -and ($found.Source -notlike "*WindowsApps*")) { $python = $found.Source; break }
    }
    if (-not $python) { return $false }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $python $player --cmd $Command 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Show-MpNotice {
    # The window manager's balloon/notification is the Windows equivalent of
    # the desktop notice the Linux and macOS trays post when a rescan ends.
    param([string]$Title, [string]$Message)
    try {
        if ($notify) {
            $notify.ShowBalloonTip(8000, $Title, $Message, [System.Windows.Forms.ToolTipIcon]::Info)
            return
        }
    } catch {
    }
    Write-MpTrayLog "$Title`: $Message"
}

function Set-MpSuiteClosing {
    # The windows being stopped read this file and stay silent, so the movie
    # does not run twice for one close. It is the same marker the Python tray
    # writes, and it expires on its own after a few minutes.
    try {
        $path = Get-MpSuiteCloseFile
        $dir = Split-Path -Parent $path
        if (-not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        [System.IO.File]::WriteAllText($path, "$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())`r`n")
    } catch {
    }
}

function Clear-MpSuiteClosing {
    try {
        $path = Get-MpSuiteCloseFile
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
    } catch {
    }
}

function Show-MpExitVideo {
    # Close MP Mix Manager kills every MP process, so the windows never reach
    # the shutdown path that plays Exit_MP_Video.mp4 themselves. Play it here
    # first, in the same borderless window, and only then stop the suite.
    # --exit-force ignores the "last window" election: every window is about
    # to be stopped, so the movie must play regardless of how many are open.
    $bash = Get-MpBash
    if (-not $bash) { return }
    $script = Join-Path $Root "scripts\display_loader_video.sh"
    if (-not (Test-Path -LiteralPath $script)) { return }
    try {
        Write-MpTrayLog "close-all: playing the exit video"
        Set-MpSuiteClosing
        & $bash $script --exit-force | Out-Null
    } catch {
        Write-MpTrayLog "close-all: exit video failed: $($_.Exception.Message)"
    }
}

function Stop-MpSuite {
    # Every pattern the tray knows about, so a Close All is complete even when
    # one launcher names a script differently from another. The list matches
    # SUITE_HELPER_SCRIPTS in scripts/mp_system_tray.py plus the role launchers.
    $deadline = (Get-Date).AddSeconds(6)
    while ($true) {
        $found = $false
        foreach ($row in @(Get-MpCommandLines)) {
            $command = [string]$row.CommandLine
            if ($command -like "*mp_system_tray*") { continue }
            $isMp = $false
            foreach ($needle in @(
                "Mix_Archive_Manager.sh",
                "mix-archive-manager",
                "launch-manager",
                "MP_Gui.sh",
                "launch_mp_gui",
                "launch-mp-gui",
                "MP_Audio_Player",
                "MP_Liquid_Visualizer",
                "mp_liquid_visualizer",
                "launch_liquid_glass",
                "launch-liquid-glass",
                "launch_liquid_visualizer",
                "traktor_s4_midi",
                "manager.ps1"
            )) {
                if ($command -like "*$needle*") { $isMp = $true; break }
            }
            if ($isMp) {
                $found = $true
                Stop-Process -Id $row.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
        # Windows needs a beat before the process table reflects the kills; a
        # second sweep catches a window that was still starting up.
        if (-not $found) { break }
        if ((Get-Date) -ge $deadline) { break }
        Start-Sleep -Milliseconds 250
    }
}

function Start-MpRole {
    param([string]$Role)
    $titles = @{
        gui = "MP Mix Manager (Liquid Glass"
        liquid = "MP Audio Player (Liquid Glass"
        manager = "MP Mix Archive Manager"
    }
    # MP Gui and MP Liquid Player open new windows until four are up. Only
    # past that does a launch raise the window that is already open.
    if (Test-MpRoleAtLimit -Role $Role) {
        Show-MpWindow -TitlePart $titles[$Role] | Out-Null
        Show-MpLimitBalloon -Role $Role
        return
    }
    if ($Role -eq "manager" -and (Show-MpWindow -TitlePart $titles[$Role])) { return }
    $target = $null
    switch ($Role) {
        "gui" {
            $target = Join-Path $Root "launch_mp_gui.bat"
            if (-not (Test-Path -LiteralPath $target)) {
                $target = Join-Path $env:USERPROFILE ".config\mix-manager\launchers\MP Gui.cmd"
            }
        }
        "liquid" { $target = Join-Path $Root "launch_liquid_glass.bat" }
        "manager" { $target = Join-Path $Root "manager.bat" }
    }
    if ($target -and (Test-Path -LiteralPath $target)) {
        Start-Process -FilePath $target -WorkingDirectory $Root | Out-Null
    }
}

$script:busyUntil = [datetime]::MinValue
$script:absentSince = $null
$script:closing = $false
# Close-all runs in a background runspace so the tray's message loop keeps
# running while the exit movie plays. These hold that worker and its result.
$script:closeWorker = $null
$script:closeState = $null
$script:closeTimer = $null
$script:closeItem = $null
$script:closeItemText = ""
$script:muteItem = $null
$script:rescanProcess = $null
$script:rescanTimer = $null
$script:rescanAction = ""
$script:rescanItems = @()
$script:rescanOutFile = ""
$script:rescanErrFile = ""

function Register-MpLaunch {
    $script:busyUntil = (Get-Date).AddSeconds(20)
    $script:absentSince = $null
}

function Set-MpRescanItemsEnabled {
    param([bool]$Enabled)
    foreach ($entry in @($script:rescanItems)) {
        if (-not $entry) { continue }
        $entry.Item.Enabled = $Enabled
        $entry.Item.Text = $entry.Text
    }
}

function Start-MpRescanProcess {
    # A large archive or a slow SMB share takes minutes. Running the scan in
    # the menu handler froze the icon the same way an inline Close All did, so
    # it runs as a child process and a UI timer collects the result.
    param([string]$ActionId)
    if ($script:rescanProcess -and -not $script:rescanProcess.HasExited) { return }
    $bash = Get-MpBash
    if (-not $bash) {
        Show-MpNotice -Title "MP Mix Manager" -Message "No Bash environment was found for the rescan."
        return
    }
    $posixRoot = $Root -replace '\\', '/'
    $outFile = Join-Path $env:TEMP ("mp-rescan-{0}.out" -f ([guid]::NewGuid().ToString("N")))
    $errFile = "$outFile.err"
    if ($ActionId -eq "rescan-archive") {
        $argumentList = @("`"$posixRoot/Mix_Archive_Manager.sh`"", "--rescan-archive")
    } else {
        $argumentList = @("-c", "`"python3 '$posixRoot/scripts/music_folders_manager.py' --generate ALL`"")
    }
    try {
        # Redirect to files instead of pipes: a scan that prints megabytes of
        # progress would fill a pipe buffer and block forever if nothing read
        # it while the icon was still being used.
        $proc = Start-Process -FilePath $bash -ArgumentList $argumentList `
            -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $outFile -RedirectStandardError $errFile
    } catch {
        Show-MpNotice -Title "MP Mix Manager" -Message "The rescan could not start: $($_.Exception.Message)"
        return
    }
    $script:rescanProcess = $proc
    $script:rescanAction = $ActionId
    $script:rescanOutFile = $outFile
    $script:rescanErrFile = $errFile
    Set-MpRescanItemsEnabled -Enabled $false
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 500
    $timer.Add_Tick({
        try {
            if (-not $script:rescanProcess -or -not $script:rescanProcess.HasExited) { return }
            if ($script:rescanTimer) {
                $script:rescanTimer.Stop()
                $script:rescanTimer.Dispose()
                $script:rescanTimer = $null
            }
            $stdout = ""
            $stderr = ""
            # The scan wrote to files (see Start-MpRescanProcess), so read
            # them rather than a pipe nobody was draining.
            try {
                if (Test-Path -LiteralPath $script:rescanOutFile) {
                    $stdout = [System.IO.File]::ReadAllText($script:rescanOutFile)
                }
            } catch { }
            try {
                if (Test-Path -LiteralPath $script:rescanErrFile) {
                    $stderr = [System.IO.File]::ReadAllText($script:rescanErrFile)
                }
            } catch { }
            if (Test-Path -LiteralPath $script:rescanOutFile) {
                Remove-Item -LiteralPath $script:rescanOutFile -Force -ErrorAction SilentlyContinue
            }
            if (Test-Path -LiteralPath $script:rescanErrFile) {
                Remove-Item -LiteralPath $script:rescanErrFile -Force -ErrorAction SilentlyContinue
            }
            Set-MpRescanItemsEnabled -Enabled $true
            $action = $script:rescanAction
            try { $script:rescanProcess.Dispose() } catch { }
            $script:rescanProcess = $null
            Show-MpRescanResult -ActionId $action -Stdout $stdout -Stderr $stderr
        } catch {
            Write-MpTrayLog "rescan result failed: $($_.Exception.Message)"
        }
    })
    $script:rescanTimer = $timer
    $timer.Start()
}

function Show-MpRescanResult {
    param([string]$ActionId, [string]$Stdout, [string]$Stderr)
    $strip = [regex]::new("\x1B\[[0-9;?]*[ -/]*[@-~]")
    $text = $strip.Replace((($Stdout, $Stderr) -join "`n"), "")
    if ($ActionId -eq "rescan-archive") {
        $fields = @{}
        foreach ($line in ($text -split "`n")) {
            if ($line -match '^RESCAN_([A-Z_]+)=(.*)$') { $fields[$Matches[1]] = $Matches[2].Trim() }
        }
        if ($fields["STATUS"] -ne "ok") {
            $detail = $fields["DETAIL"]
            if (-not $detail) { $detail = "The archive rescan did not finish." }
            Show-MpNotice -Title "Rescan Mix Archive Folders" -Message "Archive rescan failed. $detail"
            return
        }
        $parts = @(
            "$($fields["FOLDERS"]) archive folder(s) scanned",
            "$($fields["FLACS"]) FLACs",
            "$($fields["WAVS"]) converted WAVs"
        )
        if ([int]$fields["PENDING_WAVS"] -gt 0) { $parts += "$($fields["PENDING_WAVS"]) WAVs waiting for conversion" }
        if ([int]$fields["MISSING_TRACKLISTS"] -gt 0) { $parts += "$($fields["MISSING_TRACKLISTS"]) missing tracklists" }
        Show-MpNotice -Title "Rescan Mix Archive Folders" -Message ($parts -join " | ")
        return
    }
    $complete = @($text -split "`n" | Where-Object { $_ -match 'Playlist Generation Complete \(([0-9,]+) total tracks' })
    if ($complete.Count -eq 0) {
        $detail = @($text -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 1)
        if (-not $detail) { $detail = "The music folder rescan did not finish." }
        Show-MpNotice -Title "Rescan Music Folders" -Message "Music folder rescan failed. $detail"
        return
    }
    $null = $complete[-1] -match '\(([0-9,]+) total tracks'
    $tracks = $Matches[1]
    $saved = @($text -split "`n" | Where-Object { $_ -match 'Saved .* Playlist:' }).Count
    Show-MpNotice -Title "Rescan Music Folders" -Message "$saved music folder(s) refreshed | $tracks tracks in ALL"
}

function Invoke-MpTrayAction {
    param([string]$ActionId)
    if ($script:closing) { return }
    if ($ActionId -eq "rescan-archive" -or $ActionId -eq "rescan-music") {
        Start-MpRescanProcess -ActionId $ActionId
        return
    }
    if ($ActionId -eq "mute-audio") {
        if (-not (Send-MpPlayerCommand -Command "mute")) {
            Show-MpNotice -Title "Mute MP Player Audio" -Message "MP Audio Player is not running, so there is no audio to mute."
        }
        if ($script:muteItem) { $script:muteItem.Text = Get-MpMuteLabel }
        return
    }
    if ($ActionId -eq "close") {
        $script:closing = $true
        # The movie runs for about 40 seconds, then the suite is stopped. Doing
        # that inline froze the tray's message loop for the whole minute: the
        # menu stayed painted, the icon stopped answering, and Windows treats
        # a blocked notification icon as hung. That is why Close All could look
        # dead and leave every window open.
        #
        # The work now runs in a background runspace. It sets Done when the
        # last MP process is gone; a UI timer watches that flag and then quits
        # this process, so the icon stays live throughout.
        $script:closeDone = $false
        $script:closeError = ""
        $script:closeItemText = "Closing MP Mix Manager..."
        if ($script:closeItem) {
            $script:closeItem.Enabled = $false
            $script:closeItem.Text = $script:closeItemText
        }
        if ($script:closeTimer) { $script:closeTimer.Stop(); $script:closeTimer.Dispose() }
        try {
            $state = [hashtable]::Synchronized(@{
                Root = $Root
                Done = $false
                Error = ""
            })
            $script:closeState = $state
            $worker = [PowerShell]::Create()
            $script:closeWorker = $worker
            [void]$worker.AddScript({
                param($State)
                $ErrorActionPreference = "Continue"
                try { Set-MpSuiteClosing } catch { }
                try { Show-MpExitVideo } catch { }
                try { Stop-MpSuite } catch { $State.Error = "$($_.Exception.Message)" }
                # Re-check: the movie and the first sweep can race a window
                # that is still starting up.
                $deadline = (Get-Date).AddSeconds(6)
                while ((Get-Date) -lt $deadline) {
                    if (-not (Test-MpSuiteRunning)) { break }
                    Start-Sleep -Milliseconds 250
                    try { Stop-MpSuite } catch { }
                }
                try { Clear-MpSuiteClosing } catch { }
                $State.Done = $true
            }).AddArgument($state)
            [void]$worker.BeginInvoke()
        } catch {
            Write-MpTrayLog "close-all worker failed: $($_.Exception.Message)"
            # Without a worker, fall back to the inline path rather than never
            # closing anything.
            try { Show-MpExitVideo } catch { }
            try { Stop-MpSuite } catch { }
            try { Clear-MpSuiteClosing } catch { }
            [System.Windows.Forms.Application]::Exit()
            return
        }
        $script:closeTimer = New-Object System.Windows.Forms.Timer
        $script:closeTimer.Interval = 250
        $script:closeTimer.Add_Tick({
            try {
                if ($script:closeState -and -not $script:closeState.Done) { return }
                $script:closeTimer.Stop()
                if ($script:closeState -and $script:closeState.Error) {
                    Write-MpTrayLog "close-all: $($script:closeState.Error)"
                }
            } finally {
                [System.Windows.Forms.Application]::Exit()
            }
        })
        $script:closeTimer.Start()
        return
    }
    $role = switch ($ActionId) {
        "gui" { "gui" }
        "liquid" { "liquid" }
        "manager" { "manager" }
        default { "" }
    }
    if (-not $role) { return }
    Register-MpLaunch
    Start-MpRole -Role $role
}

function New-MpClickHandler {
    param([string]$ActionId)
    return {
        param($sender, $eventArgs)
        Invoke-MpTrayAction $ActionId
    }.GetNewClosure()
}

. (Join-Path $PSScriptRoot "windows_liquid_glass_icon.ps1")
$script:ownIcon = $false
$script:iconStream = $null
$icon = $null
# assets\mp_liquid_glass_icon.png, packaged as an .ico Windows can show.
$ico = Get-MpLiquidGlassIconPath -InstallRoot $Root
if ($ico -and (Test-Path -LiteralPath $ico)) {
    try {
        $bytes = [System.IO.File]::ReadAllBytes($ico)
        $script:iconStream = New-Object System.IO.MemoryStream(,$bytes)
        $icon = New-Object System.Drawing.Icon($script:iconStream)
        $script:ownIcon = $true
    } catch {
        $icon = $null
    }
}
if (-not $icon) {
    $icon = [System.Drawing.SystemIcons]::Application
}

function Show-MpTrayButton {
    # A new icon stays in the overflow. Dragging it onto the user notification
    # area keeps it on the taskbar. An icon the user hid is not in the overflow,
    # so it is left there. Each call does one short step and returns, so the
    # notification icon keeps its message pump between steps.
    if (-not ("MpTrayButton" -as [type])) {
        Add-Type -AssemblyName UIAutomationClient
        Add-Type -AssemblyName UIAutomationTypes
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using Accessibility;
public static class MpTrayButton {
    public delegate bool EnumProc(IntPtr h, IntPtr l);
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT pt);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
    [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT pt);
    [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint flags);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder sb, int n);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder sb, int n);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc proc, IntPtr l);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr parent, EnumProc proc, IntPtr l);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr FindWindow(string cls, string name);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("oleacc.dll")] public static extern int AccessibleObjectFromWindow(IntPtr hwnd, uint objid, ref Guid iid, [MarshalAs(UnmanagedType.Interface)] out IAccessible acc);
    public static string LastPlace;
    public static int FoundX;
    public static int FoundY;
    public static int SavedX;
    public static int SavedY;
    public static bool Saved;
    public static void Remember() {
        POINT pt;
        GetCursorPos(out pt);
        SavedX = pt.X;
        SavedY = pt.Y;
        Saved = true;
    }
    public static void Restore() {
        if (Saved) SetCursorPos(SavedX, SavedY);
    }
    static IAccessible Access(IntPtr hwnd) {
        Guid iid = new Guid("618736E0-3C3D-11CF-810C-00AA00389B71");
        IAccessible acc;
        int hr = AccessibleObjectFromWindow(hwnd, 0xFFFFFFFC, ref iid, out acc);
        if (hr != 0) return null;
        return acc;
    }
    public static bool ContainsName(IntPtr hwnd, string name) {
        IAccessible acc = Access(hwnd);
        if (acc == null) return false;
        int count = acc.accChildCount;
        for (int i = 1; i <= count; i++) {
            try { if (acc.get_accName(i) == name) return true; } catch { }
        }
        return false;
    }
    public static bool FindNamed(IntPtr hwnd, string name) {
        FoundX = 0;
        FoundY = 0;
        IAccessible acc = Access(hwnd);
        if (acc == null) return false;
        int count = acc.accChildCount;
        for (int i = 1; i <= count; i++) {
            string item = "";
            try { item = "" + acc.get_accName(i); } catch { continue; }
            if (item != name) continue;
            int l, t, w, h;
            try { acc.accLocation(out l, out t, out w, out h, i); } catch { return false; }
            if (w < 8 || h < 8) return false;
            FoundX = l + (w / 2);
            FoundY = t + (h / 2);
            return true;
        }
        return false;
    }
    public static string Where(int x, int y) {
        POINT pt; pt.X = x; pt.Y = y;
        IntPtr root = GetAncestor(WindowFromPoint(pt), 2);
        var cls = new StringBuilder(80);
        GetClassName(root, cls, 80);
        return cls.ToString();
    }
    public static bool Place(int x, int y) {
        int aimX = x, aimY = y;
        POINT cur;
        for (int i = 0; i < 6; i++) {
            SetCursorPos(aimX, aimY);
            System.Threading.Thread.Sleep(40);
            GetCursorPos(out cur);
            LastPlace = cur.X + "," + cur.Y;
            if (Math.Abs(cur.X - x) <= 2 && Math.Abs(cur.Y - y) <= 2) return true;
            aimX += x - cur.X;
            aimY += y - cur.Y;
        }
        return false;
    }
    public static IntPtr FindVisible(string cls) {
        IntPtr hit = IntPtr.Zero;
        EnumProc proc = delegate(IntPtr h, IntPtr l) {
            var c = new StringBuilder(80);
            GetClassName(h, c, 80);
            if (c.ToString() == cls && IsWindowVisible(h)) { hit = h; return false; }
            return true;
        };
        EnumWindows(proc, IntPtr.Zero);
        GC.KeepAlive(proc);
        return hit;
    }
    public static IntPtr FindChild(IntPtr parent, string cls, string title) {
        IntPtr hit = IntPtr.Zero;
        EnumProc proc = delegate(IntPtr h, IntPtr l) {
            var c = new StringBuilder(80);
            var t = new StringBuilder(160);
            GetClassName(h, c, 80);
            GetWindowText(h, t, 160);
            if (c.ToString() == cls && (title == null || t.ToString() == title)) { hit = h; return false; }
            return true;
        };
        EnumChildWindows(parent, proc, IntPtr.Zero);
        GC.KeepAlive(proc);
        return hit;
    }
}
"@ -ReferencedAssemblies Accessibility
    }
    [MpTrayButton]::SetProcessDPIAware() | Out-Null
    if (-not $script:trayPromote) {
        $script:trayPromote = @{ Phase = "look"; Tries = 0; ChevronX = 0; ChevronY = 0 }
        [MpTrayButton]::Remember()
    }
    $step = $script:trayPromote
    $shell = [MpTrayButton]::FindWindow("Shell_TrayWnd", $null)
    $pager = [MpTrayButton]::FindChild($shell, "SysPager", $null)
    $promoted = [MpTrayButton]::FindChild($pager, "ToolbarWindow32", "User Promoted Notification Area")
    if ($promoted -eq [IntPtr]::Zero) { throw "taskbar notification area was not found" }
    if ([MpTrayButton]::ContainsName($promoted, "MP Mix Manager")) {
        if ($step.Phase -eq "look") {
            Write-MpTrayLog "promoted already"
        } else {
            Close-MpOverflowChevron -X $step.ChevronX -Y $step.ChevronY
            Write-MpTrayLog "promoted"
        }
        [MpTrayButton]::Restore()
        return $true
    }
    switch ($step.Phase) {
        "look" {
            $root = [System.Windows.Automation.AutomationElement]::RootElement
            $trayCond = New-Object System.Windows.Automation.PropertyCondition ([System.Windows.Automation.AutomationElement]::ClassNameProperty, "Shell_TrayWnd")
            $tray = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $trayCond)
            $idCond = New-Object System.Windows.Automation.PropertyCondition ([System.Windows.Automation.AutomationElement]::AutomationIdProperty, "1502")
            $chevron = $null
            if ($tray) { $chevron = $tray.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $idCond) }
            if (-not $chevron) { throw "overflow button was not found" }
            $chevRect = $chevron.Current.BoundingRectangle
            $step.ChevronX = [int]($chevRect.X + ($chevRect.Width / 2))
            $step.ChevronY = [int]($chevRect.Y + ($chevRect.Height / 2))
            if ([MpTrayButton]::Where($step.ChevronX, $step.ChevronY) -ne "Shell_TrayWnd") { throw "overflow button is covered" }
            $overflow = [MpTrayButton]::FindVisible("NotifyIconOverflowWindow")
            if ($overflow -eq [IntPtr]::Zero) {
                if (-not (Invoke-MpTrayClick -X $step.ChevronX -Y $step.ChevronY -ExpectedClass "Shell_TrayWnd")) {
                    throw "could not reach the overflow button"
                }
            }
            $step.Phase = "seek"
            return $false
        }
        "seek" {
            $step.Tries++
            $overflow = [MpTrayButton]::FindVisible("NotifyIconOverflowWindow")
            if ($overflow -eq [IntPtr]::Zero) {
                if ($step.Tries -ge 4) { throw "overflow did not open" }
                return $false
            }
            $bar = [MpTrayButton]::FindChild($overflow, "ToolbarWindow32", "Overflow Notification Area")
            if ($bar -eq [IntPtr]::Zero) { $bar = [MpTrayButton]::FindChild($overflow, "ToolbarWindow32", $null) }
            if ([MpTrayButton]::FindNamed($bar, "MP Mix Manager")) {
                $sourceX = [MpTrayButton]::FoundX
                $sourceY = [MpTrayButton]::FoundY
                if ([MpTrayButton]::Where($sourceX, $sourceY) -ne "NotifyIconOverflowWindow") {
                    if ($step.Tries -ge 12) { throw "icon is covered" }
                    return $false
                }
                $dest = New-Object MpTrayButton+RECT
                [MpTrayButton]::GetWindowRect($promoted, [ref]$dest) | Out-Null
                $destX = [int]($dest.L + 8)
                $destY = [int](($dest.T + $dest.B) / 2)
                if ([MpTrayButton]::Where($destX, $destY) -ne "Shell_TrayWnd") { throw "taskbar icon area is covered" }
                $down = $false
                try {
                    if (-not [MpTrayButton]::Place($sourceX, $sourceY)) { throw "could not reach the icon" }
                    if ([MpTrayButton]::Where($sourceX, $sourceY) -ne "NotifyIconOverflowWindow") { throw "icon is covered" }
                    [MpTrayButton]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero)
                    $down = $true
                    Start-Sleep -Milliseconds 150
                    if (-not [MpTrayButton]::Place($destX, $destY)) { throw "could not reach the taskbar" }
                    if ([MpTrayButton]::Where($destX, $destY) -ne "Shell_TrayWnd") { throw "taskbar icon area is covered" }
                    Start-Sleep -Milliseconds 150
                    [MpTrayButton]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero)
                    $down = $false
                    Start-Sleep -Milliseconds 400
                } finally {
                    if ($down) { [MpTrayButton]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero) }
                    [MpTrayButton]::Restore()
                }
                $step.Phase = "settle"
                return $false
            }
            if ($step.Tries -ge 12) { throw "icon is not in the overflow" }
            return $false
        }
        "settle" {
            throw "icon did not stay on the taskbar"
        }
    }
    return $false
}

function Invoke-MpTrayClick {
    param([int]$X, [int]$Y, [string]$ExpectedClass)
    if ([MpTrayButton]::Where($X, $Y) -ne $ExpectedClass) { return $false }
    if (-not [MpTrayButton]::Place($X, $Y)) {
        [MpTrayButton]::Restore()
        return $false
    }
    if ([MpTrayButton]::Where($X, $Y) -ne $ExpectedClass) {
        [MpTrayButton]::Restore()
        return $false
    }
    [MpTrayButton]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 40
    [MpTrayButton]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero)
    [MpTrayButton]::Restore()
    return $true
}

function Close-MpOverflowChevron {
    param([int]$X, [int]$Y)
    if ([MpTrayButton]::FindVisible("NotifyIconOverflowWindow") -eq [IntPtr]::Zero) { return }
    if ([MpTrayButton]::Where($X, $Y) -ne "Shell_TrayWnd") { return }
    Invoke-MpTrayClick -X $X -Y $Y -ExpectedClass "Shell_TrayWnd" | Out-Null
}

$notify = $null
$timer = $null
$script:promoteTimer = $null
try {
    $notify = New-Object System.Windows.Forms.NotifyIcon
    $notify.Icon = $icon
    $notify.Text = "MP Mix Manager"
    $notify.Visible = $true

    $menu = New-Object System.Windows.Forms.ContextMenuStrip
    $entries = @(
        @{ Id = "gui"; Label = "Launch MP Gui"; Component = "gui" },
        @{ Id = "liquid"; Label = "Launch MP Liquid Player"; Component = "liquid" },
        @{ Id = "manager"; Label = "Launch MP Mix Manager"; Component = "manager" },
        @{ Id = "rescan-archive"; Label = "Rescan Mix Archive Folders"; Component = "" },
        @{ Id = "rescan-music"; Label = "Rescan Music Folders"; Component = "" },
        @{ Id = "mute-audio"; Label = "Mute MP Player Audio"; Component = "" },
        @{ Id = "close"; Label = "Close MP Mix Manager (Closes everything)"; Component = "" }
    )
    $installed = Get-MpInstalledComponents -InstallRoot $Root
    foreach ($entry in $entries) {
        if ($entry.Id -eq "close") {
            [void]$menu.Items.Add("-")
        } elseif ($entry.Component -and -not $installed.Contains($entry.Component)) {
            # This setup did not install that app, so offering to launch it
            # would only fail. Close All and the separators stay.
            continue
        }
        $item = $menu.Items.Add([string]$entry.Label)
        $item.Add_Click((New-MpClickHandler -ActionId ([string]$entry.Id))) | Out-Null
        if ($entry.Id -eq "close") {
            # Keep a handle so the label can say "Closing..." and a second
            # click cannot start a second movie.
            $script:closeItem = $item
        } elseif ($entry.Id -eq "mute-audio") {
            # Keep a handle so the title can say Unmute while the player is
            # muted. Unlike the Linux tray this item is refreshed as the menu
            # opens, because WinForms has no per-open callback.
            $script:muteItem = $item
        } elseif ($entry.Id -like "rescan-*") {
            # Both scan items are disabled while one runs, so a second click
            # cannot start a second scan over the same folders.
            $script:rescanItems += ,@{ Item = $item; Text = [string]$entry.Label }
        }
    }
    $menu.Add_Opening({
        if ($script:muteItem) { $script:muteItem.Text = Get-MpMuteLabel }
    })
    $notify.ContextMenuStrip = $menu
    $notify.Add_MouseUp({
        param($sender, $eventArgs)
        if ($eventArgs.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
            $menu.Show([System.Windows.Forms.Cursor]::Position)
        }
    })

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 2000
    $timer.Add_Tick({
        if ($Stay -or $script:closing) { return }
        if ((Get-Date) -lt $script:busyUntil) { return }
        if (Test-MpSuiteRunning) {
            $script:absentSince = $null
            return
        }
        if (-not $script:absentSince) {
            $script:absentSince = Get-Date
            return
        }
        if (((Get-Date) - $script:absentSince).TotalSeconds -ge 8) {
            [System.Windows.Forms.Application]::Exit()
        }
    })
    $timer.Start()
    $script:promoteTimer = New-Object System.Windows.Forms.Timer
    $script:promoteTimer.Interval = 1000
    $script:promoteTimer.Add_Tick({
        $script:promoteTimer.Interval = 500
        try {
            if (Show-MpTrayButton) { $script:promoteTimer.Stop() }
        } catch {
            Write-MpTrayLog "promote $($_.Exception.Message)"
            try { Close-MpOverflowChevron -X $script:trayPromote.ChevronX -Y $script:trayPromote.ChevronY } catch { }
            try { [MpTrayButton]::Restore() } catch { }
            $script:promoteTimer.Stop()
        }
    })
    $script:promoteTimer.Start()
    Write-MpTrayLog "ready pid=$PID"
    [System.Windows.Forms.Application]::Run()
} catch {
    Write-MpTrayLog "failed pid=$PID $($_.Exception.Message)"
    exit 1
} finally {
    if ($script:closeTimer) { $script:closeTimer.Stop(); $script:closeTimer.Dispose() }
    if ($script:closeWorker) {
        try { $script:closeWorker.Stop(); $script:closeWorker.Dispose() } catch { }
    }
    if ($script:promoteTimer) { $script:promoteTimer.Stop(); $script:promoteTimer.Dispose() }
    if ($timer) { $timer.Stop(); $timer.Dispose() }
    if ($notify) {
        $notify.Visible = $false
        $notify.Dispose()
    }
    if ($script:ownIcon -and $icon) { $icon.Dispose() }
    if ($script:iconStream) { $script:iconStream.Dispose() }
    if ($owned) {
        try { $mutex.ReleaseMutex() } catch { }
    }
    $mutex.Dispose()
}
Write-MpTrayLog "exit pid=$PID"
exit 0
