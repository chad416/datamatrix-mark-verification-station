<#
  Generate blocks from SCL sources in a new TIA Portal V18 project and compile.

  Needs: TIA Portal V18 with STEP 7 Professional (licence), the Windows user in the
  "Siemens TIA Openness" group, Windows PowerShell 5.1. TIA Portal runs without user interface.

  Usage 1, the three production blocks (record in results\tia_compile.txt):
    powershell -ExecutionPolicy Bypass -File scripts\tia_build.ps1 -Blocks FB_A,FB_B

  Usage 2, the PLCSIM test project (blocks, test bench, data blocks, OB1) with a project archive:
    powershell -ExecutionPolicy Bypass -File scripts\tia_build.ps1 `
      -Blocks FB_JobSelect,FB_CodeContent,FB_MarkStation `
      -Sources plc\test\FB_BenchCases.scl,plc\test\FB_SimMarker.scl,plc\test\FB_SimReader.scl,plc\test\FB_SimRecords.scl,plc\test\FB_TestBench.scl,plc\test\DB_TestBench.db,plc\test\DB_TestResult.db,plc\test\OB_Main.scl `
      -ProjectName dmc36_plcsim_test -Simulation -Hardware -NoConfidentialDataProtection -FullAccessNoPassword `
      -OutDir C:\TIA_build\plcsim -Archive C:\TIA_build\plcsim\dmc36_plcsim_test.zap18 `
      -ResultFile results\tia_compile_testbench.txt

  -Blocks      block names; the source is <PlcDir>\<name>.scl. Generated in the order given
               (a called block before its caller).
  -Sources     more source files (.scl, .db), relative to the repo folder or absolute, generated
               after the blocks in the order given.
  -Simulation  switch on "Support simulation during block compilation" in the project (PLCSIM needs it).
  -Hardware    also compile the whole device (hardware and software) and record the result.
  -NoConfidentialDataProtection
               for a simulation test project only: declare that the confidential PLC configuration
               data has no password (the choice the security wizard of the CPU offers). Without a
               decision the hardware of a firmware V2.9 CPU does not compile and cannot be loaded.
  -FullAccessNoPassword
               for a simulation test project only: set the access level of the CPU to "full access"
               so that no access password is needed. The default of a new CPU asks for a password,
               and without one the hardware does not compile.
  -Archive     save the project as a compressed archive (.zap18) at this path.
  Result: the compile messages, printed and written to the result file.
#>
param(
    [string[]]$Blocks = @(),
    [string[]]$Sources = @(),
    [string]$PlcDir = "",
    [string]$OutDir = "C:\TIA_build",
    [string]$ResultFile = "",
    [string]$ProjectName = "build",
    [string]$Archive = "",
    [switch]$Simulation,
    [switch]$Hardware,
    [switch]$NoConfidentialDataProtection,
    [switch]$FullAccessNoPassword,
    [string]$Version = "V18",
    [string]$Cpu = "OrderNumber:6ES7 513-1AL02-0AB0/V2.9"
)
$ErrorActionPreference = "Stop"
$Blocks = @($Blocks | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$Sources = @($Sources | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($Blocks.Count + $Sources.Count -eq 0) { throw "give -Blocks and/or -Sources" }
$root = Split-Path -Parent $PSScriptRoot
if ($PlcDir -eq "") { $PlcDir = Join-Path $root "plc" }
if ($ResultFile -eq "") { $ResultFile = Join-Path $root "results\tia_compile.txt" }
elseif (-not [System.IO.Path]::IsPathRooted($ResultFile)) { $ResultFile = Join-Path $root $ResultFile }
$files = @()
foreach ($b in $Blocks) { $files += (Join-Path $PlcDir ($b + ".scl")) }
foreach ($s in $Sources) { if ([System.IO.Path]::IsPathRooted($s)) { $files += $s } else { $files += (Join-Path $root $s) } }
foreach ($f in $files) { if (-not (Test-Path $f)) { throw "source not found: $f" } }
$api = "C:\Program Files\Siemens\Automation\Portal $Version\PublicAPI\$Version\Siemens.Engineering.dll"
if (-not (Test-Path $api)) { throw "Openness API not found: $api" }

$code = @"
using System;
using System.IO;
using System.Linq;
using System.Text;
using Siemens.Engineering;
using Siemens.Engineering.HW;
using Siemens.Engineering.HW.Features;
using Siemens.Engineering.SW;
using Siemens.Engineering.SW.ExternalSources;
using Siemens.Engineering.Compiler;

public static class SclBuild {
    static void Walk(CompilerResultMessage m, StringBuilder sb, int depth) {
        sb.AppendLine(new string(' ', 2 * depth) + m.State + "  " + m.Path + "  " + m.Description);
        foreach (var c in m.Messages) Walk(c, sb, depth + 1);
    }
    public static string Run(string outDir, string cpu, string[] files, string projectName,
                             string archive, bool simulation, bool hardware, bool unprotect, bool fullAccess) {
        var sb = new StringBuilder();
        using (var tia = new TiaPortal(TiaPortalMode.WithoutUserInterface)) {
            var dir = new DirectoryInfo(outDir);
            var project = tia.Projects.Create(dir, projectName + "_" + DateTime.Now.ToString("yyyyMMdd_HHmmss"));
            if (simulation) {
                try {
                    project.SetAttribute("IsSimulationDuringBlockCompilationEnabled", true);
                    sb.AppendLine("simulation support during block compilation: " + project.GetAttribute("IsSimulationDuringBlockCompilationEnabled"));
                } catch (Exception ex) {
                    sb.AppendLine("SIMULATION SUPPORT NOT SET: " + (ex.InnerException ?? ex).Message);
                }
            }
            var dev = project.Devices.CreateWithItem(cpu, "PLC_1", "PLC_1");
            PlcSoftware plc = null;
            foreach (var item in dev.DeviceItems) {
                var sc = item.GetService<SoftwareContainer>();
                if (sc != null && sc.Software is PlcSoftware) { plc = (PlcSoftware)sc.Software; break; }
            }
            if (plc == null) throw new Exception("no PLC software in " + cpu);
            if (unprotect) {
                try {
                    PlcMasterSecretConfigurator ms = null;
                    foreach (var item in dev.DeviceItems) {
                        ms = item.GetService<PlcMasterSecretConfigurator>();
                        if (ms != null) break;
                    }
                    if (ms == null) {
                        sb.AppendLine("CONFIDENTIAL DATA PROTECTION: service not offered by the CPU");
                    } else {
                        ms.Unprotect();
                        sb.AppendLine("confidential PLC configuration data: " + ms.MasterSecretConfiguration + " (simulation test project)");
                    }
                } catch (Exception ex) {
                    sb.AppendLine("CONFIDENTIAL DATA PROTECTION NOT CHANGED: " + (ex.InnerException ?? ex).Message);
                }
            }
            foreach (var file in files) {
                var name = Path.GetFileNameWithoutExtension(file);
                try {
                    var src = plc.ExternalSourceGroup.ExternalSources.CreateFromFile(name, file);
                    src.GenerateBlocksFromSource();
                    sb.AppendLine("generated " + name);
                } catch (Exception ex) {
                    sb.AppendLine("GENERATE FAILED " + name + ": " + (ex.InnerException ?? ex).Message);
                }
            }
            if (fullAccess) {
                try {
                    PlcAccessLevelProvider al = null;
                    foreach (var item in dev.DeviceItems) {
                        al = item.GetService<PlcAccessLevelProvider>();
                        if (al != null) break;
                    }
                    if (al == null) {
                        sb.AppendLine("ACCESS LEVEL: service not offered by the CPU");
                    } else {
                        var before = al.PlcProtectionAccessLevel;
                        al.PlcProtectionAccessLevel = PlcProtectionAccessLevel.FullAccess;
                        sb.AppendLine("CPU access level: " + before + " -> " + al.PlcProtectionAccessLevel + " (simulation test project)");
                    }
                } catch (Exception ex) {
                    sb.AppendLine("ACCESS LEVEL NOT CHANGED: " + (ex.InnerException ?? ex).Message);
                }
            }
            var result = plc.GetService<ICompilable>().Compile();
            sb.AppendLine("COMPILE " + result.State + "  errors=" + result.ErrorCount + "  warnings=" + result.WarningCount);
            foreach (var m in result.Messages) Walk(m, sb, 1);
            if (hardware) {
                try {
                    var hw = dev.GetService<ICompilable>();
                    if (hw == null) {
                        sb.AppendLine("HWCOMPILE not offered by the device object");
                    } else {
                        var r2 = hw.Compile();
                        sb.AppendLine("HWCOMPILE " + r2.State + "  errors=" + r2.ErrorCount + "  warnings=" + r2.WarningCount);
                        foreach (var m in r2.Messages) Walk(m, sb, 1);
                    }
                } catch (Exception ex) {
                    sb.AppendLine("HWCOMPILE FAILED: " + (ex.InnerException ?? ex).Message);
                }
            }
            project.Save();
            if (!String.IsNullOrEmpty(archive)) {
                try {
                    var target = new FileInfo(archive);
                    if (!target.Directory.Exists) target.Directory.Create();
                    if (target.Exists) target.Delete();
                    project.Archive(target.Directory, target.Name, ProjectArchivationMode.Compressed);
                    target.Refresh();
                    sb.AppendLine("ARCHIVE " + target.FullName + "  " + (target.Exists ? target.Length + " bytes" : "NOT WRITTEN"));
                } catch (Exception ex) {
                    sb.AppendLine("ARCHIVE FAILED: " + (ex.InnerException ?? ex).Message);
                }
            }
            project.Close();
        }
        return sb.ToString();
    }
}
"@

$resolver = @"
using System; using System.IO; using System.Reflection;
public static class OpnResolve {
    static string[] dirs;
    public static void Init(string[] d) {
        dirs = d;
        AppDomain.CurrentDomain.AssemblyResolve += (s, e) => {
            var n = new AssemblyName(e.Name).Name;
            if (n.EndsWith(".resources")) return null;
            foreach (var dir in dirs) { var p = Path.Combine(dir, n + ".dll"); if (File.Exists(p)) return Assembly.LoadFrom(p); }
            return null;
        };
    }
}
"@
Add-Type -TypeDefinition $resolver
$portal = "C:\Program Files\Siemens\Automation\Portal $Version"
[OpnResolve]::Init(@((Split-Path -Parent $api), "$portal\Bin\PublicAPI", "$portal\Bin"))
New-Item -ItemType Directory -Force $OutDir | Out-Null
[void][System.Reflection.Assembly]::LoadFrom($api)
Add-Type -TypeDefinition $code -ReferencedAssemblies $api
$text = "TIA Portal $Version, CPU $Cpu, " + (Get-Date -Format "yyyy-MM-dd HH:mm") + "`r`n" +
    [SclBuild]::Run($OutDir, $Cpu, [string[]]$files, $ProjectName, $Archive, [bool]$Simulation, [bool]$Hardware, [bool]$NoConfidentialDataProtection, [bool]$FullAccessNoPassword)
$text
$text | Out-File -Encoding utf8 $ResultFile
