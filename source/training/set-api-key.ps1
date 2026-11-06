# Saves your Anthropic API key for your Windows account (ANTHROPIC_API_KEY), for `agentic run`.
# Opens a normal window to paste into; the key is never printed.
#   .\training\set-api-key.ps1

Add-Type -AssemblyName Microsoft.VisualBasic
$key = [Microsoft.VisualBasic.Interaction]::InputBox(
    "Paste your Anthropic API key (it starts with sk-ant-) and click OK.", "Anthropic API key", "")
$key = "$key".Trim()
if (-not $key) { "Nothing pasted: no key saved."; exit 1 }
if (-not $key.StartsWith("sk-ant-")) {
    "That doesn't look like an API key (it should start with sk-ant-; this starts with '$($key.Substring(0, [Math]::Min(7, $key.Length)))'). Nothing saved."
    "Copy the key again from console.anthropic.com > API keys, then rerun this."
    exit 1
}
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", $key, "User")
$env:ANTHROPIC_API_KEY = $key
$old = Join-Path $env:LOCALAPPDATA "vertex-training\key.txt"
if (Test-Path $old) { Remove-Item $old }
"Saved ($($key.Length) characters). This terminal can use it now; restart VS Code for other terminals."
