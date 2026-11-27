# Building the Windows MSI packages

The Windows installers are WiX 5 MSI packages. They install the whole
application under `Program Files\MP Mix Manager`, add a **MP Mix Manager**
Start Menu shortcut pointing at `manager.bat`, and replace an older install in
place through the shared `UpgradeCode`.

The 0.5.4 packages on GitHub were built on a Windows 11 test machine. This page
describes how to build the same two packages on any Windows 10/11 host.

## What you need

| Requirement | Notes |
| :--- | :--- |
| **.NET SDK 8** | WiX 5 is a .NET tool. A self-contained SDK unpacked on a roomy drive is enough; no system-wide install. |
| **WiX 5.0.2** | Installed as a `dotnet tool` so it never touches `C:`. |
| **Python 3.10+** | Runs the payload stager and the WiX generator. |
| **~1 GB of free space** | SDK + tool + staged tree + output. |

## One-time setup (nothing goes to C:)

```bash
export MP_BUILD_ROOT=X:/mp-build
mkdir -p "$MP_BUILD_ROOT"

# 1. A private .NET SDK
powershell -NoProfile -ExecutionPolicy Bypass -Command "
  Invoke-WebRequest https://dot.net/v1/dotnet-install.ps1 -OutFile $env:TEMP/dotnet-install.ps1;
  & $env:TEMP/dotnet-install.ps1 -Channel 8.0 -InstallDir 'X:\mp-build\dotnet' -NoPath"

# 2. WiX 5.0.2 exactly as the release was built
"X:/mp-build/dotnet/dotnet.exe" tool install wix \
    --tool-path "X:/mp-build/tools" --version 5.0.2
```

## Build

```bash
MP_BUILD_ROOT=X:/mp-build ./scripts/build_windows_msi.sh
```

Output:

```
$MP_BUILD_ROOT/out/MP_Mix_Manager_Windows_x64.msi
$MP_BUILD_ROOT/out/MP_Mix_Manager_Windows_x86.msi
```

`--clean` throws away the staged tree and every intermediate first.

## What goes into the package

`scripts/stage_windows_payload.py` builds the payload tree, and
`scripts/gen_wix_payload.py` turns that tree into WiX components (one component
per file, each file its own key path). The rules match what the shipped 0.5.4
package contains:

* The repository root: every file except `*.log`.
* `scripts/` whole, including `assets/`, `lib/` and `systemd/`.
* `assets/`, `bin/`, `desktop/`, `docs/`, `MP_Liquid_Mixer/`,
  `mplanetarians-alarm-clock/`, `shaders/` (with `presets/`), `themes/`,
  `tests/`, `.pytest_cache/` and `.vscode/`.
* `TRACKLIST_EXPORTS/` with the report files.

`venv/`, `__pycache__/`, the build scratch directory, and the user's archive or
library folders are never packaged.

## Installer behaviour

`scripts/wix/Product.wxs` reproduces the shipped 0.5.4 package: same
`UpgradeCode` (`A9F4241B-E7E6-4E37-BB44-A37E67599C8E`), per-machine install
(`ALLUSERS=1`), no downgrades, `RemoveExistingProducts` after
`InstallValidate`, and a Start Menu shortcut.

### The Windows 10 launch condition

The shipped 0.5.4 build tested `VersionNT >= 1000`. That check is wrong on the
systems it is meant to allow: Microsoft's Windows Installer sets `VersionNT` to
**603** on Windows 10 and Windows Server 2016/2019 by design, and only reports
1000 when a package resets the property itself. The practical effect was that
the 0.5.4 MSI refused every Windows 10 install with error **1603**.

This authoring tests the build number instead:

```xml
<Launch Condition="MsiNTProductType = 1 AND ((VersionNT >= 1000) OR (WINDOWS_BUILD >= 10240))"
        Message="MP Mix Manager supports Windows 10 and Windows 11 only." />
```

`WINDOWS_BUILD` is read from
`HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\CurrentBuildNumber`, where
Windows 10 RTM is 10240, 22H2 is 19045 and Windows 11 starts at 22000. The
`VersionNT` alternative is kept so a future engine that does report 1000 still
passes. Client editions only: `MsiNTProductType = 1` still refuses a server.

## Verifying a build

```bash
# Layout without installing anything
msiexec /a MP_Mix_Manager_Windows_x64.msi /qn TARGETDIR=X:\mp-build\extract

# A real install, then check the shortcut and run the app
msiexec /i MP_Mix_Manager_Windows_x64.msi /qn /l*v install.log
"%ProgramFiles%\MP Mix Manager\manager.bat" --version
```

A quiet install should exit `0`. An exit of `1603` means a launch condition
refused the host — read the `/l*v` log for the `LaunchConditions` action.
