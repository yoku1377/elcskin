# Real pipe/process integration with a fake executable; no hardware library is loaded.
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$path=Join-Path $root 'tools\H3_SPI_CommandLine.ps1'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Script parse failed.'}
foreach($name in @('Convert-BatchReply','Invoke-Batch')){
 $definition=$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$true) | Where-Object {$_.Name -eq $name}
 . ([scriptblock]::Create($definition.Extent.Text))
}
$logDir=Join-Path $root ('tmp\commandline_pipe_test_'+[guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $logDir)
$JLinkExe=Join-Path $logDir 'FakeCommander.exe'
$clock=[Diagnostics.Stopwatch]::StartNew()
$fake=@'
using System;
using System.IO;
using System.Text;
using System.Threading;
class FakeCommander {
 static int Main(string[] args) {
  string mode=Environment.GetEnvironmentVariable("H3_FAKE_BATCH_MODE");
  if(mode=="timeout") {Thread.Sleep(20000);return 0;}
  string path=args[Array.IndexOf(args,"-CommandFile")+1];
  var text=new StringBuilder("SEGGER J-Link Commander V7.54d\nJ-Link Commander will now exit on Error\nConnecting to J-Link via USB...O.K.\nS/N: -1\n");
  int count=0;
  foreach(string command in File.ReadAllLines(path)) {
   if(command=="IsHalted") text.Append(mode=="halted"?"CPU is halted.\n":"CPU is not halted.\n");
   if(command.StartsWith("mem32 ")) {
    string address=command.Split(' ')[1].Substring(2);
    text.Append(address+" = "+((count++%2==0)?"00000110":"00000111")+"\n");
   }
  }
  // Emit nothing until all commands have completed; never print a prompt.
  Thread.Sleep(100);
  if(mode=="large_stderr") Console.Error.Write(new string('x',100000)+"\n");
  Console.Write(text.ToString());
  return mode=="exit_error"?1:0;
 }
}
'@
Add-Type -TypeDefinition $fake -OutputAssembly $JLinkExe -OutputType ConsoleApplication
$oldMode=$env:H3_FAKE_BATCH_MODE
try {
 foreach($mode in @('ok','large_stderr')) {
  $env:H3_FAKE_BATCH_MODE=$mode
  $result=Invoke-Batch ([uint32[]](@(0x50000010)*48)) $mode
  if($result.Values.Count -ne 48 -or $result.Values[0] -ne 272 -or $result.End -le $result.Start){throw ('Integration result failed: '+$mode)}
 }
 foreach($mode in @('halted','exit_error','timeout')) {
  $env:H3_FAKE_BATCH_MODE=$mode;$rejected=$false
  try{$null=Invoke-Batch ([uint32[]](@(0x50000010)*48)) $mode}catch{$rejected=$true}
  if(-not $rejected -or -not (Test-Path -LiteralPath (Join-Path $logDir ($mode+'.log')))){throw ('Integration failure not preserved: '+$mode)}
 }
 Write-Output 'PASS: five real-process fake-executable cases: exit-only output/no prompt, 100KB stderr drain, CPU halted, nonzero exit, bounded timeout. No hardware accessed.'
} finally {$env:H3_FAKE_BATCH_MODE=$oldMode}
