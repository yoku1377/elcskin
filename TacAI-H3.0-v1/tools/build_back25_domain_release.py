"""Freeze the user-authorized rear25 update on the delivered default-on baseline."""
from pathlib import Path
import hashlib,json,shutil,subprocess,sys,zipfile

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'pc/TfgMonitorPython_H3_v5.0'
OLD=ROOT/'release/TacAI_H3_v5_3_10_Back25DefaultOn_20261016'
OUT=ROOT/'release/TacAI_H3_v5_3_11_Back25DomainUpdate_20261016'
NAME='TacAI H3 v5.3.11 Back25 Domain Update Candidate'
PROFILES=['local_force_profile.json','back_force_profile.json','back25_consistency_profile.json',
          'back25_smooth_profile.json','back25_domain_profile.json']
CHANGED=['main.py','local_high_force.py','back25_domain_force.py','test_back25_default_on.py',
         'test_back25_consistency_monitor.py','test_back25_domain_force.py']


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    assert not OUT.exists(),'Use a fresh release directory'
    frozen=OUT/'source';frozen.mkdir(parents=True)
    with zipfile.ZipFile(OLD/'source.zip') as z:
        for n in z.namelist():assert (frozen/n).resolve().is_relative_to(frozen.resolve())
        z.extractall(frozen)
    for name in CHANGED:shutil.copy2(SRC/name,frozen/name)
    (frozen/'testdata').mkdir(exist_ok=True)
    shutil.copy2(SRC/'testdata/back25_domain_frame.json',frozen/'testdata/back25_domain_frame.json')
    for name in PROFILES:
        if name!='back25_domain_profile.json':assert sha(SRC/name)==sha(OLD/name),name
        shutil.copy2(SRC/name,frozen/name);shutil.copy2(SRC/name,OUT/name)
    resources=['CH347DLLA64.dll','TacAI_H3_App_SPI_v2.tacai','TacAI_H3_App_v1_3_VTORFIX.tacai']
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--windowed',
             '--name',NAME,'--distpath',str(OUT),'--workpath',str(OUT/'build'),'--specpath',str(OUT/'build'),
             '--exclude-module','pandas','--exclude-module','scipy','--collect-data','openpyxl',
             '--collect-submodules','openpyxl','--runtime-hook',str(frozen/'qt_platform_runtime_hook.py'),
             '--add-binary',f'{frozen/resources[0]};.','--add-data',f'{frozen/"assets"};assets']
    for name in PROFILES+resources[1:]:command+=['--add-data',f'{frozen/name};.']
    command.append(str(frozen/'main.py'))
    with (OUT/'build.log').open('w',encoding='utf-8') as f:
        subprocess.run(command,cwd=frozen,check=True,stdout=f,stderr=subprocess.STDOUT)
    exe=OUT/(NAME+'.exe')
    with zipfile.ZipFile(OUT/'source.zip','x',zipfile.ZIP_DEFLATED) as z:
        for p in frozen.rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts:z.write(p,p.relative_to(frozen))
    hashes={p.name:sha(p) for p in frozen.iterdir() if p.is_file()}
    (OUT/'source_sha256.json').write_text(json.dumps(hashes,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest=dict(version='5.3.11-back25-domain-update-candidate',exe=exe.name,exe_sha256=sha(exe),exe_bytes=exe.stat().st_size,
        default_correction_index=4,default_runtime_profile='back',old_profiles_unchanged=True,
        source_baseline='5.3.10 default-on with v5.3.9 SPI baseline',changed_sources=CHANGED,
        updated_domain_training_frames=2248,old_conditions_regression_frames=12115,
        frozen_training_MAX_pct=0.4934915500893192,frozen_training_mean_pct=0.23518189319555324,
        development_heldout_MAX_pct=2.2944022568447884,development_heldout_mean_pct=0.8879175612077835,
        independent_acceptance_passed=False,recovery_optimized=False,hardware_connected=False)
    (OUT/'build_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    report=ROOT.parent/'outputs/20261016_后25N参数更新候选/修复说明与补偿结果.md'
    shutil.copy2(report,OUT/'修复说明与补偿结果.md')
    (OUT/'使用说明.md').write_text(
      '# TacAI H3 v5.3.11：后25 N本批更新候选\n\n'
      '启动已默认选择“后25 N专项补偿（本批更新候选）”。旧v5.3.9平滑参数可在下拉框第六项比较。\n\n'
      '完全卸载、空载置零并保持至少2秒，再重新加载。确认逐帧状态显示已应用；缺少历史或超范围时会显示旁路原因。\n\n'
      '本批2248帧冻结回放最差FS%MAX/mean为0.4935%/0.2352%；开发整轮留出为2.2944%/0.8879%，未全部达标。此版是参数更新候选，不能视为独立精度验收通过。\n\n'
      '原四份参数、其他五工况和回落算法保留。本次未连接串口或刷写设备。使用同一样件、同一落点/姿态复测；请同步记录测力计真实读数。\n',encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False))


if __name__=='__main__':main()
