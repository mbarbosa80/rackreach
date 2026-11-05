# Register (or re-register) the watchdog as a scheduled task for this user.
#
# Register-ScheduledTask answers "Access is denied" on this managed machine, so
# this uses schtasks.exe, which the same user is allowed to run. No admin needed.
# ErrorActionPreference stays 'Continue': schtasks writes ordinary notices to
# stderr, and 'Stop' would turn those into failures.
#
# The task runs wscript.exe, not powershell.exe. `powershell -WindowStyle Hidden`
# still flashes a console window every time it fires -- Windows creates the
# console and the switch only hides it afterwards -- and a black box blinking on
# screen every two minutes is not something to inflict on the person working.
# The .vbs wrapper starts it with window style 0 and no console is ever made.
$ErrorActionPreference = 'Continue'
$name = 'NER Map Analyzer watchdog'
$vbs  = Join-Path $PSScriptRoot 'watchdog-hidden.vbs'
$cmd  = 'wscript.exe //B //Nologo "{0}"' -f $vbs

schtasks.exe /Delete /TN $name /F *>$null

# /SC MINUTE /MO 2 -> every two minutes, indefinitely, starting now.
$out = schtasks.exe /Create /TN $name /TR $cmd /SC MINUTE /MO 2 /F 2>&1
$code = $LASTEXITCODE
if ($code -ne 0) {
  # 5.1.2: a watchdog registered by an installation run as administrator is
  # owned by the administrators - the person only has read on it - so an
  # update without the rights can neither delete it nor write over it
  # ("Access is denied", Barbosa-Laptop, 31 Oct 2026; the 4.5.0 install had
  # been accepted at the UAC prompt). That task still runs, as the person,
  # every two minutes. When it already runs this installation's watchdog,
  # nothing needs replacing: keep it and say so.
  $have = (schtasks.exe /Query /TN $name /XML 2>$null | Out-String)
  if ($have -and $have.IndexOf($vbs, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
    Write-Output "  [*] The watchdog task was registered by an administrator and cannot be"
    Write-Output "      replaced without the rights. It already runs this installation's"
    Write-Output "      watchdog, so it is kept as it is."
    exit 0
  }
  $out | Write-Output
  Write-Output "FAILED: schtasks exit $code"
  exit $code
}
$out | Write-Output

schtasks.exe /Query /TN $name /FO LIST 2>$null | Select-String 'TaskName|Status|Next Run Time'
