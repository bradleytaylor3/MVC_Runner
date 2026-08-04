#Requires -Version 5.1
# Registers the MVC_Runner Claude Code skills (code-edit, adb-test) globally
# by linking them into ~/.claude/skills/, and points MVC_RUNNER_HOME at this
# checkout so the skills can find work_docs/, logs/, etc. from any project.

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$skillsDir = Join-Path $env:USERPROFILE ".claude\skills"

New-Item -ItemType Directory -Force -Path $skillsDir | Out-Null

foreach ($skill in @("code-edit", "adb-test")) {
    $linkPath = Join-Path $skillsDir $skill
    $target = Join-Path $repoRoot ".claude\skills\$skill"

    if (Test-Path $linkPath) {
        $existing = Get-Item $linkPath
        if ($existing.LinkType -eq "Junction" -and $existing.Target -contains $target) {
            Write-Host "Skipping $skill (already linked to this repo)"
        } else {
            Write-Warning "$linkPath already exists and is not a link to this repo -- remove it manually and re-run if you want it replaced."
        }
        continue
    }

    New-Item -ItemType Junction -Path $linkPath -Target $target | Out-Null
    Write-Host "Linked $skill -> $target"
}

[Environment]::SetEnvironmentVariable("MVC_RUNNER_HOME", $repoRoot, "User")
Write-Host "Set MVC_RUNNER_HOME=$repoRoot (User scope)."
Write-Host "Restart your terminal / Claude Code session for the skills and env var to take effect."
