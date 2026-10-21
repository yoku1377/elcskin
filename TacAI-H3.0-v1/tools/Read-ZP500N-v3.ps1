#Requires -Version 5.1
param(
    [string]$Port = 'COM13',
    [ValidateSet(2400)][int]$Baud = 2400,
    [ValidateRange(1, 60)][int]$Seconds = 15,
    [switch]$StartRawOutput
)

# Raw diagnostic only: never infer a unit or force value from a Read boundary.
# FA and optional 25 are the only writes; no retries or instrument controls.
# The supplied vendor application waits 60 ms after data arrives, then reads
# at most 24 bytes. Such a batch is an observation, not a proven wire frame.
$ErrorActionPreference = 'Stop'
$gaugeLog = Join-Path ([System.IO.Path]::GetTempPath()) ('ZP500N_Raw_v3_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.log')
$gaugeEncoding = [System.Text.UTF8Encoding]::new($false)
$gaugeClock = [System.Diagnostics.Stopwatch]::new()
$gaugeWrites = [System.Collections.Generic.List[string]]::new()

function Write-GaugeLog([string]$Message) {
    Write-Host $Message
    [System.IO.File]::AppendAllText($gaugeLog, $Message + [Environment]::NewLine, $gaugeEncoding)
}

function Format-GaugeBytes([byte[]]$Bytes) {
    $gaugeText = [System.Text.Encoding]::ASCII.GetString($Bytes).Replace("`r", '\r').Replace("`n", '\n')
    return ('HEX=' + [System.BitConverter]::ToString($Bytes) + ' TXT=' + $gaugeText)
}

function Read-GaugeWindow {
    if ($gaugePort.BytesToRead -le 0) { return }
    # Do not read individual bytes before this window has elapsed.
    Start-Sleep -Milliseconds 60
    $gaugeAvailable = $gaugePort.BytesToRead
    if ($gaugeAvailable -le 0) { return }
    $gaugeBuffer = New-Object byte[] ([Math]::Min(24, $gaugeAvailable))
    $gaugeRead = $gaugePort.Read($gaugeBuffer, 0, $gaugeBuffer.Length)
    if ($gaugeRead -gt 0) {
        if ($gaugeRead -ne $gaugeBuffer.Length) { $gaugeBuffer = [byte[]]$gaugeBuffer[0..($gaugeRead - 1)] }
        Write-Output -NoEnumerate $gaugeBuffer
    }
}

function Receive-GaugePhase([string]$Phase, [double]$Duration) {
    $gaugePhaseBytes = [System.Collections.Generic.List[byte]]::new()
    $gaugePhaseEnd = $gaugeClock.Elapsed.TotalSeconds + $Duration
    Write-GaugeLog ('PHASE=' + $Phase + ' START_MS=' + $gaugeClock.Elapsed.TotalMilliseconds.ToString('F1', [System.Globalization.CultureInfo]::InvariantCulture))
    while ($gaugeClock.Elapsed.TotalSeconds -lt $gaugePhaseEnd) {
        [byte[]]$gaugeChunk = Read-GaugeWindow
        # Timestamp the completed Read before formatting or writing its log.
        $gaugeReadMs = $gaugeClock.Elapsed.TotalMilliseconds
        if ($null -ne $gaugeChunk -and $gaugeChunk.Length -gt 0) {
            $gaugePhaseBytes.AddRange($gaugeChunk)
            Write-GaugeLog ('RX_CHUNK t_ms=' + $gaugeReadMs.ToString('F1', [System.Globalization.CultureInfo]::InvariantCulture) + ' phase=' + $Phase + ' len=' + $gaugeChunk.Length + ' ' + (Format-GaugeBytes $gaugeChunk))
        } else {
            Start-Sleep -Milliseconds 2
        }
    }
    Write-GaugeLog ($Phase + '_BYTES=' + $gaugePhaseBytes.Count)
    if ($Phase -eq 'AFTER_FA' -and $gaugePhaseBytes.Count -gt 0) {
        # This is the complete raw phase, NOT an asserted identity frame.
        Write-GaugeLog ('AFTER_FA_RAW ' + (Format-GaugeBytes ($gaugePhaseBytes.ToArray())))
    }
}

function Send-GaugeReadRequest([byte]$Command) {
    if ($Command -ne 0xFA -and $Command -ne 0x25) { throw 'Read-command whitelist violation.' }
    $gaugeTxMs = $gaugeClock.Elapsed.TotalMilliseconds
    $gaugePort.Write([byte[]]@($Command), 0, 1)
    $gaugeHex = $Command.ToString('X2')
    $gaugeWrites.Add($gaugeHex)
    Write-GaugeLog ('TX t_ms=' + $gaugeTxMs.ToString('F1', [System.Globalization.CultureInfo]::InvariantCulture) + ' HEX=' + $gaugeHex)
}

$gaugePort = [System.IO.Ports.SerialPort]::new($Port, $Baud, [System.IO.Ports.Parity]::None, 8, [System.IO.Ports.StopBits]::One)
$gaugePort.ReadTimeout = 100
$gaugePort.WriteTimeout = 1000
$gaugePort.Handshake = [System.IO.Ports.Handshake]::None
$gaugePort.DtrEnable = $false
$gaugePort.RtsEnable = $false
try {
    Write-GaugeLog ('ZP-500N raw probe v3: ' + $Port + ', 2400, 8N1; NO unit conversion or force parsing.')
    Write-GaugeLog ('LOG=' + $gaugeLog)
    $gaugePort.Open()
    $gaugeClock.Start()
    Start-Sleep -Milliseconds 50
    Receive-GaugePhase 'PASSIVE' 1.0
    Send-GaugeReadRequest 0xFA
    Receive-GaugePhase 'AFTER_FA' 1.2
    if ($StartRawOutput) {
        # Explicit raw-only read request. No identity/units are assumed valid.
        Send-GaugeReadRequest 0x25
        Receive-GaugePhase 'AFTER_25' $Seconds
    } else {
        Write-GaugeLog 'TX_25=SKIPPED; use -StartRawOutput to request raw measurement output.'
    }
    Write-GaugeLog 'UNIT=UNCONFIRMED; Read batches are not accepted force samples.'
} catch {
    Write-GaugeLog ('ERROR: ' + $_.Exception.Message)
} finally {
    try {
        if ($gaugePort.IsOpen) { $gaugePort.Close() }
    } finally {
        $gaugePort.Dispose()
    }
    Write-GaugeLog ('COMMANDS_SENT=' + ($gaugeWrites -join ','))
    Write-Host ('Finished; port released. LOG=' + $gaugeLog)
}
