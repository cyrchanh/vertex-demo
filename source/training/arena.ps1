# Starts or stops headless vertex-arena servers (two bots each, controlled from Python).
# Usage:  .\training\arena.ps1 start [-Count 2] | stop | status
# Server i listens for a trainer on 127.0.0.1:(25570 + 10 i); you can watch it on localhost:(25566 + 10 i).
# Server 0 is started through Gradle (which also builds the mod); the others run the same Java
# command in their own folders.
param(
    [Parameter(Mandatory = $true)][ValidateSet("start", "stop", "status")][string]$Command,
    [int]$Count = 1
)

$root = Split-Path $PSScriptRoot -Parent
$home_ = Join-Path $env:LOCALAPPDATA "vertex-training"
$buildDir = Join-Path $env:LOCALAPPDATA "vertex-build"
$log = Join-Path $home_ "arena-server.log"

function Get-ArenaProcesses {
    Get-CimInstance Win32_Process -Filter "Name='java.exe'" |
        Where-Object { $_.CommandLine -like '*projects\arena\launch.cfg*' -or $_.CommandLine -like '*:arena:runServer*' }
}

function Wait-Ready($logFile) {
    for ($i = 0; $i -lt 300; $i++) {
        Start-Sleep -Seconds 1
        if ((Test-Path $logFile) -and (Select-String -Path $logFile -Pattern "waiting for a trainer" -Quiet)) { return "ready after $i s" }
        if ((Test-Path $logFile) -and (Select-String -Path $logFile -Pattern "FAILED|Exception in" -Quiet)) { return "failed to start" }
    }
    "not ready after 300 s"
}

# One more server, in its own folder, with the Java command Loom's run uses.
function Start-Extra($i) {
    $java = Join-Path $env:JAVA_HOME "bin\java.exe"
    $runDir = Join-Path $buildDir "arena-run-$i"
    New-Item -ItemType Directory -Force $runDir | Out-Null
    $template = Join-Path $buildDir "arena-run"
    Copy-Item (Join-Path $template "eula.txt") $runDir -Force
    (Get-Content (Join-Path $template "server.properties")) -replace '^server-port=.*', "server-port=$(25566 + 10 * $i)" |
        Set-Content (Join-Path $runDir "server.properties") -Encoding ascii
    # Loom writes spaces in this path as @@0020; the dev launcher reads it that way.
    $dli = (Join-Path $root ".gradle\loom-cache\projects\arena\launch.cfg") -replace ' ', '@@0020'
    $argFile = Join-Path $buildDir "arena\loom-cache\argFiles\runServer"
    $arguments = "-Xmx1G -Dvertex.arena.port=$(25570 + 10 * $i) `"-Dfabric.dli.config=$dli`" -Dfabric.dli.env=server " +
        "-Dfabric.dli.main=net.fabricmc.loader.impl.launch.knot.KnotServer `"@$argFile`" " +
        "--sun-misc-unsafe-memory-access=allow --enable-native-access=ALL-UNNAMED -Dfile.encoding=UTF-8 " +
        "net.fabricmc.devlaunchinjector.Main nogui --nogui"
    $extraLog = Join-Path $home_ "arena-server-$i.log"
    Remove-Item $extraLog -ErrorAction SilentlyContinue
    Start-Process -FilePath $java -ArgumentList $arguments -WorkingDirectory $runDir `
        -RedirectStandardOutput $extraLog -RedirectStandardError "$extraLog.err" -WindowStyle Hidden
    $extraLog
}

switch ($Command) {
    "status" {
        $p = Get-ArenaProcesses | Where-Object { $_.CommandLine -like '*launch.cfg*' }
        if ($p) { "$(@($p).Count) server(s) running (pids $($p.ProcessId -join ', '))" } else { "not running" }
    }
    "stop" {
        $p = Get-ArenaProcesses
        if ($p) { Stop-Process -Id $p.ProcessId -Force -Confirm:$false; "stopped" } else { "not running" }
    }
    "start" {
        if (Get-ArenaProcesses) { "already running (stop first to change the number of servers)"; return }
        New-Item -ItemType Directory -Force $home_ | Out-Null
        if (-not $env:JAVA_HOME) { $env:JAVA_HOME = "C:\Program Files\Java\jdk-26.0.2" }
        Remove-Item $log -ErrorAction SilentlyContinue
        $gradlew = Join-Path $root "gradlew.bat"
        Start-Process -FilePath $gradlew -ArgumentList ":arena:runServer", "-PvertexBuildDir=$buildDir", "--console=plain" `
            -WorkingDirectory $root -RedirectStandardOutput $log -RedirectStandardError "$log.err" -WindowStyle Hidden
        "server 0 (trainer port 25570, watch localhost:25566): $(Wait-Ready $log) (log: $log)"
        for ($i = 1; $i -lt $Count; $i++) {
            $extraLog = Start-Extra $i
            "server $i (trainer port $(25570 + 10 * $i), watch localhost:$(25566 + 10 * $i)): $(Wait-Ready $extraLog) (log: $extraLog)"
        }
    }
}
