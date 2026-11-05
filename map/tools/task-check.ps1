# The two scheduled tasks of the application: are they there, are they
# enabled, can this account change them? (5.2.1)
#
# Why: on 3 Nov 2026 a colleague updated without administrator rights over an
# installation made with them. The tasks of that earlier installation had been
# registered from the administrator's window, so they belonged to the
# administrators: this account could read them but neither replace, disable nor
# enable them ("Access is denied"). They had been left disabled, autostart.bat
# still said "Done", and the application did not come back by itself.
#
#   -Probe   at the start of setup.bat, before anything is changed: for each
#            task that exists, try "/enable" (changes nothing on an enabled task;
#            turns a disabled one back on, which is what is wanted anyway). A
#            refusal means an administrator's task. Exit 3 when one is.
#   -Verify  at the end of autostart.bat: both tasks exist and are enabled.
#            A task found disabled is enabled when this account may; exit 4
#            when one is missing or stays disabled.
#
# Never deletes or rewrites a task. Lines starting with "TASK" are for the
# calling script; the rest is for the person.
param([switch]$Probe, [switch]$Verify)
$ErrorActionPreference = 'Continue'
$names = @('NER Map Analyzer', 'NER Map Analyzer watchdog')

function Read-Task([string]$n) {
  $raw = (schtasks.exe /Query /TN $n /XML 2>$null | Out-String)
  if (-not $raw.Trim()) { return $null }
  # The XML declares UTF-16; as a .NET string the declaration only gets in the way.
  $raw = $raw -replace '<\?xml[^>]*\?>', ''
  try { $x = [xml]$raw } catch { return @{ enabled = $true; command = '' } }
  $en = $true
  if ($x.Task.Settings.Enabled -and $x.Task.Settings.Enabled -eq 'false') { $en = $false }
  $cmd = ''
  try { $cmd = ($x.Task.Actions.Exec.Command + ' ' + $x.Task.Actions.Exec.Arguments) } catch { }
  return @{ enabled = $en; command = $cmd }
}

function Try-Enable([string]$n) {
  schtasks.exe /Change /TN $n /ENABLE *>$null
  return ($LASTEXITCODE -eq 0)
}

$locked = @(); $missing = @(); $off = @()
foreach ($n in $names) {
  $t = Read-Task $n
  if ($null -eq $t) {
    $missing += $n
    Write-Output ("TASK missing  {0}" -f $n)
    continue
  }
  $writable = Try-Enable $n
  $after = Read-Task $n
  $enabled = ($after -and $after.enabled)
  if (-not $writable) { $locked += $n }
  if (-not $enabled) { $off += $n }
  Write-Output ("TASK {0} {1} {2}" -f $(if ($writable) { 'own   ' } else { 'locked' }),
                $(if ($enabled) { 'enabled ' } else { 'DISABLED' }), $n)
}

if ($Probe) {
  if ($locked.Count -gt 0) { exit 3 }
  exit 0
}
if ($Verify) {
  if ($missing.Count -gt 0 -or $off.Count -gt 0) { exit 4 }
  exit 0
}
exit 0
