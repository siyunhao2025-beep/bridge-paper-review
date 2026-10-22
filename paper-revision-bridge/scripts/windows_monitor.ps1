[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Install', 'Status', 'Remove')]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [string]$Workspace,

    [ValidateRange(1, 1440)]
    [int]$IntervalMinutes = 5,

    [string]$TaskName,

    [ValidateSet('yes')]
    [string]$ConfirmedByUser,

    [switch]$WakeVerified,

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Get-WorkspaceState {
    param([string]$WorkspacePath)

    $resolved = (Resolve-Path -LiteralPath $WorkspacePath).Path
    $statePath = Join-Path $resolved 'bridge-state.json'
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        throw "Workspace does not contain bridge-state.json: $resolved"
    }
    $state = Get-Content -Raw -Encoding utf8 -LiteralPath $statePath | ConvertFrom-Json
    return @{
        Path = $resolved
        State = $state
    }
}

function Get-TaskOrNull {
    param([string]$Name)

    return Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
}

$workspaceInfo = Get-WorkspaceState -WorkspacePath $Workspace
$resolvedWorkspace = $workspaceInfo.Path
$state = $workspaceInfo.State
$bridgePath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'bridge.py')).Path

if ([string]::IsNullOrWhiteSpace($TaskName)) {
    $workflowShort = ([string]$state.workflow_id).Replace('-', '').Substring(0, 12)
    $TaskName = "BridgePaperReview-$workflowShort"
}
if ($TaskName -notmatch '^[A-Za-z0-9_.-]{1,120}$') {
    throw 'TaskName may contain only letters, digits, dot, underscore, and hyphen.'
}

if ($Action -in @('Install', 'Remove') -and $ConfirmedByUser -ne 'yes') {
    throw 'Install/Remove requires -ConfirmedByUser yes after explicit user confirmation.'
}

if ($Action -eq 'Status') {
    $task = Get-TaskOrNull -Name $TaskName
    [pscustomobject]@{
        task_name = $TaskName
        exists = $null -ne $task
        task_state = if ($task) { [string]$task.State } else { $null }
        workspace = $resolvedWorkspace
        monitor_status = [string]$state.monitor.status
        scheduler_id = [string]$state.monitor.scheduler_id
    } | ConvertTo-Json -Depth 4
    exit 0
}

if ($Action -eq 'Install') {
    if ([string]$state.monitor.mode -ne 'scheduled') {
        throw 'The workflow was not initialized with monitor-mode=scheduled.'
    }
    if ([string]$state.monitor.method -ne 'windows_task_scheduler') {
        throw 'The workflow monitor method is not windows_task_scheduler.'
    }
    if ([string]$state.monitor.scheduler_authorized -ne 'yes') {
        throw 'bridge-state.json does not record scheduler authorization=yes.'
    }
    if ([int]$state.monitor.interval_minutes -ne $IntervalMinutes) {
        throw "IntervalMinutes does not match the user-confirmed monitor configuration: $($state.monitor.interval_minutes)"
    }
    if (Get-TaskOrNull -Name $TaskName) {
        throw "Scheduled task already exists: $TaskName. Inspect or remove it first; this script will not overwrite it."
    }

    $pythonLauncher = (Get-Command py.exe -ErrorAction Stop).Source
    $taskArguments = '-3 "{0}" scan --workspace "{1}"' -f $bridgePath, $resolvedWorkspace
    $preview = [pscustomobject]@{
        action = 'Install'
        task_name = $TaskName
        interval_minutes = $IntervalMinutes
        executable = $pythonLauncher
        arguments = $taskArguments
        workspace = $resolvedWorkspace
        dry_run = [bool]$DryRun
    }
    if ($DryRun) {
        $preview | ConvertTo-Json -Depth 4
        exit 0
    }

    if ($PSCmdlet.ShouldProcess($TaskName, 'Register current-user manuscript folder monitor')) {
        $scheduledAction = New-ScheduledTaskAction `
            -Execute $pythonLauncher `
            -Argument $taskArguments `
            -WorkingDirectory $PSScriptRoot
        $trigger = New-ScheduledTaskTrigger `
            -Once `
            -At (Get-Date).AddMinutes(1) `
            -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
        $settings = New-ScheduledTaskSettingsSet `
            -StartWhenAvailable `
            -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
        $principal = New-ScheduledTaskPrincipal `
            -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
            -LogonType Interactive `
            -RunLevel Limited

        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $scheduledAction `
            -Trigger $trigger `
            -Settings $settings `
            -Principal $principal | Out-Null

        try {
            $activateArgs = @(
                '-3', $bridgePath, 'monitor-activate',
                '--workspace', $resolvedWorkspace,
                '--scheduler-id', "windows-task:$TaskName",
                '--authorization-confirmed', 'yes',
                '--note', "Windows Task Scheduler every $IntervalMinutes minute(s)"
            )
            if ($WakeVerified) {
                $activateArgs += '--wake-verified'
            }
            & $pythonLauncher @activateArgs
            if ($LASTEXITCODE -ne 0) {
                throw "bridge.py monitor-activate failed with exit code $LASTEXITCODE"
            }
        }
        catch {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
            throw
        }
    }
    exit 0
}

if ($Action -eq 'Remove') {
    $task = Get-TaskOrNull -Name $TaskName
    if (-not $task) {
        throw "Scheduled task does not exist: $TaskName"
    }
    if ($DryRun) {
        [pscustomobject]@{
            action = 'Remove'
            task_name = $TaskName
            workspace = $resolvedWorkspace
            dry_run = $true
        } | ConvertTo-Json -Depth 4
        exit 0
    }

    if ($PSCmdlet.ShouldProcess($TaskName, 'Remove manuscript folder monitor')) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        $pythonLauncher = (Get-Command py.exe -ErrorAction Stop).Source
        & $pythonLauncher `
            -3 $bridgePath monitor-deactivate `
            --workspace $resolvedWorkspace `
            --confirmed yes `
            --note "Windows scheduled task removed: $TaskName"
        if ($LASTEXITCODE -ne 0) {
            throw "Task was removed, but bridge.py monitor-deactivate failed with exit code $LASTEXITCODE"
        }
    }
}
