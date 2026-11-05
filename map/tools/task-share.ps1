# After an installation run WITH administrator rights (5.2.1): give the person
# signed in full control of the two scheduled tasks.
#
# Tasks registered from an administrator's window belong to the
# administrators; the person can only read them, and a later update without
# the rights can neither replace nor re-enable them (3 Nov 2026). setup.bat
# checks that this window runs as the person signed in, so the account running
# this is that person: an ACE for its own SID is added to each task's DACL.
# Best effort: on failure it says so and the installation goes on.
$ErrorActionPreference = 'Stop'
$names = @('NER Map Analyzer', 'NER Map Analyzer watchdog')
try {
  $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  $svc = New-Object -ComObject 'Schedule.Service'
  $svc.Connect()
  $root = $svc.GetFolder('\')
} catch {
  Write-Output "  [*] Could not reach the Task Scheduler to share the tasks: $($_.Exception.Message)"
  exit 0
}
foreach ($n in $names) {
  try {
    $t = $root.GetTask($n)
    $sd = $t.GetSecurityDescriptor(4)          # DACL_SECURITY_INFORMATION
    $ace = "(A;;FA;;;$sid)"
    if ($sd.IndexOf($ace, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
      $t.SetSecurityDescriptor($sd + $ace, 0)
      Write-Output "        ${n}: this account can now update it without administrator rights."
    }
  } catch {
    Write-Output "  [*] ${n}: could not be shared with this account ($($_.Exception.Message)). A later"
    Write-Output "      update without administrator rights will not be able to change it."
  }
}
exit 0
