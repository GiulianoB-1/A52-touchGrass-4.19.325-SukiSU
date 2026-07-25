$ErrorActionPreference = 'Continue'

$OutputDirectory = Join-Path (Get-Location) 'TouchGrass-Keymaster-Flight-Recorder-Capture'
$ZipPath = Join-Path (Get-Location) 'TouchGrass-Keymaster-Flight-Recorder-Capture.zip'

if (Test-Path $OutputDirectory) {
    Remove-Item -Recurse -Force $OutputDirectory
}
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null

function Save-CommandOutput {
    param(
        [Parameter(Mandatory = $true)][string]$FileName,
        [Parameter(Mandatory = $true)][scriptblock]$Command
    )

    $Path = Join-Path $OutputDirectory $FileName
    try {
        $Output = & $Command 2>&1
        $ExitCode = $LASTEXITCODE
        @(
            "exit_code=$ExitCode"
            $Output
        ) | Out-File -FilePath $Path -Encoding utf8
        return $ExitCode
    }
    catch {
        @(
            'exit_code=exception'
            $_.Exception.ToString()
        ) | Out-File -FilePath $Path -Encoding utf8
        return 1
    }
}

Write-Host 'Waiting for the phone...'
adb wait-for-device

Write-Host 'Waiting for Android boot completion (up to 180 seconds)...'
for ($i = 0; $i -lt 180; $i++) {
    $Completed = (adb shell getprop sys.boot_completed 2>$null).Trim()
    if ($Completed -eq '1') {
        break
    }
    Start-Sleep -Seconds 1
}

Save-CommandOutput -FileName 'adb-id.txt' -Command {
    adb shell id
} | Out-Null

Save-CommandOutput -FileName 'recorder-permissions.txt' -Command {
    adb shell 'ls -lZ /proc/a52_keymaster_flight_recorder; cat /proc/mounts | grep " /proc "'
} | Out-Null

$RecorderRc = Save-CommandOutput -FileName 'touchgrass-keymaster-flight-recorder.txt' -Command {
    adb shell cat /proc/a52_keymaster_flight_recorder
}

Save-CommandOutput -FileName 'touchgrass-flight-recorder-logcat-all.txt' -Command {
    adb logcat -b all -d -v threadtime
} | Out-Null

Save-CommandOutput -FileName 'touchgrass-flight-recorder-logcat-kernel.txt' -Command {
    adb logcat -b kernel -d -v threadtime
} | Out-Null

Save-CommandOutput -FileName 'touchgrass-flight-recorder-a52kmfr-logcat.txt' -Command {
    adb logcat -b all -d -v threadtime '*:V'
} | Out-Null

Save-CommandOutput -FileName 'touchgrass-flight-recorder-dmesg.txt' -Command {
    adb shell dmesg
} | Out-Null

Save-CommandOutput -FileName 'touchgrass-flight-recorder-getprop.txt' -Command {
    adb shell getprop
} | Out-Null

Save-CommandOutput -FileName 'touchgrass-flight-recorder-cmdline.txt' -Command {
    adb shell cat /proc/cmdline
} | Out-Null

Save-CommandOutput -FileName 'touchgrass-flight-recorder-bootconfig.txt' -Command {
    adb shell cat /proc/bootconfig
} | Out-Null

Save-CommandOutput -FileName 'touchgrass-flight-recorder-version.txt' -Command {
    adb shell 'uname -a; cat /proc/version'
} | Out-Null

Save-CommandOutput -FileName 'touchgrass-flight-recorder-secure-processes.txt' -Command {
    adb shell 'ps -A -Z | grep -Ei "keymaster|keymint|keystore|qsee"'
} | Out-Null

Save-CommandOutput -FileName 'touchgrass-flight-recorder-services.txt' -Command {
    adb shell 'service list | grep -Ei "keymaster|keymint|keystore|gatekeeper"'
} | Out-Null

Save-CommandOutput -FileName 'touchgrass-flight-recorder-pstore-list.txt' -Command {
    adb shell 'ls -laZ /sys/fs/pstore 2>&1'
} | Out-Null

$Summary = @(
    'A52 TOUCHGRASS KEYMASTER FLIGHT RECORDER CAPTURE'
    'Collection mode: no su, no Magisk dependency'
    "Recorder direct-read exit code: $RecorderRc"
    ''
    'A nonzero recorder exit code usually means Android SELinux blocked direct shell access.'
    'The kernel and full Android logcat captures are still included as fallbacks.'
)
$Summary | Out-File -FilePath (Join-Path $OutputDirectory 'README-FIRST.txt') -Encoding utf8

if (Test-Path $ZipPath) {
    Remove-Item -Force $ZipPath
}
Compress-Archive -Force -Path (Join-Path $OutputDirectory '*') -DestinationPath $ZipPath

Write-Host ''
Write-Host "Capture created: $ZipPath"
if ($RecorderRc -ne 0) {
    Write-Warning 'Direct recorder access failed. Upload the ZIP anyway; kernel/logcat fallbacks were captured.'
}
