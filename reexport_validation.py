"""Guards and read-only final checks for lossless Blu-ray ISO re-export."""
from pathlib import Path
import json
import plistlib
import subprocess
from fractions import Fraction
import encoding

PROJECT_KEYS = ('title','format','capacity','standard','bd_mode','menu_enabled','menu_language',
                'per_page','layout','background','font','font_size','button_rects','navigation',
                'first_button','after_play','remember_selection','text_style','button_feedback','encoding','no_menu_end_action','menu_template')

def validate_source_project(config):
    value=config.get('reexport_bd_iso')
    if value is None:return None
    if config.get('format')!='bluray' or not isinstance(value,str) or not value:
        raise ValueError('无损重新封装只适用于 Blu-ray ISO')
    source=Path(value).expanduser().resolve()
    if not source.is_file() or source.suffix.lower()!='.iso':
        raise ValueError('找不到待重新封装的 Blu-ray ISO')
    previous=json.loads((source.parent/'project.json').read_text())
    for key in PROJECT_KEYS:
        default='stop' if key=='no_menu_end_action' else None
        if config.get(key,default)!=previous.get(key,default):
            raise ValueError('无损重新封装的项目配置与原 ISO 不一致：'+key)
    identify=lambda c:[(str(Path(v['path']).expanduser().resolve()),v['title']) for v in c['videos']]
    if identify(config)!=identify(previous):
        raise ValueError('无损重新封装的节目列表与原 ISO 不一致')
    from burner import inspect_iso
    if inspect_iso(str(source))['format']!='bluray':raise ValueError('原文件不是 Blu-ray Video ISO')
    config['reexport_bd_iso']=str(source)
    return source

def full_decode(j,iso,ffmpeg,ffprobe):
    """Mount this new ISO read-only and strictly decode every menu/title stream.

    Use one dense video/audio output, avoiding sparse/dense split buffering.
    PCM output timestamps are based on decoded samples; source stream timing is
    checked independently against the project and during strict demux/decode.
    """
    report={'passed':False,'iso':str(iso),'mount_readonly':True,'streams':[],'error':None}
    report_path=j.out/'playback-validation.json'
    mount_info=j.out/'work/reexport-mount.plist'
    device=None
    try:
        j.stage('检查重新封装的蓝光播放',72)
        with mount_info.open('wb') as stream:
            j.run(['/usr/bin/hdiutil','attach','-readonly','-nobrowse','-noautoopen','-plist',iso],stdout=stream)
        volumes=plistlib.loads(mount_info.read_bytes())['system-entities']
        mounted=next(v for v in volumes if v.get('mount-point'))
        device=mounted['dev-entry'];root=Path(mounted['mount-point'])
        streams=sorted((root/'BDMV/STREAM').glob('*.m2ts'))
        pages=(len(j.config['videos'])+j.config['per_page']-1)//j.config['per_page'] if j.config.get('menu_enabled',True) else 0
        if len(streams)!=pages+len(j.config['videos']):raise ValueError('重新封装的蓝光流数量不匹配')
        expected_names=[f'{i:05d}.m2ts' for i in range(len(streams))]
        if [s.name for s in streams]!=expected_names:raise ValueError('重新封装的蓝光流编号不匹配')
        total=sum(v['info']['duration'] for v in j.config['videos'])+pages
        elapsed=0
        for index,path in enumerate(streams):
            j._wait_to_run()
            probe_path=j.out/'work'/f'reexport-stream-{index}.json'
            with probe_path.open('wb') as out:
                j.run([ffprobe,'-v','error','-show_format','-show_streams','-of','json',path],stdout=out)
            metadata=json.loads(probe_path.read_text())
            video=next(s for s in metadata['streams'] if s['codec_type']=='video')
            audio=next(s for s in metadata['streams'] if s['codec_type']=='audio')
            duration=float(metadata['format']['duration'])
            title=j.config['videos'][index-pages] if index>=pages else None
            checks={'video':video['codec_name']=='h264','audio':audio['codec_name']=='ac3' and audio['sample_rate']=='48000',
                    'av_start_sync':abs(float(video.get('start_time',0))-float(audio.get('start_time',0)))<0.15}
            if title:
                checks['source_duration']=abs(duration-title['info']['duration'])<1.0
                planned=encoding.plan(j.config,title['info'])['fps']
                checks['frame_rate']=abs(float(Fraction(video['avg_frame_rate']))-float(Fraction(planned)))<0.001
                expected=title['info']['duration']
            else:expected=duration
            item={'stream':path.name,'title':title['title'] if title else 'Menu','duration_seconds':duration,'checks':checks,'full_decode_passed':False}
            report['streams'].append(item)
            if not all(checks.values()):raise ValueError('重新封装的蓝光音视频参数验证失败：'+path.name)
            span=(72+26*elapsed/total,min(98,72+26*(elapsed+expected)/total))
            j.stage('完整检查蓝光音视频：'+item['title'],span[0])
            j.run([ffmpeg,'-hide_banner','-nostdin','-v','error','-xerror','-err_detect','explode',
                   '-threads','6','-i',path,'-map','0:v:0','-map','0:a:0','-sn','-dn',
                   '-af','asetpts=N/SR/TB','-fps_mode','passthrough','-f','null','-'],
                  progress_range=span,duration=expected)
            item['full_decode_passed']=True;elapsed+=expected
            report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        report['passed']=True
    except Exception as exc:
        report['error']=str(exc);raise
    finally:
        # Detach even if cancel happened between attachment and plist parsing.
        if device is None:
            try:
                info=plistlib.loads(subprocess.check_output(['/usr/bin/hdiutil','info','-plist'],timeout=15))
                for image in info['images']:
                    if Path(image.get('image-path','')).resolve()==Path(iso).resolve():
                        device=next((v['dev-entry'] for v in image['system-entities'] if v.get('mount-point')),None)
            except Exception:pass
        if device:
            try:
                detach=subprocess.run(['/usr/bin/hdiutil','detach',device],capture_output=True,timeout=45)
                report['detached']=detach.returncode==0
                if detach.returncode:raise RuntimeError('Could not detach read-only validation image')
            except (OSError,subprocess.TimeoutExpired,RuntimeError) as exc:
                report['detached']=False;report['passed']=False
                report['detach_error']=str(exc)
                if not report['error']:report['error']=str(exc)
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    if not report['passed']:raise ValueError(report['error'] or 'Blu-ray playback validation failed')
    return report
