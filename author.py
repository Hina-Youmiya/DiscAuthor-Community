from localization import translate
"""Local DVD-Video / Blu-ray HDMV authoring pipeline."""
from pathlib import Path
import argparse, json, math, os, re, shutil, signal, stat, subprocess, tempfile, threading, time
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont
import bluray
import encoding
import menu_feedback
import iso_export
import menu_templates
from fonts import resolve_font
from progress import EncoderProgress, tool_percentage, programme_range
from menu_text import fit_text, button_text_mask, button_outline_mask, text_style_config
from paths import ROOT,DATA_ROOT
FFMPEG=ROOT/'bin/ffmpeg'
if not FFMPEG.exists():FFMPEG=Path(os.environ.get('DISCAUTHOR_FFMPEG') or shutil.which('ffmpeg') or str(FFMPEG))
FFPROBE=ROOT/'bin/ffprobe'
if not FFPROBE.exists():FFPROBE=Path(os.environ.get('DISCAUTHOR_FFPROBE') or shutil.which('ffprobe') or str(FFMPEG.with_name('ffprobe')))
FONT=Path('/System/Library/Fonts/Hiragino Sans GB.ttc')
class Cancelled(Exception): pass

def input_stamp(path):
    s=Path(path).stat()
    if not stat.S_ISREG(s.st_mode):raise ValueError('素材不是普通文件：'+str(path))
    return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]

def capture_inputs(config):
    paths=[v['path'] for v in config.get('videos',[])]
    if config.get('menu_enabled',True):
        if menu_templates.settings(config):paths.extend(menu_templates.asset_paths(config))
        elif config.get('background'):paths.append(config['background'])
        if config.get('font',{}).get('path'):paths.append(resolve_font(config['font']['path']))
    if config.get('reexport_bd_iso'):paths.append(config['reexport_bd_iso'])
    # Watch the original path as well as its target: symlink replacement must
    # not silently select different material after validation resolves a path.
    return {str(Path(p).expanduser().absolute()):input_stamp(Path(p).expanduser()) for p in paths}

def verify_inputs(snapshot):
    for path,expected in snapshot.items():
        try:current=input_stamp(path)
        except (OSError,ValueError):current=None
        if current!=expected:raise ValueError('制作期间素材发生变化，请重新添加任务：'+path)

def video_map(info):
    index=info.get('video_stream_index')
    return f'0:{index}' if type(index) is int and index>=0 else '0:v:0'

class Job:
    def __init__(self, config, out):
        self.config=config; self.out=Path(out); self.status='queued'; self.message='等待制作'; self.progress=0
        self.cancel=threading.Event(); self.process=None; self.error=None; self.iso=None; self.log=None
        self.pause_requested=threading.Event()
        self.stage_progress=None;self.processed_seconds=None;self.total_seconds=None;self.speed=None
        self._progress_lock=threading.RLock()
        self.input_snapshot={}
    def pause(self):
        self.pause_requested.set()
    def resume(self):
        self.pause_requested.clear()
    def _wait_to_run(self):
        while self.pause_requested.is_set():
            if self.cancel.wait(.1):raise Cancelled()
        if self.cancel.is_set():raise Cancelled()
        verify_inputs(self.input_snapshot)
    def update_progress(self,ratio,progress_range,seconds=None,duration=None,speed=None):
        ratio=max(0.0,min(1.0,ratio));start,end=progress_range
        with self._progress_lock:
            self.progress=max(self.progress,start+(end-start)*ratio)
            self.stage_progress=ratio*100
            self.processed_seconds=seconds;self.total_seconds=duration;self.speed=speed
    def run(self,args,stdin=None,stdout=None,progress_range=None,duration=None):
        self._wait_to_run()
        args=[str(a) for a in args]
        encoder=EncoderProgress(duration) if duration is not None else None
        tracked=progress_range is not None and stdout is None
        if encoder is not None:
            if not tracked:raise ValueError('Encoder progress requires a progress range and no output pipe override')
            args[1:1]=['-progress','pipe:1','-stats_period','0.5','-nostats']
        self.log.write('\n$ '+repr(args)+'\n');self.log.flush()
        with subprocess.Popen(args,stdin=stdin,stdout=subprocess.PIPE if tracked else (stdout or self.log),
                              stderr=self.log if encoder or not tracked else subprocess.STDOUT,
                              text=tracked,bufsize=1 if tracked else -1,
                              env={**os.environ,'LC_ALL':'en_US.UTF-8'}) as p:
            self.process=p
            reader=None
            if tracked:
                self.update_progress(0,progress_range,0 if encoder else None,duration)
                def read_progress():
                    for line in p.stdout:
                        if encoder:
                            update=encoder.feed(line)
                            if update:
                                ratio,seconds,speed=update
                                self.update_progress(ratio,progress_range,seconds,duration,speed)
                        else:
                            self.log.write(line);self.log.flush()
                            ratio=tool_percentage(line)
                            if ratio is not None:self.update_progress(ratio,progress_range)
                reader=threading.Thread(target=read_progress,daemon=True);reader.start()
            process_paused=False
            try:
                while p.poll() is None:
                    if self.cancel.wait(.2):
                        # SIGTERM cannot be handled by a stopped process until resumed.
                        try:os.kill(p.pid,signal.SIGCONT)
                        except ProcessLookupError:pass
                        p.terminate()
                        try:p.wait(5)
                        except subprocess.TimeoutExpired:p.kill();p.wait()
                        raise Cancelled()
                    requested=self.pause_requested.is_set()
                    if requested!=process_paused:
                        try:os.kill(p.pid,signal.SIGSTOP if requested else signal.SIGCONT)
                        except ProcessLookupError:pass
                        process_paused=requested
            finally:
                if reader:reader.join()
                if tracked:p.stdout.close()
                self.process=None
            if p.returncode: raise RuntimeError(f'{Path(args[0]).name} 运行失败（{p.returncode}），请查看 build.log')
            verify_inputs(self.input_snapshot)
            if tracked:self.update_progress(1,progress_range,duration,duration,self.speed)
    def stage(self,msg,pct):
        with self._progress_lock:
            self.message=msg;self.progress=max(self.progress,pct)
            self.stage_progress=100.0 if pct==100 else None
            self.processed_seconds=None;self.total_seconds=None;self.speed=None
    def snapshot(self):
        with self._progress_lock:
            result={k:getattr(self,k) for k in ['status','message','progress','error','iso','stage_progress','processed_seconds','total_seconds','speed']}|{'folder':str(self.out)}
            if self.status=='running' and self.pause_requested.is_set():
                result['status']='paused';result['speed']=None
            return result

def probe(path):
    p=subprocess.run([str(FFPROBE),'-v','error','-show_format','-show_streams','-of','json',str(path)],capture_output=True,text=True,timeout=30)
    if p.returncode: raise ValueError(f'无法读取视频：{path}')
    d=json.loads(p.stdout);v=next((s for s in d['streams'] if s['codec_type']=='video' and not s.get('disposition',{}).get('attached_pic')),None)
    if not v: raise ValueError(f'文件没有视频轨：{path}')
    duration=float(d.get('format',{}).get('duration') or v.get('duration') or 0)
    if not math.isfinite(duration) or duration<=0: raise ValueError(f'无法确定视频时长：{path}')
    a=next((s for s in d['streams'] if s['codec_type']=='audio'),None)
    return {'duration':duration,'width':v['width'],'height':v['height'],'fps':v.get('avg_frame_rate'),'field_order':v.get('field_order'),'audio':a is not None,
            'video_stream_index':v.get('index'),
            'audio_sample_rate':int(a.get('sample_rate') or 48000) if a else 48000,'audio_channels':int(a.get('channels') or 2) if a else 2,
            'codec':v.get('codec_name'), **{key:v.get(key) for key in ('pix_fmt','color_space','color_transfer','color_primaries','color_range','sample_aspect_ratio','r_frame_rate')}}

def validate(config):
    c=dict(config)
    menu_templates.settings(c,strict=True)
    iso_export.validate_options(c)
    if c.get('format') not in ['dvd','bluray']: raise ValueError('请选择 DVD 或 Blu-ray')
    c.setdefault('per_page',4);c.setdefault('layout','auto');c.setdefault('title','我的影片');c.setdefault('standard','ntsc');c.setdefault('bd_mode','auto');c.setdefault('capacity','dvd5' if c['format']=='dvd' else 'bd25')
    if c['standard'] not in ['ntsc','pal'] or c['bd_mode'] not in encoding.MODES:raise ValueError('视频制式无效')
    if c['capacity'] not in ({'dvd5','dvd9'} if c['format']=='dvd' else {'bd25','bd50'}):raise ValueError('光盘容量与格式不匹配')
    if not isinstance(c.get('videos'),list) or not 1<=len(c['videos'])<=30: raise ValueError('请添加 1–30 个视频')
    c.setdefault('menu_enabled',True);c.setdefault('no_menu_end_action','stop')
    c.setdefault('cleanup_work_on_success',False)
    if type(c['cleanup_work_on_success']) is not bool:raise ValueError('cleanup_work_on_success must be a boolean')
    c.setdefault('prevent_sleep',False)
    if type(c['prevent_sleep']) is not bool:raise ValueError('prevent_sleep must be a boolean')
    if type(c['menu_enabled']) is not bool:raise ValueError('menu_enabled must be a boolean')
    if c['no_menu_end_action'] not in ('loop','stop'):raise ValueError('no_menu_end_action must be loop or stop')
    if not c['menu_enabled']:
        if len(c['videos'])!=1:raise ValueError('无菜单模式只支持一个视频，请改为有菜单或保留一个视频')
        if c['no_menu_end_action']!='stop':raise ValueError('无菜单模式统一播放结束后停止，请修改旧工程的循环设置')
    c.setdefault('first_button',1);c.setdefault('after_play','menu');c.setdefault('remember_selection',True)
    c.setdefault('font',{'path':str(FONT),'index':0});c.setdefault('font_size',42)
    # Inactive menu settings are retained so a project can switch modes without
    # losing its design. They are not inputs to a disc with no menus.
    if c['menu_enabled']:
        template=menu_templates.settings(c,strict=True)
        if template is not None:c['menu_template']=template
        if type(c['per_page']) is not int or not 1<=c['per_page']<=12:raise ValueError('每页节目数应为 1–12')
        if c['layout'] not in ['auto','custom']:raise ValueError('菜单布局无效')
        if c['layout']=='custom' and template is None:
            rects=c.get('button_rects',[])
            if len(rects)!=c['per_page']:raise ValueError('自定义按钮区域数应等于每页节目数')
            for r in rects:
                if len(r)!=4 or any(type(n) is not int for n in r):raise ValueError('按钮区域必须是四个整数')
                x,y,w,h=r
                if x<0 or y<0 or w<80 or h<40 or x+w>1920 or y+h>1080:raise ValueError('按钮区域超出画面或过小')
        if type(c['first_button']) is not int or not 1<=c['first_button']<=c['per_page']:raise ValueError('first_button must be between 1 and per_page')
        if c['after_play'] not in ('menu','next','loop'):raise ValueError('after_play must be menu, next or loop')
        if type(c['remember_selection']) is not bool:raise ValueError('remember_selection must be a boolean')
        c['button_feedback']=menu_feedback.feedback_config(c)
        c['text_style']=text_style_config(c)
        if not 16<=int(c['font_size'])<=100:raise ValueError('字体大小应为 16–100')
        ImageFont.truetype(str(resolve_font(c['font']['path'])),int(c['font_size']),index=int(c['font'].get('index',0)))
    c['encoding']=encoding.settings(c)
    clean=[]
    for i,item in enumerate(c['videos']):
        p=Path(item['path']).expanduser().resolve()
        if not p.is_file(): raise ValueError(f'找不到视频：{p}')
        if any(x in str(p) for x in ['\n','\r','"']):raise ValueError('视频路径不能含换行或双引号')
        info=probe(p);encoding.check_source(info,p)
        clean.append({'path':str(p),'title':str(item.get('title') or p.stem)[:100], 'info':info})
    c['videos']=clean;c['title']=str(c['title'])[:100]
    if c['menu_enabled'] and c.get('background') and menu_templates.settings(c) is None:
        bg=Path(c['background']).expanduser().resolve()
        with Image.open(bg) as im:im.verify()
        c['background']=str(bg)
    if c.get('reexport_bd_iso') is not None:
        from reexport_validation import validate_source_project
        validate_source_project(c)
    return c

def rects_for(c,count=None):
    if menu_templates.settings(c) is not None:return menu_templates.rects(c,count)
    n=c.get('per_page',4)
    if c.get('layout')=='custom':return c['button_rects']
    cols=1 if n<=2 else (2 if n<=8 else 3);rows=math.ceil(n/cols)
    gap=32;w=(1660-gap*(cols-1))//cols;h=min(190,(600-gap*(rows-1))//rows)
    return [[130+(i%cols)*(w+gap),270+(i//cols)*(h+gap),w,h] for i in range(n)]

def pages_for(c):
    if not c.get('menu_enabled',True):return []
    n=c.get('per_page',4);pages=[];total=math.ceil(len(c['videos'])/n)
    for page in range(total):
        videos=c['videos'][page*n:page*n+n]
        rects=rects_for(c,len(videos))
        buttons=[]
        for j,v in enumerate(videos):
            buttons.append({'title':page*n+j+1,'label':v['title'],'rect':rects[j]})
        if page>0:buttons.append({'page':page-1,'label':translate('上一页',c.get('menu_language','ja')),'rect':[130,930,340,75]})
        if page<total-1:buttons.append({'page':page+1,'label':translate('下一页',c.get('menu_language','ja')),'rect':[1430,930,340,75]})
        nav=c.get('navigation',{})
        for i,button in enumerate(buttons):
            if str(i+1) in nav:
                refs=nav[str(i+1)]
                if len(refs)!=4 or any(type(r) is not int or not 1<=r<=14 for r in refs):
                    raise ValueError('自定义遥控目标超出当前菜单按钮范围')
                defaults=bluray.neighbours(buttons,i)
                button['nav']=[r if r<=len(buttons) else defaults[k] for k,r in enumerate(refs)]
        pages.append(buttons)
    return pages

def first_button_for(c,buttons):
    programs=sum('title' in button for button in buttons)
    return c.get('first_button',1) if c.get('first_button',1)<=programs else 1

def render(c,buttons,page,out):
    from PIL import ImageOps
    template=menu_templates.settings(c)
    original=template is not None or bool(c.get('background'))
    im=(menu_templates.render_background(c) if template is not None else ImageOps.fit(Image.open(c['background']).convert('RGB'),(1920,1080)) if original else Image.new('RGB',(1920,1080),'#101c2e'))
    d=ImageDraw.Draw(im)
    text_style=text_style_config(c)
    if not original:
        text,font=fit_text(d,c['title'],1650,78);d.text((130,100),text,font=font,fill='#f4f1e9')
        d.text((134,200),'SELECT A FILM',font=ImageFont.truetype(str(FONT),23),fill='#b3c8d5')
    for b in buttons:
        x,y,w,h=b['rect']
        if template is not None:menu_templates.draw_button(im,b,c)
        elif not original or 'page' in b:d.rounded_rectangle((x,y,x+w,y+h),radius=18,fill='#23364d',outline='#527087',width=2)
        # The base label and every highlight use exactly the same font geometry.
        if text_style['outline_enabled']:
            im.paste(text_style['outline_color'],(x,y,x+w,y+h),button_outline_mask(b,c))
        im.paste(text_style['color'],(x,y,x+w,y+h),button_text_mask(b,c))
    if not original:
        d.text((130,1030),translate('方向键选择 · 确认播放 · TOP MENU 返回目录',c.get('menu_language','ja')),font=ImageFont.truetype(str(FONT),22),fill='#b3c8d5')
    im.save(out);return im

def ffbase():return [FFMPEG,'-hide_banner','-nostdin','-y','-loglevel','warning']

def dvd_cache_policy(config,info,dest,bitrate):
    """Optional cache: preserve the original path whenever storage is tight.

    Allow two raw 8-bit frames plus 64 KiB per frame for lossless coding and
    container overhead, in addition to the existing four-copy build reserve.
    Only one programme cache exists at a time and it is removed after encoding.
    """
    height=576 if config.get('standard')=='pal' else 480
    fps=25 if height==576 else 30000/1001
    frames=math.ceil(float(info['duration'])*fps)+2
    audio_bound=math.ceil((float(info['duration'])+1)*int(info.get('audio_sample_rate') or 48000)*int(info.get('audio_channels') or 2)*4)
    cache_bound=frames*(720*height*3+65536)+audio_bound+64_000_000
    total=max(float(info['duration']),sum(float(v.get('info',{}).get('duration',0)) for v in config.get('videos',[])))
    build_reserve=math.ceil(total*(bitrate+192)*1000/8*4+500_000_000)
    policy={'strategy':'original_two_pass','cache_upper_bound_bytes':cache_bound,
            'build_reserve_bytes':build_reserve}
    # NUT preserves a channel count but not arbitrary speaker layouts. Keep
    # the original input's layout/downmix for multichannel programmes.
    if int(info.get('audio_channels') or 2)>2:
        policy['reason']='preserve_multichannel_layout';return policy
    try:available=shutil.disk_usage(Path(dest).parent).free
    except OSError:
        policy['reason']='space_check_unavailable';return policy
    policy['available_bytes']=available
    if available<cache_bound+build_reserve:
        policy['reason']='insufficient_cache_space';return policy
    policy.update(strategy='ffv1_nut',reason='reuse_final_filter_frames')
    return policy

def dvd_cached_encode(j,source,dest,info,plan,video,audio,passlog,progress_range):
    """Cache filtered video and lossless audio together, then encode two passes.

    A separate fast audio input plus unbounded apad can exhaust FFmpeg's
    shortest/sync FIFO while video is still decoding. Interleaved input keeps
    both streams under the same demux backpressure; padding is finite as well.
    """
    start,end=progress_range;duration=float(info['duration']);span=end-start
    filtered=start+span*.70;analysed=start+span*.85
    fd,name=tempfile.mkstemp(prefix=Path(dest).stem+'.filtercache-',suffix='.nut',dir=Path(dest).parent)
    os.close(fd);cache=Path(name);policy=plan['filter_cache']
    try:
        j.stage('预处理画质（无损缓存）：'+Path(source).name,start)
        cache_input=ffbase()+['-i',source]
        padding_duration=duration+1
        if not info['audio']:
            cache_input+=['-f','lavfi','-i',f'anullsrc=r=48000:cl=stereo:d={padding_duration:.9f}']
        j.run(cache_input+['-map',video_map(info),'-sn','-dn','-map_metadata','-1',
              '-vf',plan['filter'],'-fps_mode','passthrough','-c:v','ffv1','-level','3',
              '-coder','0','-context','0','-g','1','-slices','4','-slicecrc','1',
              '-pix_fmt','yuv420p','-map','0:a:0' if info['audio'] else '1:a:0',
              '-c:a','pcm_f32le',cache],progress_range=(start,filtered),duration=duration)
        policy['actual_cache_bytes']=cache.stat().st_size
        policy['audio_cache_codec']='pcm_f32le'
        p=subprocess.run([str(FFPROBE),'-v','error','-show_entries',
                          'format=start_time,duration:stream=codec_type,start_time','-of','json',str(cache)],
                         capture_output=True,text=True,timeout=30)
        if p.returncode:raise RuntimeError('无法读取无损缓存时间戳')
        metadata=json.loads(p.stdout)
        # An AV container uses a shared origin, which need not be the video's
        # first timestamp. Restore that origin without erasing the AV offset.
        offset=float(metadata.get('format',{}).get('start_time',0))
        if not math.isfinite(offset):raise RuntimeError('无损缓存时间戳无效')
        policy['container_start_seconds']=offset
        cache_duration=float(metadata.get('format',{}).get('duration') or duration)
        if not math.isfinite(cache_duration) or cache_duration<=0:raise RuntimeError('无损缓存时长无效')
        padding_duration=max(duration,cache_duration)+1
        policy['audio_padding_limit_seconds']=padding_duration
        # NUT keeps rational PTS and pixels, but not every colour/field tag.
        # Restore frame properties without applying colour conversion twice.
        restore=('setparams=field_mode='+('tff' if plan['interlaced'] else 'prog')+
                 ':range=tv:color_primaries='+plan['color_primaries']+
                 ':color_trc='+plan['color_transfer']+':colorspace='+plan['color_space']+
                 ',setsar='+('64/45' if j.config['standard']=='pal' else '32/27'))
        cached_video=list(video);cached_video[cached_video.index('-vf')+1]=restore
        cached_video[cached_video.index('-map')+1]='0:v:0'
        cached_input=ffbase()+['-itsoffset',f'{offset:.9f}','-i',cache]
        j.stage('分析画质（第一遍）：'+Path(source).name,filtered)
        j.run(cached_input+cached_video+['-an','-pass','1','-passlogfile',passlog,'-f','null',os.devnull],
              progress_range=(filtered,analysed),duration=duration)
        cached_audio=list(audio);cached_audio[cached_audio.index('-map')+1]='0:a:0'
        cached_audio[cached_audio.index('-af')+1]=f'apad=whole_dur={padding_duration:.9f}'
        j.stage('编码输出（第二遍）：'+Path(source).name,analysed)
        j.run(cached_input+cached_video+cached_audio+
              ['-pass','2','-passlogfile',passlog,'-shortest',dest],
              progress_range=(analysed,end),duration=duration)
    finally:
        try:cache.unlink(missing_ok=True);policy['cache_removed']=True
        except OSError as error:
            policy.update(cache_removed=False,retained_cache=str(cache))
            j.log.write('无法清理临时无损缓存：'+str(error)+'\n')

def encode(j,source,dest,info,menu=False,bitrate=18000,progress_range=None):
    c=j.config;dvd=c['format']=='dvd';pal=c['standard']=='pal'
    # Direct encode callers also get automatic mode selection from this source.
    if not c.get('videos') and c.get('bd_mode','auto')=='auto':
        c={**c,'videos':[{'info':info}]}
    encoding.check_source(info,source)
    plan=encoding.plan(c,info,menu)
    inputs=ffbase()
    if menu:
        inputs+=['-loop','1','-framerate',plan['fps'],'-i',source,'-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
    else:
        inputs+=['-i',source]
        if not info['audio']:inputs+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
    video=['-map','0:v:0' if menu else video_map(info),'-sn','-dn','-map_metadata','-1']
    if menu:video+=['-t','2']
    if dvd:
        video+=['-target','pal-dvd' if pal else 'ntsc-dvd','-vf',plan['filter'],
                '-aspect','16:9','-b:v',f'{bitrate}k','-maxrate:v','8500k',
                '-bufsize:v','1835008','-pix_fmt','yuv420p','-g',('15' if pal else '18'),'-bf','2']
        if plan['interlaced']:video+=['-flags','+ilme+ildct','-alternate_scan','1']
    else:
        gop='25' if plan['fps']=='25' else ('30' if plan['interlaced'] else '24')
        scan_params = (':fake-interlaced=1' if plan.get('progressive_compat') else
                       ':tff=1' if plan['interlaced'] else '')
        x264='bluray-compat=1:force-cfr=1:aud=1:nal-hrd=vbr:vbv-maxrate=30000:vbv-bufsize=30000:ref=3:b-pyramid=strict:keyint='+gop+':min-keyint=1:open-gop=0:slices=4'+scan_params
        video+=['-vf',plan['filter'],'-c:v','libx264','-preset',plan['preset'],
                '-profile:v','high','-level:v','4.1','-pix_fmt','yuv420p',
                '-b:v',f'{bitrate}k','-x264-params',x264]
        if plan['interlaced'] and not plan.get('progressive_compat'):
            video+=['-flags','+ilme+ildct']
    video+=['-colorspace',plan['color_space'],'-color_primaries',plan['color_primaries'],
            '-color_trc',plan['color_transfer'],'-color_range','tv']
    audio=['-map',('1:a:0' if menu or not info['audio'] else '0:a:0'),'-c:a','ac3',
           '-b:a','192k' if dvd else '384k','-ar','48000','-ac','2','-af','apad']
    # Both passes share the exact video pipeline. Logs belong to this clip only.
    passlog=Path(dest).with_suffix('.pass')
    start,end=progress_range or (j.progress,j.progress)
    duration=2.0 if menu else float(info['duration'])
    if dvd and not menu and plan['passes']==2:
        plan['filter_cache']=dvd_cache_policy(c,info,dest,bitrate)
        if plan['filter_cache']['strategy']=='ffv1_nut':
            dvd_cached_encode(j,source,dest,info,plan,video,audio,passlog,(start,end))
            Path(dest).with_suffix('.encoding.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
            return
    if plan['passes']==2:
        midpoint=(start+end)/2
        j.stage('分析画质（第一遍）：'+Path(source).name,start)
        j.run(inputs+video+['-an','-pass','1','-passlogfile',passlog,'-f','null',os.devnull],
              progress_range=(start,midpoint),duration=duration)
        j.stage('编码输出（第二遍）：'+Path(source).name,midpoint)
        start=midpoint
        second=['-pass','2','-passlogfile',passlog]
    else:
        second=[]
        if not menu and plan['settings']['quality'] == 'efficient':
            j.stage('高效单遍编码（CPU）：'+Path(source).name,start)
    j.run(inputs+video+audio+second+['-shortest',dest],progress_range=(start,end),duration=duration)
    Path(dest).with_suffix('.encoding.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))

def dvd(j,pages,bitrate):
    out=j.out;disc=out/'disc';work=out/'work';pal=j.config['standard']=='pal';height=576 if pal else 480
    menu_enabled=j.config.get('menu_enabled',True)
    project=ET.Element('dvdauthor',{'dest':str(disc)})
    vmg=ET.SubElement(project,'vmgm');ET.SubElement(vmg,'fpc').text=('g0=0; g1=0; jump titleset 1 menu;' if menu_enabled else 'jump title 1;')
    menus=ET.SubElement(vmg,'menus');ET.SubElement(menus,'video',{'format':'pal' if pal else 'ntsc'})
    ts=ET.SubElement(project,'titleset')
    if menu_enabled:
        ms=ET.SubElement(ts,'menus')
        ET.SubElement(ms,'video',{'format':'pal' if pal else 'ntsc','aspect':'16:9','widescreen':'nopanscan'})
        ET.SubElement(ms,'audio',{'format':'ac3','lang':'ja'})
    for n,buttons in enumerate(pages):
        menu_range=(2+6*n/len(pages),2+6*(n+1)/len(pages))
        j.stage(f'制作 DVD 菜单 {n+1}/{len(pages)}',menu_range[0])
        png=work/f'menu{n}.png';im=render(j.config,buttons,n,png)
        raw=work/f'menu{n}.mpg';encode(j,png,raw,{},True,6000,progress_range=menu_range)
        masks=[]
        for state,disc_state in [('selected','highlight'),('activated','select')]:
            mask=menu_feedback.page_overlay(buttons,state,j.config,(720,height))
            fn=work/f'menu{n}-{disc_state}.png';mask.save(fn);masks.append(fn)
        sub=ET.Element('subpictures',{'format':'PAL' if pal else 'NTSC'});stream=ET.SubElement(sub,'stream');spu=ET.SubElement(stream,'spu',{'start':'00:00:00.00','highlight':str(masks[0]),'select':str(masks[1]),'transparent':'000000','force':'yes'})
        for i,b in enumerate(buttons):
            coords=menu_feedback.dvd_button_rect(b,height)
            attrs={'name':f'b{i+1}',**dict(zip(('x0','y0','x1','y1'),map(str,coords)))}
            for direction,ref in zip(['up','down','left','right'],bluray.neighbours(buttons,i)):attrs[direction]=f'b{ref}'
            ET.SubElement(spu,'button',attrs)
        sx=work/f'spu{n}.xml';ET.ElementTree(sub).write(sx,encoding='utf-8',xml_declaration=True)
        muxed=work/f'menu{n}-buttons.mpg'
        with raw.open('rb') as fi,muxed.open('wb') as fo:j.run([ROOT/'deps/bin/spumux',sx],stdin=fi,stdout=fo)
        pgc=ET.SubElement(ms,'pgc',{'entry':'root'} if n==0 else {})
        first=first_button_for(j.config,buttons)
        restore=f'if (g1 eq 0) {{ button={first*1024}; }} else {{ button=g1; }} g1=0;'
        route=' '.join(f'if (g0 eq {k+1}) {{ g0=0; jump menu {k+1}; }}' for k in range(1,len(pages))) if n==0 else ''
        ET.SubElement(pgc,'pre').text=route+' '+restore
        ET.SubElement(pgc,'vob',{'file':str(muxed),'pause':'inf'})
        for i,b in enumerate(buttons):ET.SubElement(pgc,'button',{'name':f'b{i+1}'}).text=(f'jump title {b["title"]};' if 'title' in b else f'g0=0; g1=0; jump menu {b["page"]+1};')
        ET.SubElement(pgc,'post').text=f'jump menu {n+1};'
    titles=ET.SubElement(ts,'titles');ET.SubElement(titles,'video',{'format':'pal' if pal else 'ntsc','aspect':'16:9'})
    ET.SubElement(titles,'audio',{'format':'ac3','lang':'ja'})
    for i,v in enumerate(j.config['videos']):
        progress_range=programme_range(j.config['videos'],i)
        j.stage(f'编码节目 {i+1}/{len(j.config["videos"])}：{v["title"]}',progress_range[0])
        dest=work/f'title{i}.mpg';encode(j,v['path'],dest,v['info'],bitrate=bitrate,progress_range=progress_range)
        pgc=ET.SubElement(titles,'pgc')
        if menu_enabled:
            page=i//j.config.get('per_page',4);slot=i%j.config.get('per_page',4)+1
            selected=slot if j.config.get('remember_selection',True) else first_button_for(j.config,pages[page])
            ET.SubElement(pgc,'pre').text=f'g0={page+1}; g1={selected*1024};'
        ET.SubElement(pgc,'vob',{'file':str(dest)})
        has_next=i+1<len(j.config['videos'])
        if menu_enabled:
            after=j.config.get('after_play','menu')
            post=f'jump title {i+2};' if after in ('next','loop') and has_next else ('jump title 1;' if after=='loop' else 'call menu entry root;')
        else:
            post=f'jump title {i+2};' if has_next else ('jump title 1;' if j.config.get('no_menu_end_action','stop')=='loop' else 'exit;')
        ET.SubElement(pgc,'post').text=post
    xml=out/'dvd.xml';ET.ElementTree(project).write(xml,encoding='utf-8',xml_declaration=True)
    j.stage('编译 DVD 导航结构',85);j.run([ROOT/'deps/bin/dvdauthor','-x',xml])
    j.stage('生成 DVD-Video ISO',94);iso=out/'DVD_VIDEO.iso'
    j.run([ROOT/'bin/mkisofs','-dvd-video','-udf','-m','._*','-m','.DS_Store','-V','DISC_AUTHOR','-o',iso,disc],progress_range=(94,99));return iso

def bd(j,pages,bitrate):
    # encode() already writes x264 NAL-HRD and buffering/picture-timing SEI.
    # Do not request tsMuxer's automatic SEI insertion: a missing VCL-HRD
    # alone triggers it, replacing valid x264 timing with the fixed 855000
    # delay (truncated to 330712 by our 19-bit field; the HRD limit is 90000).
    # This applies to menu video as well as titles; keep the encoded timing.
    out=j.out;work=out/'work';disc=out/'disc';streams=[]
    for n,buttons in enumerate(pages):
        menu_range=(2+6*n/len(pages),2+6*(n+1)/len(pages))
        j.stage(f'编译 Blu-ray 菜单 {n+1}/{len(pages)}',menu_range[0])
        png=work/f'menu{n}.png';render(j.config,buttons,n,png);video=work/f'menu{n}.mkv';encode(j,png,video,{},True,12000,progress_range=menu_range)
        ig=work/f'menu{n}.sup';bluray.write_igs(ig,buttons,j.config);streams.append((video,ig))
    for i,v in enumerate(j.config['videos']):
        progress_range=programme_range(j.config['videos'],i,end=82)
        j.stage(f'编码节目 {i+1}/{len(j.config["videos"])}：{v["title"]}',progress_range[0])
        dest=work/f'title{i}.mkv';encode(j,v['path'],dest,v['info'],bitrate=bitrate,progress_range=progress_range);streams.append((dest,None))
    # Single-title STOP route tested on DMP-BDT180, not a blanket Panasonic
    # compatibility claim. Keep tsMuxer's original index/MovieObject and ISO
    # layout; the common output guard below also checks the actual AVC bytes.
    # Custom menus, multiple titles and looping still need authored navigation.
    native_stop=(not j.config.get('menu_enabled',True) and not pages
                 and len(streams)==1 and j.config.get('no_menu_end_action','stop')=='stop')
    if native_stop:
        video=streams[0][0];meta=work/'native-stop.meta';iso=out/'BLURAY_VIDEO.iso'
        meta.write_text(f'MUXOPT --blu-ray --vbr --new-audio-pes --hdmv-descriptors --mplsOffset=0 --m2tsOffset=0\n'
                        f'V_MPEG4/ISO/AVC, "{video}", track=1, contSPS\n'
                        f'A_AC3, "{video}", track=2, lang=jpn\n')
        j.stage('生成 UDF 2.50 Blu-ray ISO',83)
        j.run([ROOT/'bin/tsmuxer',meta,iso],progress_range=(83,96))
        # The cleanup contract owns a disc directory even when no BDMV tree
        # is materialized. No customer files are placed here.
        disc.mkdir()
        (out/'bluray-authoring.json').write_text(json.dumps({
            'route':'tsmuxer-native-single-stop','navigation':'tsmuxer-native',
            'iso_packaging':'tsmuxer-direct','app_info':'upstream-unspecified',
            'physical_reference':'User-confirmed DMP-BDT180; DMP-BD65 failure reported',
            'this_output_physical_player_tested':False},ensure_ascii=False,indent=2))
        return iso
    mux_sizes=[video.stat().st_size for video,_ in streams];mux_total=max(1,sum(mux_sizes))
    for i,(video,ig) in enumerate(streams):
        mux_range=(83+10*sum(mux_sizes[:i])/mux_total,83+10*sum(mux_sizes[:i+1])/mux_total)
        j.stage(f'封装 Blu-ray 流 {i+1}/{len(streams)}',mux_range[0])
        meta=work/f'mux{i}.meta';folder=work/f'mux{i}'
        s=f'MUXOPT --blu-ray --vbr --new-audio-pes --hdmv-descriptors --mplsOffset={i} --m2tsOffset={i}\nV_MPEG4/ISO/AVC, "{video}", track=1, contSPS\nA_AC3, "{video}", track=2, lang=jpn\n'
        if ig:s+=f'S_HDMV/PGS, "{ig}", lang=jpn, ig\n'
        meta.write_text(s);j.run([ROOT/'bin/tsmuxer',meta,folder],progress_range=mux_range)
        if i==0:shutil.copytree(folder,disc)
        else:
            for sub,ext in [('STREAM','m2ts'),('CLIPINF','clpi'),('PLAYLIST','mpls')]:
                for p in (folder/'BDMV'/sub).glob(f'*.{ext}'):
                    if p.name.startswith('._'):continue
                    shutil.copy2(p,disc/'BDMV'/sub/p.name)
                    if sub!='STREAM':shutil.copy2(p,disc/'BDMV/BACKUP'/sub/p.name)
        if ig:
            pl=disc/f'BDMV/PLAYLIST/{i:05d}.mpls';bluray.set_still(pl);shutil.copy2(pl,disc/'BDMV/BACKUP/PLAYLIST'/pl.name)
    first_play_plan=encoding.plan(j.config,{} if pages else j.config['videos'][0]['info'],menu=bool(pages))
    bluray.navigation(disc,len(pages),len(j.config['videos']),j.config.get('per_page',4),j.config,
                      first_play_mode=first_play_plan['mode'])
    j.stage('生成 UDF 2.50 Blu-ray ISO',94);iso=out/'BLURAY_VIDEO.iso'
    j.run([ROOT/'bin/tsmuxer','--pack-iso',disc,iso,'DISC_AUTHOR'],progress_range=(94,97));return iso

def directory_identity(path):
    path=Path(path)
    if path.is_symlink() or not path.is_dir():raise ValueError('Cleanup requires an owned regular directory: '+str(path))
    st=path.stat()
    return str(path.resolve()),st.st_dev,st.st_ino

def cleanup_completed_job(j,owned_out,owned_work,report):
    """Delete only the fresh job's generated trees, after evidence is safe.

    Preflight every tree before the first removal. Refuse all symlinks and
    changed directory identities; rmtree uses its fd-based traversal on macOS.
    Cancellation is checked before this short, noninterruptible commit phase.
    """
    if directory_identity(j.out)!=owned_out:raise ValueError('Output directory changed; cleanup refused')
    work=j.out/'work';disc=j.out/'disc'
    if directory_identity(work)!=owned_work:raise ValueError('Work directory changed; cleanup refused')
    owned_disc=directory_identity(disc)
    if not shutil.rmtree.avoids_symlink_attacks:raise RuntimeError('Safe directory cleanup is unavailable on this system')
    trees=[work,disc]
    files=[]
    for tree in trees:
        for parent,dirs,names in os.walk(tree,followlinks=False):
            for name in dirs+names:
                path=Path(parent)/name
                if path.is_symlink():raise ValueError('Generated directory contains a symlink; cleanup refused: '+str(path))
            if tree==work:
                files.extend(Path(parent)/name for name in names if name.endswith(('.png','.encoding.json','.xml','.meta')))
    files.extend(p for p in j.out.glob('*.xml') if p.is_file())
    evidence=j.out/'evidence';evidence.mkdir(exist_ok=False)
    for source in files:
        if source.is_symlink():raise ValueError('Evidence symlink refused: '+str(source))
        target=evidence/source.relative_to(j.out)
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        report['evidence_files'].append(str(target.relative_to(j.out)))
    # Ensure input files could never be deleted even if a future caller changes
    # the authoring directory contract or tries a hard-coded source path.
    for video in j.config['videos']:
        source=Path(video['path']).expanduser().resolve()
        if any(source.is_relative_to(tree.resolve()) for tree in trees):
            raise ValueError('Source video is inside a generated directory; cleanup refused')
    if directory_identity(j.out)!=owned_out or directory_identity(work)!=owned_work or directory_identity(disc)!=owned_disc:
        raise ValueError('Generated directories changed; cleanup refused')
    if j.cancel.is_set():raise Cancelled()
    def removal_error(function,path,error):
        # On removable macOS filesystems an AppleDouble sidecar can disappear
        # with its companion file after scandir. Already absent is success.
        if not isinstance(error,FileNotFoundError):raise error
    for tree in trees:
        shutil.rmtree(tree,onexc=removal_error)
        report['removed'].append(tree.name)
    report['status']='complete'

def finalize_with_cleanup(j,iso,result,owned_out,owned_work):
    """Write a reviewable result for both successful and failed cleanup."""
    from burner import inspect_iso
    report={'requested':True,'status':'pending','removed':[],'evidence_files':[],'error':None}
    result['cleanup']=report
    result['structure_validation']={'valid':False}
    try:
        j._wait_to_run()
        image=inspect_iso(str(iso))
        if image['format']!=j.config['format']:raise ValueError('Generated ISO format does not match the project')
        result['structure_validation']={'valid':True,**image}
        cleanup_completed_job(j,owned_out,owned_work,report)
        result['status']='complete'
    except Cancelled:
        report.update(status='cancelled',error='Cancelled before cleanup');result['status']='cancelled';raise
    except Exception as exc:
        report.update(status='failed',error=str(exc));result['status']='failed';raise
    finally:
        (j.out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))

def build(j):
    sleep_guard=None
    result=None
    try:
        j.status='running';j.stage('检查视频与容量',1)
        j.input_snapshot=capture_inputs(j.config)
        j.config=validate(j.config);verify_inputs(j.input_snapshot)
        if j.config.get('prevent_sleep',False):
            sleep_guard=subprocess.Popen(['/usr/bin/caffeinate','-i','-m','-s','-w',str(os.getpid())],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        j.out.mkdir(parents=True,exist_ok=False);(j.out/'work').mkdir();j.log=(j.out/'build.log').open('w',buffering=1)
        owned_out=directory_identity(j.out);owned_work=directory_identity(j.out/'work')
        c=j.config;cap={'dvd5':4_700_000_000,'dvd9':8_540_000_000,'bd25':25_000_000_000,'bd50':50_000_000_000}[c['capacity']]
        seconds=sum(v['info']['duration'] for v in c['videos']);budget=int((cap*.90*8/seconds)/1000)-(192 if c['format']=='dvd' else 384)
        bitrate=min(7500 if c['format']=='dvd' else 24000,budget)
        if bitrate<(1800 if c['format']=='dvd' else 5000):raise ValueError('视频总时长过长，请选择更大容量或减少视频')
        estimate=seconds*(bitrate+(192 if c['format']=='dvd' else 384))*1000/8
        required=estimate*(5 if c.get('export_mode')=='project_name' else 4)+500_000_000
        if shutil.disk_usage(j.out).free < required:raise ValueError('输出盘空间不足，需要约 '+str(round(required/1e9,1))+' GB 空间')
        (j.out/'encoding-report.json').write_text(json.dumps({'settings':encoding.settings(c),'output_mode':encoding.plan(c,c['videos'][0]['info'])['mode'],'warnings':encoding.warnings(c),'videos':[{'path':v['path'],'source':v['info'],'plan':encoding.plan(c,v['info'])} for v in c['videos']]},ensure_ascii=False,indent=2))
        (j.out/'project.json').write_text(json.dumps(c,ensure_ascii=False,indent=2))
        (j.out/'input-snapshot.json').write_text(json.dumps(j.input_snapshot,ensure_ascii=False,indent=2))
        if c.get('reexport_bd_iso'):
            from bluray_reexport import reexport_one
            from reexport_validation import full_decode
            from bd_validation import validate_iso
            # A transport-counter repair cannot restore stripped AVC timing
            # or repair authored IG. Reject those sources before copying GBs.
            try:
                checks=validate_iso(c['reexport_bd_iso'],FFMPEG,j.out/'work',j._wait_to_run,menu_pages=len(pages_for(c)))
            except Exception as exc:
                checks={'passed':False,'source_iso':c['reexport_bd_iso'],'physical_player_tested':False,'error':str(exc)}
                raise
            finally:
                (j.out/'source-bd-validation.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
            reexport=reexport_one(c['reexport_bd_iso'],j.out/'repack',ROOT/'bin/tsmuxer',
                                  progress=lambda message,percent:j.stage(message,2+float(percent)*.68),
                                  checkpoint=j._wait_to_run,run=j.run)
            iso=Path(reexport['iso'])
            j.iso=str(iso)
            (j.out/'reexport.json').write_text(json.dumps(reexport,ensure_ascii=False,indent=2))
            full_decode(j,iso,FFMPEG,FFPROBE)
        else:
            pages=pages_for(c);iso=(dvd if c['format']=='dvd' else bd)(j,pages,bitrate)
        j._wait_to_run()
        if iso.stat().st_size>cap:raise ValueError('ISO 超过所选光盘容量，请降低时长或选择更大光盘')
        j.iso=str(iso)
        result={'format':c['format'],'iso':str(iso),'bytes':iso.stat().st_size,'video_bitrate_kbps':bitrate,'hardware_tested':False}
        if c['format']=='bluray':
            from bd_validation import validate_iso
            from bd_avc_repair import repair_iso
            j.stage('检查 Blu-ray 视频时序与菜单',98)
            try:
                # Operate only on this job's unpublished output. MKV's initial
                # CodecPrivate/user-data order survives tsMuxer and violates
                # H.264 7.4.1.2.3. Reorder those existing header NALs without
                # changing coded pictures, sound, timestamps or byte offsets.
                # The independent reader below checks the resulting bytes.
                if iso.is_symlink() or not iso.resolve().is_relative_to(j.out.resolve()):
                    raise ValueError('AVC normalization requires this job\'s owned unpublished ISO')
                normalization=repair_iso(iso,checkpoint=j._wait_to_run,generated_image=True)
                result['avc_normalization']=normalization
                (j.out/'bd-avc-normalization.json').write_text(json.dumps(normalization,ensure_ascii=False,indent=2))
                checks=validate_iso(iso,FFMPEG,j.out/'work',j._wait_to_run,menu_pages=len(pages_for(c)))
                result['bd_validation']=checks
            except Exception as exc:
                checks={'passed':False,'physical_player_tested':False,'error':str(exc)}
                result['bd_validation']=checks
                raise
            finally:
                (j.out/'bd-validation.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
        if c.get('reexport_bd_iso'):
            result.update(reexport_source_iso=c['reexport_bd_iso'],lossless_reexport=True,full_playback_validated=True)
        if c.get('cleanup_work_on_success',False) and not c.get('reexport_bd_iso'):
            finalize_with_cleanup(j,iso,result,owned_out,owned_work)
        else:
            result['structure_validation']={'valid':False}
            try:
                from burner import inspect_iso
                j._wait_to_run()
                image=inspect_iso(str(iso))
                if image['format']!=c['format']:raise ValueError('Generated ISO format does not match the project')
                result['structure_validation']={'valid':True,**image}
                result['status']='complete'
            except Exception:
                result['status']='cancelled' if j.cancel.is_set() else 'failed'
                raise
            finally:(j.out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        if c.get('export_mode')=='project_name':
            try:
                # Publishing is the last step: incomplete/invalid images and
                # failed cleanup must never appear in the delivery folder.
                if not result.get('structure_validation',{}).get('valid'):
                    from burner import inspect_iso
                    image=inspect_iso(str(iso))
                    if image['format']!=c['format']:raise ValueError('Generated ISO format does not match the project')
                    result['structure_validation']={'valid':True,**image}
                j.stage('按项目名保存 ISO',99)
                published=iso_export.publish_iso(j,iso)
                j.iso=str(published);result['iso']=str(published)
                if result.get('structure_validation',{}).get('path')==str(iso):
                    result['structure_validation']['path']=str(published)
                result.update(status='complete',export={'mode':'project_name','filename':published.name})
            except iso_export.UnsafeISOPublication as exc:
                # Some removable filesystems cannot commit a filename without
                # risking overwrite. Keep the verified original, never expose a
                # partially copied ISO as a finished delivery.
                j.iso=str(iso)
                result.update(status='complete',export={'mode':'task_folder','requested_mode':'project_name',
                    'fallback':True,'reason':str(exc),'filename':iso.name})
            except Exception:
                result['status']='cancelled' if j.cancel.is_set() else 'failed'
                raise
            finally:(j.out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        # A successfully published filename is the commit point. A late cancel
        # or unrelated source edit must not turn that completed delivery failed.
        if not (result.get('export',{}).get('mode')=='project_name'):
            j._wait_to_run()
        j.status='complete';j.stage('ISO 已生成（输出盘不支持安全直接命名，已保存在任务文件夹）' if result.get('export',{}).get('fallback') else 'ISO 已生成',100)
    except Cancelled:j.status='cancelled';j.message='已取消，已保留日志和中间文件'
    except Exception as e:
        import traceback
        j.status='failed';j.error=str(e);j.message='制作失败'
        if j.log:j.log.write(traceback.format_exc())
    finally:
        if result is not None and j.status in ('failed','cancelled'):
            result.update(status=j.status,error=j.error or j.message)
            try:(j.out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
            except OSError:pass
        if j.log:j.log.close()
        if sleep_guard is not None:
            try:
                sleep_guard.terminate()
                try:sleep_guard.wait(timeout=2)
                except subprocess.TimeoutExpired:sleep_guard.kill();sleep_guard.wait()
            except ProcessLookupError:pass
    return j

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('project');ap.add_argument('--output',required=True);a=ap.parse_args()
    j=Job(json.loads(Path(a.project).read_text()),a.output);build(j);print(json.dumps(j.snapshot(),ensure_ascii=False,indent=2));raise SystemExit(0 if j.status=='complete' else 1)
