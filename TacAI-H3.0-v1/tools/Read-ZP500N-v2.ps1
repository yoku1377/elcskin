#Requires -Version 5.1
param(
    [string]$Port = 'COM13',
    [int]$Baud = 2400,
    [ValidateRange(1, 60)][int]$Seconds = 10
)

# Derived by static inspection of the supplied vendor application's IL.
# FA requests instrument information; 25 starts measurement output.
# No zero, unit, peak, power, or configuration command is sent.
$ErrorActionPreference = 'Stop'
$gaugeLog = Join-Path ([System.IO.Path]::GetTempPath()) ('ZP500N_Read_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.log')
$gaugeEncoding = [System.Text.UTF8Encoding]::new($false)
function Write-GaugeLog([string]$Message) {
    Write-Host $Message
    [System.IO.File]::AppendAllText($gaugeLog, $Message + [Environment]::NewLine, $gaugeEncoding)
}
function Format-GaugeBytes([byte[]]$Bytes) {
    $gaugeText = [System.Text.Encoding]::ASCII.GetString($Bytes).Replace("`r", '\r').Replace("`n", '\n')
    return ('HEX=' + [System.BitConverter]::ToString($Bytes) + ' TXT=' + $gaugeText)
}
function Read-GaugeAvailable {
    $gaugeAvailable = $gaugePort.BytesToRead
    if ($gaugeAvailable -le 0) { return }
    $gaugeBuffer = New-Object byte[] ([Math]::Min(4096, $gaugeAvailable))
    $gaugeRead = $gaugePort.Read($gaugeBuffer, 0, $gaugeBuffer.Length)
    if ($gaugeRead -gt 0) {
        if ($gaugeRead -ne $gaugeBuffer.Length) { $gaugeBuffer = [byte[]]$gaugeBuffer[0..($gaugeRead - 1)] }
        Write-Output -NoEnumerate $gaugeBuffer
    }
}

$gaugePort = [System.IO.Ports.SerialPort]::new($Port, $Baud, [System.IO.Ports.Parity]::None, 8, [System.IO.Ports.StopBits]::One)
$gaugePort.ReadTimeout = 100
$gaugePort.WriteTimeout = 1000
$gaugePort.Handshake = [System.IO.Ports.Handshake]::None
$gaugePort.DtrEnable = $false
$gaugePort.RtsEnable = $false
try {
    Write-GaugeLog 'ZP-500N command-line probe v2; raw receive capture, no force calibration.'
    Write-GaugeLog ('ZP-500N: ' + $Port + ', ' + $Baud + ', 8N1; read requests FA -> 25 only.')
    Write-GaugeLog ('LOG=' + $gaugeLog)
    $gaugePort.Open()
    # The original application starts a 50 ms timer after Open(), then sends FA.
    Start-Sleep -Milliseconds 50
    $gaugePort.Write([byte[]]@(0xFA), 0, 1)
    Write-GaugeLog 'TX FA (instrument information)'
    $gaugeMetadata = [System.Collections.Generic.List[byte]]::new()
    $gaugeClock = [System.Diagnostics.Stopwatch]::StartNew()
    $gaugeLastInput = 0.0
    $gaugeInfoReady = $false
    while ($gaugeClock.Elapsed.TotalSeconds -lt 3.0) {
        [byte[]]$gaugeChunk = Read-GaugeAvailable
        if ($null -ne $gaugeChunk -and $gaugeChunk.Length -gt 0) {
            $gaugeMetadata.AddRange($gaugeChunk)
            $gaugeLastInput = $gaugeClock.Elapsed.TotalSeconds
            Write-GaugeLog ('INFO_CHUNK ' + (Format-GaugeBytes $gaugeChunk))
        }
        if ($gaugeMetadata.Count -gt 48) { break }
        if (($null -eq $gaugeChunk -or $gaugeChunk.Length -eq 0) -and $gaugeMetadata.Count -ge 32 -and ($gaugeClock.Elapsed.TotalSeconds - $gaugeLastInput) -ge 0.060) {
            $gaugeInfoReady = $true
            break
        }
        Start-Sleep -Milliseconds 10
    }
    Write-GaugeLog ('INFO_BYTES=' + $gaugeMetadata.Count)
    if (-not $gaugeInfoReady -or $gaugeMetadata.Count -lt 32 -or $gaugeMetadata.Count -gt 48) {
        throw 'Expected 32..48 information bytes followed by 60 ms idle before timeout. Measurement output was not started.'
    }
    [byte[]]$gaugeInfo = $gaugeMetadata.ToArray()
    Write-GaugeLog ('INFO_FULL ' + (Format-GaugeBytes $gaugeInfo))
    $gaugeUnitCode = [char]$gaugeInfo[10]
    $gaugeUnit = switch ($gaugeUnitCode) { '1' {'kgf'} '4' {'kgf'} '2' {'lbf'} '5' {'lbf'} '3' {'N'} '6' {'N'} default { 'UNKNOWN' } }
    Write-GaugeLog ('UNIT_CODE=' + $gaugeUnitCode + '; UNIT=' + $gaugeUnit)
    if ($gaugeUnit -eq 'UNKNOWN') { throw 'Unknown information unit code. Measurement output was not started.' }
    $gaugePort.Write([byte[]]@(0x25), 0, 1)
    Write-GaugeLog 'TX 25 (measurement output request)'
    $gaugeClock.Restart()
    $gaugeTotal = 0
    while ($gaugeClock.Elapsed.TotalSeconds -lt $Seconds) {
        [byte[]]$gaugeChunk = Read-GaugeAvailable
        if ($null -ne $gaugeChunk -and $gaugeChunk.Length -gt 0) {
            $gaugeTotal += $gaugeChunk.Length
            Write-GaugeLog ('RX_CHUNK t=' + $gaugeClock.Elapsed.TotalSeconds.ToString('F3', [System.Globalization.CultureInfo]::InvariantCulture) + ' ' + (Format-GaugeBytes $gaugeChunk))
        }
        Start-Sleep -Milliseconds 10
    }
    Write-GaugeLog ('MEASUREMENT_RX_BYTES=' + $gaugeTotal)
} catch {
    Write-GaugeLog ('ERROR: ' + $_.Exception.Message)
} finally {
    if ($gaugePort.IsOpen) { $gaugePort.Close() }
    $gaugePort.Dispose()
    Write-Host ('Finished; port released. LOG=' + $gaugeLog)
}
