"""Freeze automatic routing on the delivered v5.3.12 baseline."""
from pathlib import Path
import hashlib,json,shutil,subprocess,sys,zipfile

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'pc/TfgMonitorPython_H3_v5.0'
OLD=ROOT/'release/TacAI_H3_v5_3_12_BaselineQuality_20261016_Final'
OUT=ROOT/'release/TacAI_H3_v5_3_13_AutomaticCompensation_20261016'
NAME='TacAI H3 v5.3.13 Automatic Compensation Candidate'
PROFILES=['local_force_profile.json','back_force_profile.json','back25_consistency_profile.json',
          'back25_smooth_profile.json','back25_domain_profile.json']
CHANGED=['main.py','local_high_force.py','automatic_compensation.py','test_automatic_compensation.py',
         'test_back25_default_on.py','test_back25_consistency_monitor.py']

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    assert not OUT.exists(),'Use a fresh release directory'
    frozen=OUT/'source';frozen.mkdir(parents=True)
    with zipfile.ZipFile(OLD/'source.zip') as z:
        for n in z.namelist():assert (frozen/n).resolve().is_relative_to(frozen.resolve())
        z.extractall(frozen)
    for name in CHANGED:shutil.copy2(SRC/name,frozen/name)
    shutil.copy2(SRC/'testdata/auto_front_frame.json',frozen/'testdata/auto_front_frame.json')
    for name in PROFILES:
        assert sha(SRC/name)==sha(OLD/name),name
        shutil.copy2(SRC/name,OUT/name)
    for name in ['h3_v3_3_force_model.py','back25_domain_force.py','back25_smooth_force.py','forcefield_2d.py',
                 'baseline_quality.py','local_state.py','regional_ard_force.py','regional_high_force.py']:
        assert sha(SRC/name)==sha(frozen/name),name
    resources=['CH347DLLA64.dll','TacAI_H3_App_SPI_v2.tacai','TacAI_H3_App_v1_3_VTORFIX.tacai']
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--windowed',
             '--name',NAME,'--distpath',str(OUT),'--workpath',str(OUT/'build'),'--specpath',str(OUT/'build'),
             '--exclude-module','pandas','--exclude-module','scipy','--collect-data','openpyxl',
             '--collect-submodules','openpyxl','--runtime-hook',str(frozen/'qt_platform_runtime_hook.py'),
             '--add-binary',f'{frozen/resources[0]};.','--add-data',f'{frozen/"assets"};assets']
    for name in PROFILES+resources[1:]:command+=['--add-data',f'{frozen/name};.']
    command.append(str(frozen/'main.py'))
    with (OUT/'build.log').open('w',encoding='utf8') as f:
        subprocess.run(command,cwd=frozen,check=True,stdout=f,stderr=subprocess.STDOUT)
    exe=OUT/(NAME+'.exe')
    with zipfile.ZipFile(OUT/'source.zip','x',zipfile.ZIP_DEFLATED) as z:
        for p in frozen.rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts:z.write(p,p.relative_to(frozen))
    (OUT/'source_sha256.json').write_text(json.dumps({p.name:sha(p) for p in frozen.iterdir() if p.is_file()},ensure_ascii=False,indent=2),encoding='utf8')
    manifest=dict(version='5.3.13-automatic-compensation-candidate',exe=exe.name,exe_sha256=sha(exe),exe_bytes=exe.stat().st_size,
        default_correction_index=6,default_runtime_profile='auto',available_profiles=['front','back'],
        calibration_profiles_unchanged=True,baseline_quality_unchanged=True,source_baseline='Delivered v5.3.12',
        changed_sources=CHANGED,independent_force_acceptance_passed=False,recovery_optimized=False,hardware_connected=False)
    (OUT/'build_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(manifest,ensure_ascii=False))

if __name__=='__main__':main()
