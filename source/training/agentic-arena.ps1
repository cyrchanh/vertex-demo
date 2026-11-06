# Starts or stops the agentic loop's own vertex-arena server, separate from the trainer's servers.
# Usage:  .\training\agentic-arena.ps1 start | stop | status
# It listens for the loop on 127.0.0.1:25600 and can be watched in Minecraft on localhost:25596.
# It reuses the build that `arena.ps1 start` (Gradle) made, so start a trainer server once first;
# `stop` only stops this server, never the trainer's (they're told apart by the port).
param(
    [Parameter(Mandatory = $true)][ValidateSet("start", "stop", "status")][string]$Command
)

$Port = 25600
$WatchPort = 25596
$root = Split-Path $PSScriptRoot -Parent
$home_ = Join-Path $env:LOCALAPPDATA "vertex-training"
$buildDir = Join-Path $env:LOCALAPPDATA "vertex-build"
$runDir = Join-Path $buildDir "arena-run-agentic"
$log = Join-Path $home_ "arena-server-agentic.log"

function Get-AgenticServer {
    Get-CimInstance Win32_Process -Filter "Name='java.exe'" |
        Where-Object { $_.CommandLine -like "*-Dvertex.arena.port=$Port *" }
}

switch ($Command) {
    "status" {
        $p = Get-AgenticServer
        if ($p) { "agentic arena running (pid $($p.ProcessId), port $Port, watch localhost:$WatchPort)" } else { "not running" }
    }
    "stop" {
        $p = Get-AgenticServer
        if ($p) { Stop-Process -Id $p.ProcessId -Force -Confirm:$false; "stopped" } else { "not running" }
    }
    "start" {
        if (Get-AgenticServer) { "already running"; return }
        if (-not $env:JAVA_HOME) { $env:JAVA_HOME = "C:\Program Files\Java\jdk-26.0.2" }
        $template = Join-Path $buildDir "arena-run"
        $argFile = Join-Path $buildDir "arena\loom-cache\argFiles\runServer"
        $dli = (Join-Path $root ".gradle\loom-cache\projects\arena\launch.cfg") -replace ' ', '@@0020'
        if (-not (Test-Path $argFile) -or -not (Test-Path (Join-Path $template "server.properties"))) {
            "no arena build yet: run .\training\arena.ps1 start once first"; return
        }
        New-Item -ItemType Directory -Force $runDir | Out-Null
        Copy-Item (Join-Path $template "eula.txt") $runDir -Force
        (Get-Content (Join-Path $template "server.properties")) -replace '^server-port=.*', "server-port=$WatchPort" |
            Set-Content (Join-Path $runDir "server.properties") -Encoding ascii
        $java = Join-Path $env:JAVA_HOME "bin\java.exe"
        $arguments = "-Xmx1G -Dvertex.arena.port=$Port `"-Dfabric.dli.config=$dli`" -Dfabric.dli.env=server " +
            "-Dfabric.dli.main=net.fabricmc.loader.impl.launch.knot.KnotServer `"@$argFile`" " +
            "--sun-misc-unsafe-memory-access=allow --enable-native-access=ALL-UNNAMED -Dfile.encoding=UTF-8 " +
            "net.fabricmc.devlaunchinjector.Main nogui --nogui"
        Remove-Item $log -ErrorAction SilentlyContinue
        Start-Process -FilePath $java -ArgumentList $arguments -WorkingDirectory $runDir `
            -RedirectStandardOutput $log -RedirectStandardError "$log.err" -WindowStyle Hidden
        for ($i = 0; $i -lt 300; $i++) {
            Start-Sleep -Seconds 1
            if ((Test-Path $log) -and (Select-String -Path $log -Pattern "waiting for a trainer" -Quiet)) {
                "agentic arena ready after $i s (port $Port, watch localhost:$WatchPort, log $log)"; return
            }
            if ((Test-Path $log) -and (Select-String -Path $log -Pattern "FAILED|Exception in" -Quiet)) {
                "failed to start (log $log)"; return
            }
        }
        "not ready after 300 s (log $log)"
    }
}
