"""macOS image burning and explicitly requested optical tray control.

Media writes occur only in BurnManager.start(); tray operations never erase media.

Read-only DiscRecording queries; exact images are handed to hdiutil with burn
verification enabled by default. A process-wide flock survives a lost native frontend.
"""
import copy
import ctypes as C
from datetime import datetime, timezone
import fcntl
from functools import lru_cache
import json
import math
import os
import plistlib
from pathlib import Path
import re
import signal
import stat
import subprocess
import threading
import time
import uuid
from paths import ROOT

ACTIVE = {'preparing','burning','verifying','finishing','cancelling'}
class BurnConflict(ValueError): code = 409


class ISO9660:
    """Bounded directory reader: never mounts images or decodes video."""
    def __init__(self, path):
        self.f = open(path,'rb'); self.size = os.fstat(self.f.fileno()).st_size
        try:
            self.f.seek(16*2048); pvd = self.f.read(2048)
            if pvd[:7] != b'\x01CD001\x01': raise ValueError('ISO9660 absent')
            self.root = self.record(pvd[156:190])
        except Exception:self.close();raise
    def close(self): self.f.close()
    def record(self, data):
        if len(data)<34: raise ValueError('Invalid ISO directory')
        offset = int.from_bytes(data[2:6],'little')*2048
        size = int.from_bytes(data[10:14],'little')
        if offset+size>self.size: raise ValueError('Truncated ISO')
        return offset,size,bool(data[25]&2)
    def entries(self, record):
        offset,size,isdir=record
        if not isdir or size>16*1024*1024: raise ValueError('Invalid ISO directory')
        self.f.seek(offset); data=self.f.read(size); pos=0; result={}
        while pos<len(data):
            length=data[pos]
            if not length: pos=(pos//2048+1)*2048; continue
            item=data[pos:pos+length]
            if length<34 or len(item)!=length: raise ValueError('Invalid ISO directory')
            name=item[33:33+item[32]].decode('ascii','replace').split(';')[0]
            if name not in ('\x00','\x01'): result[name.upper()]=self.record(item)
            pos+=length
        return result
    def locate(self,path):
        rec=self.root
        for name in path.split('/'):
            rec=self.entries(rec).get(name.upper())
            if rec is None: return None
        return rec
    def names(self,path):
        rec=self.locate(path)
        return list(self.entries(rec)) if rec else []
    def read(self,path,limit=65536):
        rec=self.locate(path)
        if not rec or rec[2]: return b'',0
        self.f.seek(rec[0]);return self.f.read(min(limit,rec[1])),rec[1]


class BDFile(C.Structure): pass
CloseFile=C.CFUNCTYPE(None,C.POINTER(BDFile))
SeekFile=C.CFUNCTYPE(C.c_int64,C.POINTER(BDFile),C.c_int64,C.c_int)
ReadFile=C.CFUNCTYPE(C.c_int64,C.POINTER(BDFile),C.c_void_p,C.c_int64)
BDFile._fields_=[('internal',C.c_void_p),('close',CloseFile),('seek',SeekFile),('tell',C.c_void_p),('eof',C.c_void_p),('read',ReadFile),('write',C.c_void_p)]
class BDDir(C.Structure): pass
class DirEntry(C.Structure): _fields_=[('name',C.c_char*256)]
BDDir._fields_=[('internal',C.c_void_p),('close',C.CFUNCTYPE(None,C.POINTER(BDDir))),('read',C.CFUNCTYPE(C.c_int,C.POINTER(BDDir),C.POINTER(DirEntry)))]
class UDFBluray:
    def __init__(self,path):
        self.lib=C.CDLL(str(ROOT/'deps/lib/libbluray.2.dylib'))
        self.lib.bd_open.argtypes=[C.c_char_p,C.c_char_p];self.lib.bd_open.restype=C.c_void_p
        self.lib.bd_close.argtypes=[C.c_void_p]
        self.lib.bd_open_file_dec.argtypes=[C.c_void_p,C.c_char_p];self.lib.bd_open_file_dec.restype=C.POINTER(BDFile)
        self.lib.bd_open_dir.argtypes=[C.c_void_p,C.c_char_p];self.lib.bd_open_dir.restype=C.POINTER(BDDir)
        self.handle=self.lib.bd_open(os.fsencode(path),None)
        if not self.handle: raise ValueError('BDMV filesystem absent')
    def close(self): self.lib.bd_close(self.handle)
    def names(self,path):
        handle=self.lib.bd_open_dir(self.handle,path.encode());result=[]
        if not handle:return result
        try:
            ent=DirEntry()
            while len(result)<10000:
                if handle.contents.read(handle,C.byref(ent))!=0:break
                result.append(bytes(ent.name).decode('ascii','replace'))
        finally:handle.contents.close(handle)
        return result
    def read(self,path,limit=65536):
        handle=self.lib.bd_open_file_dec(self.handle,path.encode())
        if not handle:return b'',0
        try:
            size=handle.contents.seek(handle,0,2)
            if size<0:return b'',0
            handle.contents.seek(handle,0,0);buf=C.create_string_buffer(min(limit,size))
            count=handle.contents.read(handle,buf,len(buf))
            return buf.raw[:max(0,count)],size
        finally:handle.contents.close(handle)


def fingerprint(path):
    s=Path(path).stat()
    if not stat.S_ISREG(s.st_mode):raise ValueError('请选择本机 ISO 文件')
    return s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns

def inspect_iso(path):
    if not isinstance(path,str) or not path:raise ValueError('请选择本机 ISO 文件')
    path=Path(path).expanduser().resolve();before=fingerprint(path)
    if path.suffix.lower()!='.iso' or before[2]<32768 or before[2]%2048:raise ValueError('ISO 文件大小或扩展名无效')
    dvd=False;bd=False;reader=None
    try:
        reader=ISO9660(path)
        header,_=reader.read('VIDEO_TS/VIDEO_TS.IFO')
        if header.startswith(b'DVDVIDEO-VMG'):
            titles=[n for n in reader.names('VIDEO_TS') if re.fullmatch(r'VTS_\d\d_0.IFO',n)]
            dvd=bool(titles)
            for name in titles:
                if not reader.read('VIDEO_TS/'+name)[0].startswith(b'DVDVIDEO-VTS'):dvd=False
                if reader.read('VIDEO_TS/'+name[:7]+'1.VOB',16)[1]<2048:dvd=False
        # Reject mixed DVD/BD content rather than guessing a target medium.
        has_bd=bool(reader.locate('BDMV'))
    except ValueError:has_bd=True
    finally:
        if reader:reader.close()
    if has_bd:
        try:
            reader=UDFBluray(path)
            try:
                idx,_=reader.read('BDMV/index.bdmv');obj,_=reader.read('BDMV/MovieObject.bdmv')
                playlists=[n for n in reader.names('BDMV/PLAYLIST') if re.fullmatch(r'\d{5}\.mpls',n)]
                bd=idx[:4]==b'INDX' and idx[4:8] in (b'0100',b'0200',b'0300') and obj[:4]==b'MOBJ' and bool(playlists)
                for name in playlists:
                    data,size=reader.read('BDMV/PLAYLIST/'+name,4*1024*1024)
                    if data[:4]!=b'MPLS' or size>len(data) or len(data)<20:bd=False;break
                    offset=int.from_bytes(data[8:12],'big')
                    if offset+10>len(data):bd=False;break
                    count=int.from_bytes(data[offset+6:offset+8],'big');pos=offset+10
                    if not count:bd=False;break
                    for _ in range(count):
                        length=int.from_bytes(data[pos:pos+2],'big');clip=data[pos+2:pos+7].decode('ascii','replace')
                        if length<18 or pos+2+length>len(data) or not re.fullmatch(r'\d{5}',clip):bd=False;break
                        if reader.read('BDMV/CLIPINF/'+clip+'.clpi',8)[0][:4]!=b'HDMV':bd=False
                        if reader.read('BDMV/STREAM/'+clip+'.m2ts',16)[1]<192:bd=False
                        pos+=2+length
            finally:reader.close()
        except (ValueError,OSError):bd=False
    if dvd==bd:raise ValueError('镜像不是完整的 DVD-Video 或 Blu-ray Video')
    if fingerprint(path)!=before:raise ValueError('ISO 文件在检查期间发生变化')
    return {'path':str(path),'title':path.stem,'format':'dvd' if dvd else 'bluray','size_bytes':before[2],'required_bytes':before[2]}


class DiscRecording:
    """Exact-device queries and explicit tray control through public macOS APIs."""
    def __init__(self):
        self.dr=C.CDLL('/System/Library/Frameworks/DiscRecording.framework/DiscRecording')
        self.cf=C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        signatures={'CFRelease':(None,[C.c_void_p]),'CFArrayGetCount':(C.c_long,[C.c_void_p]),'CFArrayGetValueAtIndex':(C.c_void_p,[C.c_void_p,C.c_long]),'CFDictionaryGetValue':(C.c_void_p,[C.c_void_p,C.c_void_p]),'CFStringGetCString':(C.c_bool,[C.c_void_p,C.c_void_p,C.c_long,C.c_uint32]),'CFBooleanGetValue':(C.c_bool,[C.c_void_p]),'CFNumberGetValue':(C.c_bool,[C.c_void_p,C.c_int,C.c_void_p])}
        for name,(result,args) in signatures.items():
            fn=getattr(self.cf,name);fn.restype=result;fn.argtypes=args
        for name in ('CFGetTypeID','CFNumberGetTypeID','CFArrayGetTypeID','CFDictionaryGetTypeID'):
            fn=getattr(self.cf,name);fn.restype=C.c_ulong;fn.argtypes=[C.c_void_p] if name=='CFGetTypeID' else []
        self.cf.CFStringCreateWithCString.restype=C.c_void_p
        self.cf.CFStringCreateWithCString.argtypes=[C.c_void_p,C.c_char_p,C.c_uint32]
        self.dr.DRDeviceCopyDeviceForIORegistryEntryPath.restype=C.c_void_p
        self.dr.DRDeviceCopyDeviceForIORegistryEntryPath.argtypes=[C.c_void_p]
        self.dr.DRDeviceIsValid.restype=C.c_bool
        self.dr.DRDeviceIsValid.argtypes=[C.c_void_p]
        for name in ('DRDeviceEjectMedia','DRDeviceCloseTray'):
            fn=getattr(self.dr,name);fn.restype=C.c_int32;fn.argtypes=[C.c_void_p]
        self.dr.DRCopyDeviceArray.restype=C.c_void_p
        for name in ('DRDeviceCopyInfo','DRDeviceCopyStatus'):
            fn=getattr(self.dr,name);fn.restype=C.c_void_p;fn.argtypes=[C.c_void_p]
    def key(self,name):return C.c_void_p.in_dll(self.dr,'kDRDevice'+name).value
    def get(self,obj,name):return self.cf.CFDictionaryGetValue(obj,self.key(name)) if obj else None
    def string(self,value):
        if not value:return ''
        b=C.create_string_buffer(8192)
        return b.value.decode('utf8','replace') if self.cf.CFStringGetCString(value,b,len(b),0x08000100) else ''
    def boolean(self,obj,name):
        v=self.get(obj,name);return bool(v and self.cf.CFBooleanGetValue(v))
    def optional_boolean(self,obj,name):
        value=self.get(obj,name)
        return bool(self.cf.CFBooleanGetValue(value)) if value else None
    def number(self,obj,name):
        value=self.get(obj,name);n=C.c_longlong()
        return n.value if value and self.cf.CFNumberGetValue(value,4,C.byref(n)) else None
    def speed_number(self,value):
        """DiscRecording speed CFNumbers are floating decimal KB/s, not bytes/s."""
        if not value or self.cf.CFGetTypeID(value)!=self.cf.CFNumberGetTypeID():return None
        number=C.c_double()
        if not self.cf.CFNumberGetValue(value,13,C.byref(number)):return None  # kCFNumberDoubleType
        return number.value if math.isfinite(number.value) and number.value>0 else None
    def speed_status(self,status,fmt):
        # Use only the status dictionary already obtained for this device.
        # Unknown/absent values stay unknown; never invent a negotiated speed.
        def get(obj,name):
            if not obj or self.cf.CFGetTypeID(obj)!=self.cf.CFDictionaryGetTypeID():return None
            try:return self.get(obj,name)
            except (ValueError,AttributeError):return None
        # Burn uses MediaInfoKey.BurnSpeedsKey; retain the documented top-level
        # location as a fallback for devices/OS versions that expose it there.
        speeds=[];speed_path=None
        media=get(status,'MediaInfoKey')
        for obj,path in ((media,'MediaInfoKey.BurnSpeedsKey'),(status,'BurnSpeedsKey')):
            array=get(obj,'BurnSpeedsKey')
            if array and self.cf.CFGetTypeID(array)==self.cf.CFArrayGetTypeID():
                for index in range(min(256,self.cf.CFArrayGetCount(array))):
                    value=self.speed_number(self.cf.CFArrayGetValueAtIndex(array,index))
                    if value is not None:speeds.append(value)
            if speeds:
                speed_path=path;break
        unit={'dvd':1385.0,'bluray':4496.0}.get(fmt)  # Public DRDeviceBurnSpeed*1x constants.
        speeds=sorted(set(speeds))
        return {'source':'DiscRecording device status','kbps_unit_bytes':1000,
                'supported_kbps':speeds,'supported_x':[value/unit for value in speeds] if unit else [],
                'supported_speed_path':speed_path,
                'maximum_write_kbps':self.speed_number(get(status,'MaximumWriteSpeedKey')),
                'current_write_kbps':self.speed_number(get(status,'CurrentWriteSpeedKey')),
                'sampled_at':_diagnostic_timestamp(),'live_burn_measurement':False}
    def devices(self):
        array=self.dr.DRCopyDeviceArray();result=[]
        if not array:return result
        try:
            for index in range(self.cf.CFArrayGetCount(array)):
                dev=self.cf.CFArrayGetValueAtIndex(array,index)
                info=self.dr.DRDeviceCopyInfo(dev);status=self.dr.DRDeviceCopyStatus(dev)
                try:
                    caps=self.get(info,'WriteCapabilitiesKey');media=self.get(status,'MediaInfoKey')
                    text=lambda key:self.string(self.get(info,key))
                    media_type=self.string(self.get(media,'MediaTypeKey'))
                    kind=self.string(self.get(media,'MediaClassKey'))
                    fmt='dvd' if kind==self.string(self.key('MediaClassDVD')) else 'bluray' if kind==self.string(self.key('MediaClassBD')) else 'unknown'
                    suffixes=('DVDR','DVDRW','DVDRDualLayer','DVDRWDualLayer','DVDPlusR','DVDPlusRW','DVDPlusRDoubleLayer','DVDPlusRWDoubleLayer','BDR','BDRE')
                    writable=any(media_type==self.string(self.key('MediaType'+s)) and self.boolean(caps,'CanWrite'+s+'Key') for s in suffixes)
                    free=self.number(media,'MediaBlocksFreeKey');used=self.number(media,'MediaBlocksUsedKey')
                    # DiscRecording distinguishes a drive that is spinning up from
                    # an empty drive. Keep that state so insert/eject transitions
                    # never become a misleading "insert a blank disc" error.
                    media_state=self.string(self.get(status,'MediaStateKey'))
                    state={self.string(self.key('MediaStateMediaPresent')):'ready',
                           self.string(self.key('MediaStateInTransition')):'probing',
                           self.string(self.key('MediaStateNone')):'absent'}.get(media_state,'unknown')
                    present=state=='ready'
                    busy=self.boolean(status,'IsBusyKey') or state=='probing'
                    vendor=text('VendorNameKey');product=text('ProductNameKey')
                    ident=text('IORegistryEntryPathKey')
                    # hdiutil -device accepts the IOService/OpenFirmware path.
                    if not ident.startswith('IOService:'):continue
                    speed_status=self.speed_status(status,fmt)
                    result.append({'id':ident,'name':(vendor+' '+product).strip() or ident,'vendor':vendor,'product':product,'bus':text('PhysicalInterconnectKey'),'location':text('PhysicalInterconnectLocationKey'),'tray_open':self.optional_boolean(status,'IsTrayOpenKey'),'can_write_dvd':self.boolean(caps,'CanWriteDVDKey'),'can_write_bd':self.boolean(caps,'CanWriteBDKey'),'write_speed_status':speed_status,'media':{'state':state,'present':present,'busy':busy,'type':media_type,'format':fmt,'blank':self.boolean(media,'MediaIsBlankKey'),'writable':writable,'free_bytes':free*2048 if free is not None else None,'capacity_bytes':(free+(used or 0))*2048 if free is not None else None,'speeds':speed_status['supported_x']}})
                finally:
                    if info:self.cf.CFRelease(info)
                    if status:self.cf.CFRelease(status)
        finally:self.cf.CFRelease(array)
        return result

    def control_tray(self,device_id,action,*,timeout=12.0):
        """Use exact IORegistry identity; report command acceptance and observed tray.

        Apple's DRCoreDevice.h documents EjectMedia as safe unmount+eject (or
        OpenTray with no medium), CloseTray as motorized close, and IsValid as
        hot-unplug detection. No drive index or vendor substring is inferred.
        """
        if action not in ('eject','close'):
            raise ValueError('光驱托盘操作无效')
        if not isinstance(device_id,str) or not device_id.startswith('IOService:') or '\0' in device_id:
            raise ValueError('未找到所选光驱')
        name=self.cf.CFStringCreateWithCString(None,device_id.encode('utf8'),0x08000100)
        if not name:raise ValueError('未找到所选光驱')
        dev=None
        try:
            dev=self.dr.DRDeviceCopyDeviceForIORegistryEntryPath(name)
            if not dev or not self.dr.DRDeviceIsValid(dev):raise ValueError('未找到所选光驱')
            info=self.dr.DRDeviceCopyInfo(dev)
            try:
                if not info or self.string(self.get(info,'IORegistryEntryPathKey'))!=device_id:
                    raise ValueError('未找到所选光驱')
            finally:
                if info:self.cf.CFRelease(info)
            def status():
                if not self.dr.DRDeviceIsValid(dev):return {'connected':False,'tray_open':None,'busy':None,'media_present':None}
                snapshot=self.dr.DRDeviceCopyStatus(dev)
                if not snapshot:raise ValueError('无法读取光驱状态')
                try:
                    return {'connected':True,'tray_open':self.optional_boolean(snapshot,'IsTrayOpenKey'),
                            'busy':self.optional_boolean(snapshot,'IsBusyKey'),
                            'media_present':self.string(self.get(snapshot,'MediaStateKey'))==self.string(self.key('MediaStateMediaPresent'))}
                finally:self.cf.CFRelease(snapshot)
            before=status()
            if before['busy'] is not False:raise BurnConflict('光驱正在忙碌')
            operation=self.dr.DRDeviceEjectMedia if action=='eject' else self.dr.DRDeviceCloseTray
            code=int(operation(dev))
            expected=action=='eject';deadline=time.monotonic()+max(0,min(30.0,float(timeout)))
            while True:
                observed=status()
                confirmed=code==0 and observed['connected'] and observed['tray_open'] is expected
                if code!=0 or confirmed or not observed['connected'] or time.monotonic()>=deadline:break
                time.sleep(.2)
            return {'ok':code==0 and observed['connected'],'action':action,'device_id':device_id,
                    'os_status':code,'confirmed':confirmed,**observed}
        finally:
            if dev:self.cf.CFRelease(dev)
            self.cf.CFRelease(name)


def control_tray(device_id,action):
    """Explicit non-recording hardware operation, normally protected by BurnManager."""
    return DiscRecording().control_tray(device_id,action)


def detect_devices():
    try:return {'devices':DiscRecording().devices(),'error':None}
    except (OSError,ValueError,AttributeError) as exc:return {'devices':[],'error':str(exc)}


def media_readiness_error(media):
    """Reject transient/unknown states before checking properties of a medium."""
    # Legacy injected providers may omit state; the native provider always
    # reports it explicitly, including unknown statuses that fail closed.
    state=media.get('state','ready' if media.get('present') else 'absent')
    if state=='probing':return '正在识别光盘，请稍候'
    if media.get('busy'):return '光驱正在忙碌'
    if state not in ('ready','absent'):return '无法确认光盘状态，请重新检测'
    if not media.get('present') or state=='absent':return '请插入空白光盘'
    return None


def verification_option(value):
    if type(value) is not bool:raise ValueError('烧录后校验选项必须是布尔值')
    return value


def burn_command(image,device,speed=None,verify_after_burn=True):
    verification_option(verify_after_burn)
    command=['/usr/bin/hdiutil','burn',image,'-device',device,'-puppetstrings','-verifyburn' if verify_after_burn else '-noverifyburn','-noeject','-noaddpmap','-nooptimizeimage','-noskipfinalfree','-forceclose','-nosynthesize']
    if speed is not None:command+=['-speed',str(speed)]
    return command


# hdiutil -puppetstrings keeps only MESSAGE/PERCENT keys language-neutral.
# Its values use CoreFoundation's system language, even with LC_ALL=C.
_BURN_STAGES = {
    'Preparing data for burn': 'preparing', 'Preparing data for burning': 'preparing',
    'Preparing': 'preparing', 'Opening session': 'preparing', 'Opening track': 'preparing',
    'Writing track': 'burning', 'Writing': 'burning', 'Burning': 'burning', 'Burn started': 'burning',
    'Closing session': 'finishing', 'Closing track': 'finishing', 'Finishing burn': 'finishing',
    'Finishing': 'finishing', 'Finalizing': 'finishing',
    'Verifying': 'verifying', 'Verifying burn\\U2026': 'verifying',
    'Verifying burn': 'verifying', 'Verifying disc': 'verifying',
    'Burn completed successfully': 'finishing',
}
_BURN_FAILURES = {'Burn failed', 'Burn was canceled.', 'Verify was canceled.',
                  'Verifying the burned data failed.'}
_BURN_FALLBACKS = {
    'Preparing data for burn': ('ディスクに書き込むデータを準備中', '正在准备用于刻录的数据', '正在为刻录准备数据', '正在準備燒錄的資料'),
    'Opening session': ('セッションを開いています', '正在打开会话', '正在打開區段'),
    'Opening track': ('トラックを開いています', '正在打开轨道', '正在打開軌道'),
    'Writing track': ('トラックを書き込み中', '正在写入轨道', '正在寫入軌道'),
    'Closing track': ('トラックを閉じています', '正在关闭轨道', '正在關閉軌道'),
    'Closing session': ('セッションを閉じています', '正在关闭会话', '正在關閉區段'),
    'Finishing burn': ('ディスクの作成を終了中', '即将完成刻录', '即將完成燒錄'),
    'Verifying': ('検証中', '正在验证', '驗證中', '正在驗證'),
    'Verifying burn': ('ディスク作成の検証中', '正在验证刻录', '正在驗證燒錄'),
    'Verifying disc': ('ディスクを検証中', '正在验证光盘', '正在驗證光碟'),
    'Burn completed successfully': ('ディスク作成が完了しました', '刻录已成功完成', '成功完成燒錄'),
    'Burn failed': ('ディスクを作成できませんでした', '刻录失败', '燒錄失敗'),
    'Burn was canceled.': ('ディスクの作成がキャンセルされました', '刻录被取消', '已取消燒錄'),
    'Verify was canceled.': ('検証はキャンセルされました', '验证被取消', '取消驗證'),
    'Verifying the burned data failed.': ('書き込み済みデータの検証に失敗しました', '验证已刻录的数据失败', '驗證已燒錄的資料失敗'),
}


def _message_normalize(message):
    return message.strip().rstrip('.…⋯。').casefold()


@lru_cache(maxsize=1)
def _burn_message_names():
    names = {_message_normalize(key): key for key in set(_BURN_STAGES) | _BURN_FAILURES}
    for key, values in _BURN_FALLBACKS.items():
        for value in values: names[_message_normalize(value)] = key
    # Read Apple's actual message values on this OS, so a system language other
    # than the app's three interface languages is also handled when available.
    tables = (
        '/System/Library/PrivateFrameworks/DiskImages.framework/Versions/A/Resources/CDiskImage.loctable',
        '/System/Library/Frameworks/DiscRecording.framework/Versions/A/Resources/OSErrors.loctable',
    )
    for path in tables:
        try:
            data = plistlib.loads(Path(path).read_bytes())
            for translations in data.values():
                if not isinstance(translations, dict): continue
                for key in set(_BURN_STAGES) | _BURN_FAILURES:
                    value = translations.get(key)
                    if isinstance(value, str): names[_message_normalize(value)] = key
        except (OSError, ValueError, plistlib.InvalidFileException):
            pass
    return names


def burn_message_key(line):
    text = line.strip()
    if text.startswith('MESSAGE:'): text = text[len('MESSAGE:'):]
    # Full known messages only: an option such as -verifyburn or a diagnostic
    # containing "verification failed" is not evidence of a verification phase.
    return _burn_message_names().get(_message_normalize(text))


def parse_progress(line,current):
    """Use real localized phase events and measured percentage values only."""
    stage = _BURN_STAGES.get(burn_message_key(line), current)
    percent=None
    match=re.search(r'PERCENT:\s*([-+]?\d+(?:\.\d+)?)',line)
    if match:
        value=float(match.group(1))
        if math.isfinite(value) and 0<=value<=100:percent=value
    return stage,percent


def _diagnostic_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


class BurnDiagnostics:
    """Passive observations only. These numbers never determine burn success."""
    def __init__(self,image_bytes,requested_speed,device,clock=None,timestamp=None):
        self.clock=clock or time.monotonic;self.timestamp=timestamp or _diagnostic_timestamp
        self.started=self.clock();self.started_at=self.timestamp();self.finished=None
        self.image_bytes=image_bytes;self.requested_speed=requested_speed
        self.device_status=copy.deepcopy(device.get('write_speed_status'))
        self.segments=[];self.stage='preparing';self._begin(self.stage,0,self.started_at)
        self.completed=False
    def _begin(self,stage,elapsed,at):
        self.segments.append({'stage':stage,'started_at':at,'start_elapsed_seconds':elapsed,
                              'ended_at':None,'end_elapsed_seconds':None,'end_observed_by':None,
                              'last_progress_percent':None})
    def _end(self,elapsed,at,reason):
        self.segments[-1].update(ended_at=at,end_elapsed_seconds=elapsed,end_observed_by=reason)
    def observe(self,line):
        stage,percent=parse_progress(line,self.stage)
        has_percent=bool(re.fullmatch(r'PERCENT:\s*[-+]?\d+(?:\.\d+)?\s*',line.strip()))
        if stage==self.stage and not has_percent:return None
        elapsed=max(0.0,self.clock()-self.started);at=self.timestamp()
        transition=stage!=self.stage
        if transition:
            self._end(elapsed,at,'phase_transition');self.stage=stage;self._begin(stage,elapsed,at)
        if has_percent:self.segments[-1]['last_progress_percent']=percent
        return {'observed_at':at,'elapsed_seconds':round(elapsed,3),'stage':stage,
                'phase_transition':transition,'stage_progress_percent':percent}
    def finish(self,completed=False):
        if self.finished is None:
            self.finished=max(0.0,self.clock()-self.started)
            self._end(self.finished,self.timestamp(),'process_exit')
        self.completed=bool(completed)
        return self.snapshot()
    def snapshot(self):
        elapsed=self.finished if self.finished is not None else max(0.0,self.clock()-self.started)
        segments=copy.deepcopy(self.segments);totals={}
        for segment in segments:
            end=segment['end_elapsed_seconds']
            duration=max(0.0,(elapsed if end is None else end)-segment['start_elapsed_seconds'])
            segment['elapsed_seconds']=round(duration,3)
            totals[segment['stage']]=round(totals.get(segment['stage'],0.0)+duration,3)
        estimate=None
        writing=[s for s in self.segments if s['stage']=='burning']
        # Entire payload / measured write-phase wall time is not negotiated
        # laser speed. Never compute it from incomplete/cancelled writes or
        # from a phase whose end was only inferred when the process exited.
        if self.completed and writing and all(s['end_observed_by']=='phase_transition' for s in writing):
            seconds=sum(s['end_elapsed_seconds']-s['start_elapsed_seconds'] for s in writing)
            if seconds>0:
                estimate={'kind':'payload_bytes_per_write_phase_second_estimate',
                          'bytes_per_second':round(self.image_bytes/seconds,3),
                          'write_phase_seconds':round(seconds,3),'not_negotiated_speed':True}
        return {'started_at':self.started_at,'elapsed_seconds':round(elapsed,3),
                'image_bytes':self.image_bytes,'requested_speed_x':self.requested_speed,
                'requested_speed_policy':'max' if self.requested_speed is None else 'explicit',
                'device_status_before_burn':copy.deepcopy(self.device_status),
                'stage_seconds':totals,'stage_timings':segments,'write_payload_average_estimate':estimate,
                'timing_source':'monotonic clock at received hdiutil phase/progress events'}


class BurnReceipt:
    """Language-independent evidence; observation alone never marks a burn good."""
    def __init__(self):
        self.verification_seen=False;self.success_message=False;self.failure_message=False
    def feed(self,line):
        key=burn_message_key(line)
        if _BURN_STAGES.get(key)=='verifying':self.verification_seen=True
        if key=='Burn completed successfully':self.success_message=True
        if key in _BURN_FAILURES:self.failure_message=True
    def result(self,exit_code,cancelled=False,verification_required=True):
        verification_option(verification_required)
        completed=exit_code==0 and self.success_message and not self.failure_message and not cancelled
        return {'exit_code':exit_code,'verification_seen':self.verification_seen,
                'success_message':self.success_message,'failure_message':self.failure_message,
                'cancelled':bool(cancelled),'verification_required':verification_required,
                'verification_skipped':not verification_required,
                'complete':completed and (self.verification_seen or not verification_required),
                'verified':completed and self.verification_seen and verification_required}


def inspect_burn_receipt(log_text,exit_code,cancelled=False,verification_required=True):
    """Read-only receipt reconstruction; never modifies state or repeats a burn.

    The caller must independently establish the actual process exit code. Old
    logs without explicit verification + completion messages are inconclusive.
    """
    receipt=BurnReceipt()
    for line in log_text.splitlines():receipt.feed(line)
    return receipt.result(exit_code,cancelled,verification_required)


class BurnManager:
    def __init__(self,root,devices=detect_devices,inspector=inspect_iso,popen=subprocess.Popen,tray_controller=None):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'burn.json';self.lockpath=self.root/'burn.lock'
        self.devices=devices;self.inspector=inspector;self.popen=popen
        self.tray_controller=tray_controller or control_tray
        self.lock=threading.RLock();self.job=None;self.process=None;self.thread=None
        self.fd=None;self.cancel_requested=False;self.closing=False
    def _persist(self):
        temp=self.path.with_suffix('.tmp-'+uuid.uuid4().hex)
        try:
            with open(temp,'w') as f:
                json.dump(self.job,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
            os.replace(temp,self.path)
        finally:
            try:temp.unlink()
            except FileNotFoundError:pass
    def _read(self):
        try:return json.loads(self.path.read_text())
        except (OSError,ValueError):return None
    def _try_lock(self):
        fd=open(self.lockpath,'a+')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:fd.close();return None
        return fd
    def snapshot(self):
        with self.lock:
            if self.fd is not None:return {'active':True,'job':copy.deepcopy(self.job)}
            # A replacement helper reads the old helper's persistent job state.
            fd=self._try_lock();external=fd is None
            if fd is not None:fd.close()
            job=self._read()
            if not external and self.job and (not job or job.get('id')==self.job.get('id')):
                job=copy.deepcopy(self.job)
            if job and job.get('status') in ACTIVE and not external:
                job.update(status='failed',stage='failed',progress=None,stage_progress=None,verified=False,error='烧录进程已中断，盘片状态未知',message='烧录进程已中断，盘片状态未知')
            return {'active':external,'job':job}
    def control_tray(self,device_id,action,*,direct_active=False):
        """Reserve both workflows before an exact-device eject/close operation.

        Service callers also hold the authoring lock, and pass the current
        DirectBurn snapshot. File locks cover another/replacement backend.
        """
        if action not in ('eject','close'):raise ValueError('光驱托盘操作无效')
        with self.lock:
            if self.closing:raise BurnConflict('程序正在退出')
            if direct_active:raise BurnConflict('已有视频直刻任务运行中')
            if self.fd is not None:raise BurnConflict('已有烧录任务运行中')
            fd=self._try_lock()
            if fd is None:raise BurnConflict('已有烧录任务运行中')
            direct_fd=None
            try:
                direct_fd=open(self.root/'direct-burn.lock','a+')
                try:fcntl.flock(direct_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:raise BurnConflict('已有视频直刻任务运行中') from None
                detection=self.devices()
                if detection.get('error'):raise ValueError(detection['error'])
                selected=next((d for d in detection.get('devices',[]) if d['id']==device_id),None)
                if not selected:raise ValueError('未找到所选光驱')
                if selected.get('media',{}).get('busy') is not False:raise BurnConflict('光驱正在忙碌')
                result=self.tray_controller(device_id,action)
                if result.get('device_id')!=device_id or result.get('action')!=action:
                    raise ValueError('光驱托盘操作返回了不匹配的设备')
                refreshed=self.devices()
                result['device']=next((d for d in refreshed.get('devices',[]) if d['id']==device_id),None)
                if refreshed.get('error'):result['refresh_error']=refreshed['error']
                return result
            finally:
                if direct_fd:direct_fd.close()
                fd.close()

    def check(self,iso,device_id,speed=None,verify_after_burn=True):
        errors=[];image=None;device=None
        try:verification_option(verify_after_burn)
        except ValueError as exc:errors.append(str(exc))
        try:image=self.inspector(iso)
        except Exception as exc:errors.append(str(exc))
        detection=self.devices()
        if detection.get('error'):errors.append(detection['error'])
        device=next((d for d in detection['devices'] if d['id']==device_id),None)
        if not device:errors.append('未找到所选光驱')
        else:
            media=device['media']
            readiness_error=media_readiness_error(media)
            if readiness_error:errors.append(readiness_error)
            elif not media['blank']:errors.append('盘片不是空白盘，不会自动擦除')
            elif not media['writable']:errors.append('光驱不能写入此盘片')
            if image:
                if not device['can_write_dvd' if image['format']=='dvd' else 'can_write_bd']:errors.append('光驱不支持此类型烧录')
                if not readiness_error:
                    if image['format']!=media['format']:errors.append('ISO 类型与盘片类型不匹配')
                    free=media.get('free_bytes')
                    if free is None:errors.append('无法确认盘片可用容量')
                    elif image['required_bytes']>free:errors.append('盘片容量不足')
        if speed is not None and (isinstance(speed,bool) or not isinstance(speed,(float,int)) or not math.isfinite(speed) or not 0<speed<=128):errors.append('烧录速度无效')
        return {'valid':not errors,'errors':errors,'image':image,'device':device,
                'verification_required':verify_after_burn if type(verify_after_burn) is bool else None}
    def start(self,iso,device_id,speed=None,verify_after_burn=True):
        verification_option(verify_after_burn)
        with self.lock:
            if self.closing:raise BurnConflict('程序正在退出')
            if self.fd is not None:raise BurnConflict('已有烧录任务运行中')
            fd=self._try_lock()
            if fd is None:raise BurnConflict('已有烧录任务运行中')
            try:
                check=self.check(iso,device_id,speed,verify_after_burn)
                if not check['valid']:raise ValueError('\n'.join(check['errors']))
                image=check['image'];device=check['device'];stamp=fingerprint(image['path'])
                ident=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
                log=self.root/('burn-'+ident+'.log')
                # Prove both journal and log writable before any hardware write.
                with open(log,'x') as stream:stream.write('DiscAuthor image burn; verify_after_burn='+str(verify_after_burn).lower()+'\n')
                self.diagnostics=BurnDiagnostics(image['size_bytes'],speed,device)
                self.job={'id':ident,'status':'preparing','stage':'preparing','progress':None,'stage_progress':None,'message':'准备烧录','error':None,'iso':image['path'],'device_id':device['id'],'device_name':device['name'],'format':image['format'],'verified':False,'verify_after_burn':verify_after_burn,'verification_required':verify_after_burn,'verification_skipped':not verify_after_burn,'log_path':str(log),'started_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'diagnostics':self.diagnostics.snapshot()}
                self._persist();self.fd=fd;self.cancel_requested=False
                self.thread=threading.Thread(target=self._run,args=(image,device,speed,stamp,verify_after_burn),daemon=False)
                self.thread.start()
            except Exception:
                self.fd=None;fd.close();raise
            return {'active':True,'job':copy.deepcopy(self.job)}
    def _publish(self,**changes):
        self.job.update(changes)
        try:self._persist()
        except OSError as exc:
            # A failed journal must never interrupt the drive's safe close.
            self.job['error']='烧录状态保存失败：'+str(exc)
    def _run(self,image,device,speed,stamp,verify_after_burn=True):
        result='failed';error=None;verified=False;receipt=BurnReceipt();evidence=None
        try:
            with open(self.job['log_path'],'a',buffering=1) as log:
                # Re-query after reservation: changing a disc after check cannot bypass it.
                check=self.check(image['path'],device['id'],speed,verify_after_burn)
                if not check['valid']:raise ValueError('\n'.join(check['errors']))
                if fingerprint(image['path'])!=stamp:raise ValueError('ISO 文件在检查期间发生变化')
                self.diagnostics.device_status=copy.deepcopy(check['device'].get('write_speed_status'))
                with self.lock:
                    if self.cancel_requested:result='cancelled';return
                    command=burn_command(image['path'],device['id'],speed,verify_after_burn)
                    log.write(json.dumps(command,ensure_ascii=False)+'\n')
                    log.write('DISCAUTHOR_BURN_DIAGNOSTICS:'+json.dumps(self.diagnostics.snapshot(),ensure_ascii=False)+'\n')
                    self.process=self.popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1,env=dict(os.environ,LC_ALL='C',LANG='C'),start_new_session=True,pass_fds=(self.fd.fileno(),))
                for line in self.process.stdout:
                    try:log.write(line)
                    except OSError:pass
                    with self.lock:
                        stage,percent=parse_progress(line,self.job['stage'])
                        receipt.feed(line)
                        observation=self.diagnostics.observe(line)
                        if observation:
                            try:log.write('DISCAUTHOR_BURN_OBSERVATION:'+json.dumps(observation,ensure_ascii=False)+'\n')
                            except OSError:pass
                        if not self.cancel_requested:
                            changes={}
                            if stage!=self.job['stage']:changes.update(stage=stage,status=stage,stage_progress=None,progress=None)
                            if percent is not None:changes.update(stage_progress=percent,progress=percent)
                            elif re.search(r'PERCENT:\s*-1(?:\.0+)?(?:\s|$)',line):changes.update(stage_progress=None,progress=None)
                            if changes.get('stage'):changes['message']={'preparing':'准备烧录','burning':'正在烧录','verifying':'正在校验','finishing':'正在封盘'}[stage]
                            if changes:self._publish(**changes,diagnostics=self.diagnostics.snapshot())
                code=self.process.wait()
                evidence=receipt.result(code,self.cancel_requested,verify_after_burn)
                evidence['diagnostics']=self.diagnostics.finish(evidence['complete'])
                verified=evidence['verified']
                try:
                    log.write('DISCAUTHOR_BURN_RECEIPT:'+json.dumps(evidence,sort_keys=True)+'\n')
                    log.flush();os.fsync(log.fileno())
                except OSError:pass
                if self.cancel_requested:result='cancelled';error='烧录已取消，盘片可能无法使用'
                elif code!=0:error='烧录失败，请查看日志'
                elif not evidence['complete']:error='未收到烧录校验结果，请检查盘片' if verify_after_burn else '未收到烧录完成结果，请检查盘片'
                else:result='complete'
        except Exception as exc:error=str(exc)
        finally:
            # An output parser/log failure is not evidence that the drive has
            # stopped. Keep its reservation and active state until real exit.
            # Drain remaining pipe data when possible so hdiutil cannot block
            # on a full output pipe while safely closing the disc.
            if self.process is not None:
                try:
                    if self.process.poll() is None:
                        try:
                            while self.process.stdout.read(65536):pass
                        except Exception:pass
                    self.process.wait()
                except Exception:
                    while self.process.poll() is None:time.sleep(.1)
            with self.lock:
                self.process=None
                diagnostics=self.diagnostics.finish(result=='complete')
                self._publish(receipt=evidence,diagnostics=diagnostics,status=result,stage=result,progress=100.0 if result=='complete' else None,stage_progress=100.0 if result=='complete' else None,verified=result=='complete' and verified,error=error,message={'complete':'烧录并校验完成' if verified else '烧录完成，已跳过校验','cancelled':'烧录已取消，盘片可能无法使用','failed':'烧录失败，请查看日志'}[result])
                if self.fd:self.fd.close();self.fd=None
    def cancel(self):
        with self.lock:
            if self.fd is None:
                if self.snapshot()['active']:raise BurnConflict('烧录由另一进程保护中，请等待完成')
                return self.snapshot()
            self.cancel_requested=True;self._publish(status='cancelling',stage='cancelling',progress=None,stage_progress=None,message='正在取消烧录，请等待光驱结束')
            if self.process:
                try:self.process.send_signal(signal.SIGINT)
                except ProcessLookupError:pass
            return self.snapshot()
    def log(self):
        with self.lock:
            job=self.job if self.fd else self._read()
            if not job:return ''
            path=Path(job.get('log_path',''))
            if path.parent.resolve()!=self.root.resolve():return ''
            try:
                with open(path,'rb') as f:f.seek(max(0,path.stat().st_size-64000));return f.read().decode('utf8','replace')
            except OSError:return ''
    def shutdown(self,wait=True):
        """Refuse new work and wait; never signal the writing process on app exit."""
        with self.lock:self.closing=True;thread=self.thread
        if wait and thread and thread is not threading.current_thread():thread.join()
