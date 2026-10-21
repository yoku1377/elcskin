param(
    [string]$JLinkExe = 'C:\Program Files\SEGGER\JLink\JLink.exe',
    [string]$DllPath = 'F:\SPI测试\H3_SPI_GPIO_Witness_v3_20261016\CH347DLLA64.dll',
    [switch]$SelfTest
)
$ErrorActionPreference = 'Stop'
if (-not [Environment]::Is64BitProcess) { throw 'Run 64-bit Windows PowerShell.' }
$expectedHash = '00AE967AC6A21253CC53E32085423807C7817198D9328B17B936ED8CA075F375'
if ($SelfTest) { $nativePath = 'not-loaded-in-self-test.dll' }
else {
    $nativePath = (Resolve-Path -LiteralPath $DllPath).Path
    $JLinkExe = (Resolve-Path -LiteralPath $JLinkExe).Path
    if ((Get-FileHash -LiteralPath $nativePath -Algorithm SHA256).Hash -ne $expectedHash) { throw 'CH347 DLL hash mismatch.' }
}
$source = @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
namespace H3CliV1 {
 [StructLayout(LayoutKind.Sequential, Pack=1)]
 public struct Config {
  public byte Mode, Clock, ByteOrder;
  public ushort Interval;
  public byte DefaultData;
  public uint ChipSelect;
  public byte CS1Polarity, CS2Polarity;
  public ushort AutoDeactivate, ActiveDelay;
  public uint DeactivateDelay;
 }
 public interface ITransport { string Open(); void Send(byte[] data); void Close(); }
 public class NativeTransport : ITransport {
  const string Dll = @"__DLL__";
  [DllImport(Dll, CallingConvention=CallingConvention.StdCall)] static extern IntPtr CH347OpenDevice(uint i);
  [DllImport(Dll, CallingConvention=CallingConvention.StdCall)] static extern int CH347CloseDevice(uint i);
  [DllImport(Dll, CallingConvention=CallingConvention.StdCall)] static extern int CH347GetDeviceInfor(uint i, [Out] byte[] data);
  [DllImport(Dll, CallingConvention=CallingConvention.StdCall)] static extern int CH347SetTimeout(uint i,uint w,uint r);
  [DllImport(Dll, CallingConvention=CallingConvention.StdCall)] static extern int CH347SPI_SetDataBits(uint i,byte bits);
  [DllImport(Dll, CallingConvention=CallingConvention.StdCall)] static extern int CH347SPI_Init(uint i,ref Config cfg);
  [DllImport(Dll, CallingConvention=CallingConvention.StdCall)] static extern int CH347SPI_GetCfg(uint i,ref Config cfg);
  [DllImport(Dll, CallingConvention=CallingConvention.StdCall)] static extern int CH347SPI_ChangeCS(uint i,byte level);
  [DllImport(Dll, CallingConvention=CallingConvention.StdCall)] static extern int CH347SPI_WriteRead(uint i,uint cs,uint length,[In,Out] byte[] data);
  uint index; bool opened, configured;
  static bool Valid(IntPtr h) { return h!=IntPtr.Zero && h!=new IntPtr(-1); }
  static void Check(int ok,string name) { if(ok==0) throw new Exception(name+" failed"); }
  public string Open() {
   if(Marshal.SizeOf(typeof(Config))!=20) throw new Exception("SPIConfig must be 20 bytes");
   var matches=new List<uint>();
   for(uint i=0;i<32;i++) {
    if(!Valid(CH347OpenDevice(i))) continue;
    try {
     byte[] info=new byte[4096]; Check(CH347GetDeviceInfor(i,info),"GetDeviceInfor");
     string id=Encoding.ASCII.GetString(info,263,64).TrimEnd('\0');
     if(info[262]==3 && info[327]==0 && info[341]==4 && id.ToUpperInvariant().Contains("PID_55DE")) matches.Add(i);
    } finally { Check(CH347CloseDevice(i),"Close enumerated interface"); }
   }
   if(matches.Count!=1) throw new Exception("Expected exactly one CH347F Mode0 IF4 SPI interface");
   index=matches[0];
   if(!Valid(CH347OpenDevice(index))) throw new Exception("Open selected SPI interface failed");
   opened=true;
   Check(CH347SetTimeout(index,1000,1000),"SetTimeout");
   Check(CH347SPI_SetDataBits(index,0),"SetDataBits");
   Config cfg=new Config(); cfg.Clock=7;cfg.ByteOrder=1;cfg.DefaultData=255;cfg.ChipSelect=128;
   Check(CH347SPI_Init(index,ref cfg),"SPI_Init");configured=true;
   Check(CH347SPI_ChangeCS(index,1),"CS high");
   Config got=new Config();Check(CH347SPI_GetCfg(index,ref got),"GetCfg");
   if(got.Mode!=0 || got.Clock!=7 || got.ByteOrder!=0 || got.ChipSelect!=128 ||
      got.CS1Polarity!=0 || got.CS2Polarity!=0 || got.AutoDeactivate!=0)
    throw new Exception("SPI mode/clock/MSB/CS readback mismatch");
   return "index="+index+", Mode0, 468750Hz, MSB, CS0 high";
  }
  public void Send(byte[] data) { Check(CH347SPI_WriteRead(index,0,(uint)data.Length,data),"WriteRead CS unchanged"); }
  public void Close() {
   Exception error=null;
   if(opened) {
    try { if(configured) Check(CH347SPI_ChangeCS(index,1),"Final CS high"); } catch(Exception e) { error=e; }
    try { Check(CH347CloseDevice(index),"Close SPI"); } catch(Exception e) { if(error==null) error=e; }
    opened=false;
   }
   if(error!=null) throw error;
  }
 }
 public class Output {
  public readonly ManualResetEvent Ready=new ManualResetEvent(false);
  public volatile string Error;
  public string Metadata;
  public volatile int Transfers;
  public volatile bool RequestedStop, Closed, LimitReached;
  public long LastSendStart, LastSendEnd;
  readonly ITransport transport;readonly byte value;readonly Thread thread;
  public Output(byte v,ITransport t) {
   if(v!=0 && v!=255) throw new Exception("Only 00/FF allowed");
   value=v;transport=t;thread=new Thread(Run);thread.IsBackground=true;
  }
  public void Start() { thread.Start(); }
  public bool Stop(int milliseconds) { RequestedStop=true;return thread.Join(milliseconds); }
  void Run() {
   try {
    Metadata=transport.Open();var timer=Stopwatch.StartNew();
    while(!RequestedStop) {
     if(timer.ElapsedMilliseconds>=11000 || Transfers>=2048) { LimitReached=true;throw new Exception("Stimulus limit reached before batch completed"); }
     // WriteRead overwrites its input; always refill the complete buffer.
     byte[] data=new byte[536];for(int i=0;i<data.Length;i++) data[i]=value;
     LastSendStart=Stopwatch.GetTimestamp();transport.Send(data);LastSendEnd=Stopwatch.GetTimestamp();
     Transfers++;Ready.Set();
    }
   } catch(Exception e) { Error=e.Message; }
   finally {
    try { transport.Close();Closed=true; } catch(Exception e) { if(Error==null) Error=e.Message; }
    Ready.Set();
   }
  }
 }
 public class FakeTransport : ITransport {
  public bool Closed, FailSend;
  public int Calls;
  public byte? Expected;
  public string Open() { return "fake"; }
  public void Send(byte[] data) {
   if(data.Length!=536) throw new Exception("bad length");
   foreach(byte b in data) if(b!=Expected) throw new Exception("buffer not refilled");
   Calls++;for(int i=0;i<data.Length;i++) data[i]=85;
   Thread.Sleep(5);if(FailSend) throw new Exception("fake send failure");
  }
  public void Close() { Closed=true; }
 }
}
'@
$source = $source.Replace('__DLL__', $nativePath.Replace('"','""'))
Add-Type -TypeDefinition $source -Language CSharp

function Convert-BatchReply([string]$Text, [uint32[]]$Addresses, [int]$Code) {
    if ($Code -ne 0) { throw "Commander exit code $Code" }
    $lines = @($Text -split '\r?\n' | ForEach-Object { ($_ -replace '^\s*J-Link>\s*','').Trim() })
    foreach ($line in $lines) {
        if ($line -eq 'J-Link Commander will now exit on Error') { continue }
        if ($line -match '\b(ERROR|Unknown|Cannot|Failed)\b') { throw "Commander error: $line" }
    }
    if ($Text -notmatch 'Connecting to J-Link via USB[^\r\n]*O\.K\.') { throw 'Commander did not confirm USB connection.' }
    $running = @();$records = @()
    for ($i=0;$i -lt $lines.Count;$i++) {
        $line=$lines[$i]
        if ($line -eq 'CPU is not halted.') { $running += $i }
        elseif ($line -match '^CPU is .*halted') { throw "CPU state rejected: $line" }
        if ($line -match '^(?:0x)?[0-9A-Fa-f]{8}\s*=' -or $line -match '^(?:0x)?[0-9][0-9A-Za-z]*\s*=') {
            if ($line -notmatch '^(?:0x)?([0-9A-Fa-f]{8})\s*=\s*([0-9A-Fa-f]{8})$') { throw "Malformed memory value: $line" }
            $records += [pscustomobject]@{Line=$i;Address=[Convert]::ToUInt32($Matches[1],16);Value=[Convert]::ToUInt32($Matches[2],16)}
        }
    }
    if ($running.Count -ne 2 -or $records.Count -ne $Addresses.Count) { throw 'Incomplete or extra CPU/memory records.' }
    if ($running[0] -ge $records[0].Line -or $running[1] -le $records[-1].Line) { throw 'Memory reads must be between two running checks.' }
    for ($i=0;$i -lt $Addresses.Count;$i++) {
        if ($records[$i].Address -ne $Addresses[$i]) { throw 'Memory address/order mismatch.' }
    }
    return @($records | ForEach-Object {$_.Value})
}

if ($SelfTest) {
    if ([Runtime.InteropServices.Marshal]::SizeOf([type][H3CliV1.Config]) -ne 20) { throw 'ABI size' }
    foreach ($value in @(0,255)) {
        $fake=[H3CliV1.FakeTransport]::new();$fake.Expected=[byte]$value
        $out=[H3CliV1.Output]::new([byte]$value,$fake);$out.Start()
        if (-not $out.Ready.WaitOne(1000)) { throw 'fake ready timeout' }
        Start-Sleep -Milliseconds 25
        if (-not $out.Stop(1000) -or $out.Error -or -not $fake.Closed -or $fake.Calls -lt 2) { throw 'fake output/refill/close failed' }
    }
    $fake=[H3CliV1.FakeTransport]::new();$fake.Expected=0;$fake.FailSend=$true
    $out=[H3CliV1.Output]::new(0,$fake);$out.Start();[void]$out.Ready.WaitOne(1000)
    if (-not $out.Stop(1000) -or $out.Error -ne 'fake send failure' -or -not $fake.Closed) { throw 'send failure not preserved' }
    $fixture="Connecting to J-Link via USB...O.K.`nJ-Link Commander will now exit on Error`nS/N: -1`nCPU is not halted.`n50000010 = 00000110`n50000010 = 00000111`nCPU is not halted.`n"
    $values=@(Convert-BatchReply $fixture @(0x50000010,0x50000010) 0)
    if ($values.Count -ne 2 -or $values[0] -ne 272) { throw 'No-prompt parsing failed' }
    $bad=@($fixture+"ERROR: actual error`n",$fixture.Replace('00000111','0000011G'),$fixture.Replace('50000010 = 00000111','50000410 = 00000111'),$fixture.Replace('CPU is not halted.','CPU is halted.'),$fixture+"50000010 = 00000111`n",$fixture.Replace("50000010 = 00000111`n",''),$fixture+"5000001G = 00000001`n",$fixture+"5000010 = 00000001`n")
    foreach ($text in $bad) {
        $rejected=$false;try { $null=Convert-BatchReply $text @(0x50000010,0x50000010) 0 } catch { $rejected=$true }
        if (-not $rejected) { throw 'Invalid batch accepted' }
    }
    Write-Output 'PASS: C# ABI, 00/FF refill, worker cleanup/failure, no-prompt batch parsing and eight rejection cases. No hardware accessed.'
    return
}

$logDir = Join-Path $PSScriptRoot ('logs\'+(Get-Date -Format 'yyyyMMdd_HHmmss_fff')+'_commandline')
[void](New-Item -ItemType Directory -Path $logDir -Force)
$clock=[Diagnostics.Stopwatch]::StartNew()
$summary=[ordered]@{tool='spi_commandline';version=2;transfer_length=536;status='started';evidence_scope='GPIO during one constant 00/FF stimulus phase, including SDK/USB gaps';flash_operations=0;target_write_commands=0;target_reset_halt_commands=0;uart_commands_sent=0;phases=@();errors=@();dll_sha256=$expectedHash}
function Save-Summary { $summary | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $logDir 'summary.json') -Encoding UTF8 }
function Invoke-Batch([uint32[]]$Addresses,[string]$Name) {
    foreach ($address in $Addresses) { if ($address -notin @(0x50000000,0x50000010,0x50000020,0x50000024)) { throw 'Read address outside whitelist.' } }
    if ($Addresses.Count -lt 1 -or $Addresses.Count -gt 64) { throw 'Batch length rejected.' }
    $cmd=Join-Path $logDir ($Name+'.jlink')
    $commands=@('device STM32G031G8','si SWD','speed 1000','connect','IsHalted')
    $commands+=@($Addresses | ForEach-Object { 'mem32 0x{0:X8} 1' -f $_ })
    $commands+=@('IsHalted','exit')
    $commands | Set-Content -LiteralPath $cmd -Encoding ASCII
    $pi=[Diagnostics.ProcessStartInfo]::new();$pi.FileName=$JLinkExe
    $pi.Arguments='-AutoConnect 0 -NoGui 1 -ExitOnError 1 -CommandFile "'+$cmd+'"'
    $pi.WorkingDirectory=Split-Path -Parent $JLinkExe
    $pi.UseShellExecute=$false;$pi.CreateNoWindow=$true;$pi.RedirectStandardOutput=$true;$pi.RedirectStandardError=$true
    $p=[Diagnostics.Process]::new();$p.StartInfo=$pi;$started=$false;$raw='';$stderr='';$timedOut=$false
    $begin=$clock.Elapsed.TotalSeconds
    try {
        [void]$p.Start();$started=$true
        $stdoutTask=$p.StandardOutput.ReadToEndAsync();$stderrTask=$p.StandardError.ReadToEndAsync()
        if (-not $p.WaitForExit(8000)) { $timedOut=$true;$p.Kill();if (-not $p.WaitForExit(2000)) { throw 'Commander host process did not exit after kill.' } }
        $end=$clock.Elapsed.TotalSeconds
        if (-not $stdoutTask.Wait(1000) -or -not $stderrTask.Wait(1000)) { throw 'Commander output did not close.' }
        $raw=$stdoutTask.Result;$stderr=$stderrTask.Result
        if ($timedOut) { throw 'Commander batch exceeded 8s; no result accepted.' }
        $values=@(Convert-BatchReply ($raw+"`n"+$stderr) $Addresses $p.ExitCode)
        return [pscustomobject]@{Values=$values;Start=$begin;End=$end;ExitCode=$p.ExitCode}
    } finally {
        $cleanup=@()
        try {
            if ($started -and -not $p.HasExited) { $p.Kill();if (-not $p.WaitForExit(2000)) { $cleanup+='Host process termination not confirmed.' } }
        } catch { $cleanup+=('Host cleanup: '+$_.Exception.Message) }
        try { if ($started -and $null -ne $stdoutTask -and $stdoutTask.IsCompleted) { $raw=$stdoutTask.Result } }
        catch { $cleanup+=('stdout: '+$_.Exception.Message) }
        try { if ($started -and $null -ne $stderrTask -and $stderrTask.IsCompleted) { $stderr=$stderrTask.Result } }
        catch { $cleanup+=('stderr: '+$_.Exception.Message) }
        try {
            ($raw+"`n"+$stderr+"`n"+($cleanup -join "`n")) | Set-Content -LiteralPath (Join-Path $logDir ($Name+'.log')) -Encoding UTF8
        } catch { Write-Warning ('Could not save Commander log: '+$_.Exception.Message) }
        finally { $p.Dispose() }
    }
}

Write-Host 'Command-line test: native JLink batch mode; keep current v5 firmware and wiring.'
$active=$null
try {
    $initial=Invoke-Batch @(0x50000000,0x50000020,0x50000024,0x50000010) 'initial'
    $v=$initial.Values;$moder=$v[0];$afrl=$v[1];$afrh=$v[2];$idr=$v[3]
    if (($moder -band 3) -ne 2 -or (($moder -shr 6) -band 3) -ne 2 -or (($moder -shr 8) -band 3) -ne 2 -or (($moder -shr 16) -band 3) -ne 2 -or ($afrl -band 15) -ne 0 -or (($afrl -shr 12) -band 15) -ne 0 -or (($afrl -shr 16) -band 15) -ne 1 -or ($afrh -band 15) -ne 1 -or ($idr -band 256) -eq 0) { throw 'GPIO configuration or initial NSS-high check failed; no SPI data sent.' }
    $summary.initial_gpio=$v
    foreach ($value in @(0,255,0,255)) {
        $number=$summary.phases.Count+1
        Write-Host ('Phase {0}: CH347 {1:X2}; reading PA4...' -f $number,$value)
        $active=[H3CliV1.Output]::new([byte]$value,[H3CliV1.NativeTransport]::new());$active.Start()
        try {
            if (-not $active.Ready.WaitOne(5000)) { throw 'CH347 initialization/first transfer exceeded 5s.' }
            if ($active.Error -or $active.Transfers -lt 1) { throw ('CH347: '+$active.Error) }
            $phaseStart=$clock.Elapsed.TotalSeconds
            $batch=Invoke-Batch ([uint32[]](@(0x50000010)*48)) ('phase'+$number)
            $stopRequested=$clock.Elapsed.TotalSeconds
        } finally {
            if (-not $active.Stop(2500)) { $summary.cleanup_unconfirmed=$true;throw 'CH347 SDK did not return; no further phases started.' }
        }
        if ($active.Error -or -not $active.Closed -or $active.LimitReached) { throw ('CH347 phase failed: '+$active.Error) }
        if ($batch.Start -lt $phaseStart -or $batch.End -gt $stopRequested) { throw 'Batch outside constant stimulus phase.' }
        $ids=@($batch.Values);$low=@($ids | Where-Object { ($_ -band 16) -eq 0 }).Count
        $high=$ids.Count-$low;$sckLow=@($ids | Where-Object { ($_ -band 1) -eq 0 }).Count;$sckHigh=$ids.Count-$sckLow
        $nssLow=@($ids | Where-Object { ($_ -band 256) -eq 0 }).Count
        if ($nssLow -ne 0) { throw 'NSS low observed; stopping further phases.' }
        $matching=@($ids | Where-Object { ($_ -band 256) -ne 0 -and (($_ -band 16) -ne 0) -eq ($value -eq 255) })
        $matchingSckLow=@($matching | Where-Object { ($_ -band 1) -eq 0 }).Count
        $matchingSckHigh=$matching.Count-$matchingSckLow
        $ok=($matching.Count -ge 2 -and $matchingSckLow -gt 0 -and $matchingSckHigh -gt 0)
        $phase=[ordered]@{phase=$number;value=$value;transfers=$active.Transfers;configuration=$active.Metadata;mosi_low=$low;mosi_high=$high;sck_low=$sckLow;sck_high=$sckHigh;nss_low=$nssLow;matching_sck_low=$matchingSckLow;matching_sck_high=$matchingSckHigh;matched=$ok;batch_start=$batch.Start;batch_end=$batch.End;idr=$ids}
        $summary.phases+=,$phase;Save-Summary
        Write-Host ('PA4 low={0}, high={1}; SCK low={2}, high={3}; matched={4}' -f $low,$high,$sckLow,$sckHigh,$ok)
        $active=$null
    }
    $ok=@($summary.phases | Where-Object {-not $_.matched}).Count -eq 0
    $summary.status=if($ok){'matched_levels_observed'}else{'insufficient_evidence'}
    Write-Host ('Result: '+$summary.status)
} catch {
    $summary.status='failed';$summary.errors+=,$_.Exception.Message
    Write-Host ('ERROR: '+$_.Exception.Message)
} finally {
    if ($null -ne $active) { if (-not $active.Stop(2500)) { $summary.cleanup_unconfirmed=$true } }
    Save-Summary
    Write-Host ('Logs: '+$logDir)
}
