param(
    [Parameter(Mandatory=$true)][string]$RequestPath,
    [Parameter(Mandatory=$true)][string]$OutputPath
)

$ErrorActionPreference = 'Stop'
$request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$excelApp = $null
$book = $null
$sheet = $null

function Get-GridHash($grid) {
    $stream = New-Object System.IO.MemoryStream
    $writer = New-Object System.IO.BinaryWriter($stream)
    try {
        foreach ($value in $grid) {
            if ($null -eq $value) { $writer.Write([byte]0) }
            elseif ($value -is [string]) { $writer.Write([byte]1); $writer.Write([string]$value) }
            elseif ($value -is [bool]) { $writer.Write([byte]2); $writer.Write([bool]$value) }
            else { $writer.Write([byte]3); $writer.Write([double]$value) }
        }
        $writer.Flush()
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { return ([BitConverter]::ToString($hasher.ComputeHash($stream.ToArray()))).Replace('-','').ToLowerInvariant() }
        finally { $hasher.Dispose() }
    } finally { $writer.Dispose(); $stream.Dispose() }
}

function Read-Number([string]$address) {
    $value = $sheet.Range($address).Value2
    # Excel errors are integer variants, numeric cell values are doubles.
    if ($null -eq $value -or $value -isnot [double] -or [double]::IsNaN($value) -or [double]::IsInfinity($value)) {
        throw "Invalid or error-valued numeric output at $address : $value"
    }
    return [double]$value
}

function Get-Metrics {
    $flows = $sheet.Range('G19:G352').Value2
    $values = @($flows | ForEach-Object {
        if ($_ -isnot [double] -or [double]::IsNaN($_) -or [double]::IsInfinity($_)) { throw 'Invalid cash flow' }
        [double]$_
    })
    return [ordered]@{
        base_npv = Read-Number 'K353'
        stress_npv = Read-Number 'M353'
        dnpv = Read-Number 'N353'
        regular_payment = Read-Number 'B14'
        final_balance = Read-Number 'E352'
        cashflows = $values
    }
}

function Assert-MetricsEqual($actual, $expected) {
    foreach ($key in @('base_npv','stress_npv','dnpv','regular_payment','final_balance')) {
        if ([math]::Abs($actual[$key] - $expected[$key]) -gt 0.000001) { throw "Baseline restoration failed: $key" }
    }
    if ($actual.cashflows.Count -ne $expected.cashflows.Count) { throw 'Restored cashflow count changed' }
    for ($i=0; $i -lt $actual.cashflows.Count; $i++) {
        if ([math]::Abs($actual.cashflows[$i]-$expected.cashflows[$i]) -gt 0.000001) { throw "Restored cashflow changed: $i" }
    }
}

try {
    if ((Get-FileHash -LiteralPath $request.source_copy -Algorithm SHA256).Hash.ToLowerInvariant() -ne $request.contract.source_sha256) {
        throw 'Temporary source copy hash does not match the input contract'
    }
    $excelApp = New-Object -ComObject Excel.Application
    $excelApp.Visible = $false
    $excelApp.DisplayAlerts = $false
    $excelApp.EnableEvents = $false
    $excelApp.AutomationSecurity = 3
    $book = $excelApp.Workbooks.Open($request.source_copy, 0, $true)
    $excelApp.Calculation = -4135 # manual, private Excel instance only
    $sheet = $book.Worksheets.Item($request.contract.sheet)
    $excelApp.CalculateFullRebuild()
    $initialGrid = $sheet.Range($request.contract.used_range).Formula
    $baselineHash = Get-GridHash $initialGrid
    $baseline = Get-Metrics
    $rows = @()
    foreach ($experiment in $request.plan) {
        if ((Get-GridHash ($sheet.Range($request.contract.used_range).Formula)) -ne $baselineHash) {
            throw "Experiment $($experiment.experiment_id) did not start from the same baseline"
        }
        Assert-MetricsEqual (Get-Metrics) $baseline
        $changedCell = $null
        $oldFormula = $null
        try {
            if ($null -ne $experiment.cell) {
                $changedCell = $sheet.Range($experiment.cell)
                if ($changedCell.HasFormula) { throw "Experiment input is a formula: $($experiment.cell)" }
                $oldFormula = $changedCell.Formula
                if ([math]::Abs([double]$changedCell.Value2 - $experiment.old_value) -gt 1e-12) { throw 'Unexpected original input' }
                $changedCell.Value2 = [double]$experiment.new_value
            }
            $excelApp.CalculateFullRebuild()
            $trialGrid = $sheet.Range($request.contract.used_range).Formula
            $diffs = @()
            for ($r=$initialGrid.GetLowerBound(0); $r -le $initialGrid.GetUpperBound(0); $r++) {
                for ($c=$initialGrid.GetLowerBound(1); $c -le $initialGrid.GetUpperBound(1); $c++) {
                    if (-not [object]::Equals($initialGrid[$r,$c],$trialGrid[$r,$c])) {
                        $diffs += $sheet.Cells.Item($r,$c).Address($false,$false)
                    }
                }
            }
            $expectedDiffs = if ($null -eq $experiment.cell -or $experiment.old_value -eq $experiment.new_value) { 0 } else { 1 }
            if ($diffs.Count -ne $expectedDiffs -or ($diffs.Count -eq 1 -and $diffs[0] -ne $experiment.cell)) {
                throw "OAT violation: changed formula/input cells: $($diffs -join ',')"
            }
            $metrics = Get-Metrics
        } finally {
            if ($null -ne $changedCell) { $changedCell.Formula = $oldFormula }
            $excelApp.CalculateFullRebuild()
            if ((Get-GridHash ($sheet.Range($request.contract.used_range).Formula)) -ne $baselineHash) { throw 'Restoration of formulas/inputs failed' }
            Assert-MetricsEqual (Get-Metrics) $baseline
        }
        $rows += [ordered]@{experiment_id=$experiment.experiment_id;metrics=$metrics;
            changed_cells=$diffs;baseline_grid_sha256=$baselineHash;restored_grid_sha256=$baselineHash;
            baseline_before_verified=$true;restored_after_verified=$true}
        Write-Host "Captured $($experiment.experiment_id)"
    }
    $result = [ordered]@{schema_version=1;engine='Microsoft Excel COM CalculateFullRebuild';excel_version=$excelApp.Version;
        source_sha256=$request.contract.source_sha256;baseline=$baseline;experiments=$rows}
    $result | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
} finally {
    if ($null -ne $book) { $book.Close($false) }
    if ($null -ne $sheet) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($sheet) }
    if ($null -ne $book) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($book) }
    if ($null -ne $excelApp) { $excelApp.Quit(); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($excelApp) }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
