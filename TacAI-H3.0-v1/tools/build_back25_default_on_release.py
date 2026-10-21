"""Package the default-on fix while preserving v5.3.9 algorithm/resources."""
from pathlib import Path
import hashlib, json, shutil, subprocess, sys, zipfile
from PyInstaller.archive.readers import CArchiveReader

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'pc/TfgMonitorPython_H3_v5.0'
OLD=ROOT/'release/TacAI_H3_v5_3_9_Back25SmoothCandidate_20261016'
OUT=ROOT/'release/TacAI_H3_v5_3_10_Back25DefaultOn_20261016'
NAME='TacAI H3 v5.3.10 Back25 Default On Candidate'
PROFILES=['local_force_profile.json','back_force_profile.json','back25_consistency_profile.json','back25_smooth_profile.json']


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    previous=json.loads((OLD/'source_sha256.json').read_text(encoding='utf-8'))
    # Other work in the checkout (for example SPI) is not part of this release.
    # Freeze the delivered baseline, then overlay only this task's three files.
    for name in ['back25_smooth_force.py','back25_consistency_force.py','local_high_force.py','local_state.py']:
        assert sha(SRC/name)==previous[name], f'Offline replay algorithm differs: {name}'
    assert not OUT.exists(), 'Use a new release directory'
    source=OUT/'source';source.mkdir(parents=True)
    with zipfile.ZipFile(OLD/'source.zip') as z:
        for member in z.namelist():
            assert (source/member).resolve().is_relative_to(source.resolve())
        z.extractall(source)
    for name in ['main.py','test_back25_consistency_monitor.py','test_back25_default_on.py','test_product_monitor_v45.py']:
        shutil.copy2(SRC/name,source/name)
    for name in PROFILES:
        assert sha(SRC/name)==previous[name]
        shutil.copy2(SRC/name,source/name);shutil.copy2(SRC/name,OUT/name)
    old_exe=OLD/'TacAI H3 v5.3.9 Back25 Smooth Candidate.exe'
    archive=CArchiveReader(str(old_exe))
    resources=['CH347DLLA64.dll','TacAI_H3_App_SPI_v2.tacai','TacAI_H3_App_v1_3_VTORFIX.tacai']
    for name in resources:
        assert name in archive.toc, name
        (source/name).write_bytes(archive.extract(name))
    assert sha(source/'CH347DLLA64.dll')==previous['CH347DLLA64.dll']
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--windowed',
             '--name',NAME,'--distpath',str(OUT),'--workpath',str(OUT/'build'),
             '--specpath',str(OUT/'build'),'--exclude-module','pandas','--exclude-module','scipy',
             '--collect-data','openpyxl','--collect-submodules','openpyxl',
             '--runtime-hook',str(source/'qt_platform_runtime_hook.py'),
             '--add-binary',f'{source / resources[0]};.',
             '--add-data',f'{source / "assets"};assets']
    for name in PROFILES+resources[1:]:command+=['--add-data',f'{source/name};.']
    command.append(str(source/'main.py'))
    with (OUT/'build.log').open('w',encoding='utf-8') as log:
        subprocess.run(command,cwd=source,check=True,stdout=log,stderr=subprocess.STDOUT)
    exe=OUT/(NAME+'.exe')
    with zipfile.ZipFile(OUT/'source.zip','x',zipfile.ZIP_DEFLATED) as z:
        for p in source.rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts:z.write(p,p.relative_to(source))
    hashes={p.name:sha(p) for p in source.iterdir() if p.is_file()}
    (OUT/'source_sha256.json').write_text(json.dumps(hashes,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest=dict(version='5.3.10-back25-default-on-candidate',exe=exe.name,exe_sha256=sha(exe),exe_bytes=exe.stat().st_size,
                  previous_version='5.3.9-back25-smooth-candidate',default_correction_index=4,default_runtime_profile='back',
                  algorithm_and_profiles_unchanged=True,domain_guards_unchanged=True,recovery_optimized=False,
                  hardware_connected=False,independent_acceptance_passed=False,
                  corrected_data_note='New second batch rejected by existing domain checks; offline forced extrapolation is not installed.')
    (OUT/'build_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'使用说明.md').write_text(
      '# TacAI H3 v5.3.10：后25 N专项补偿默认开启\n\n'
      '启动即选择“后25 N平滑区域补偿（新版候选）”，并配置对应运行模型；每次重启均默认开启。可在记录前手动切换或关闭。\n\n'
      '完全卸载并空载置零，保持至少2秒，再重新加载。模式已选择与当前帧已应用分别显示；缺少历史或超范围时保留原值并显示旁路原因。\n\n'
      '**本次后25N二次的2,248帧全部超出旧参数适用范围；默认开启不能消除这个参数覆盖问题。** 离线强制外推试算没有安装进本版。\n\n'
      '本版保持v5.3.9算法、四个参数文件及范围检查不变，只修复启动默认选项、日志及版本标记。回落未优化，未连接或刷写设备。\n',encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False))


if __name__=='__main__':main()
