"""HDMV menu authoring. Binary layout verified against VideoLAN libbluray parsers."""
import struct, shutil
from pathlib import Path
import menu_feedback

def u16(x): return struct.pack('>H',x)
def u24(x): return x.to_bytes(3,'big')
def u32(x): return struct.pack('>I',x)
def cmd(op, dst=0, src=0): return bytes.fromhex(op)+u32(dst)+u32(src)
def jump_obj(n): return cmd('21800000',n)
def jump_obj_register(register): return cmd('21000000',register)
def jump_title(n): return cmd('21810000',n)
def play(n): return cmd('22800000',n)
def set_stream(): return cmd('51c00001',0x80010000,0x80010000)
def move_immediate(register,value): return cmd('50400001',register,value)
def select_saved_button(): return cmd('51400003',0x80000000,0x80000000)

def segment(kind, data, pts=45000):
    return b'PG'+u32(pts)+u32(0)+bytes([kind])+u16(len(data))+data

def rle(im):
    data=bytearray()
    for y in range(im.height):
        row=list(im.crop((0,y,im.width,y+1)).getdata()); x=0
        while x<len(row):
            c=row[x]; end=x+1
            while end<len(row) and row[end]==c and end-x<16383: end+=1
            n=end-x
            if n==1 and c: data.append(c)
            else:
                data.append(0)
                if n<64: data.append(n | (0x80 if c else 0))
                else: data.extend([0x40|(0x80 if c else 0)|(n>>8), n&255])
                if c: data.append(c)
            x=end
        data.extend(b'\0\0')
    return bytes(data)

def neighbours(buttons, i):
    if 'nav' in buttons[i]:return buttons[i]['nav']
    # Horizontal: row-major cycle; vertical: column-major cycle.
    horizontal=sorted(range(len(buttons)),key=lambda j:(buttons[j]['rect'][1],buttons[j]['rect'][0]))
    vertical=sorted(range(len(buttons)),key=lambda j:(buttons[j]['rect'][0],buttons[j]['rect'][1]))
    h=horizontal.index(i);v=vertical.index(i)
    return [vertical[(v-1)%len(vertical)]+1,vertical[(v+1)%len(vertical)]+1,horizontal[(h-1)%len(horizontal)]+1,horizontal[(h+1)%len(horizontal)]+1]

def write_igs(path,buttons,config=None):
    # One always-on, multiplexed interactive composition. One BOG per visible button.
    bogs=b''; objects=[]
    for i,b in enumerate(buttons):
        x,y,w,h=b['rect']; ids=[i*3+s for s in range(3)]
        for state,oid in enumerate(ids):
            im=menu_feedback.button_indexed(b,menu_feedback.STATES[state],config)
            encoded=rle(im); raw=u16(oid)+b'\0\xc0'+u24(len(encoded)+4)+u16(w)+u16(h)+encoded
            if len(raw)>65535: raise ValueError('Menu object exceeds one ODS segment')
            objects.append(segment(0x15,raw))
        nav=neighbours(buttons,i)
        action=jump_title(b['title']) if 'title' in b else jump_obj(b['page']+1)
        commands=[action]
        if 'page' in b:
            settings=config or {};per_page=settings.get('per_page',4)
            remaining=len(settings.get('videos',[]))-b['page']*per_page
            requested=settings.get('first_button',1)
            first=requested if requested<=min(per_page,remaining) else 1
            commands=[move_immediate(0,first),action]
        button=u16(i+1)+u16(i+1)+b'\0'+u16(x)+u16(y)+b''.join(u16(n) for n in nav)
        button+=u16(ids[0])+u16(ids[0])+b'\0'
        button+=b'\xff'+u16(ids[1])+u16(ids[1])+b'\0'
        button+=b'\xff'+u16(ids[2])+u16(ids[2])+u16(len(commands))+b''.join(commands)
        bogs+=u16(i+1)+b'\x01'+button
    page=b'\0\0'+bytes(8)+bytes(4)+b'\0'+u16(0xffff)+u16(0xffff)+b'\0'+bytes([len(buttons)])+bogs
    # The IG shares the primary M2TS with video/audio: stream_model=0,
    # ui_model=0 (always-on). Multiplexed ICs include two 33-bit timeouts,
    # each preceded by seven reserved bits. Zero keeps this still menu active;
    # see tests/fixtures/bd_ig_reference.json for an independent muxed sample.
    ic=b'\0'+bytes(10)+u24(0)+b'\x01'+page
    ics=u16(1920)+u16(1080)+b'\x10'+u16(0)+b'\x80\xc0'+u24(len(ic))+ic
    palette=menu_feedback.bd_palette(config)
    path.write_bytes(segment(0x18,ics)+segment(0x14,palette)+b''.join(objects)+segment(0x80,b''))

def write_return_igs(path, menu_page=0, *, frame_rate_code=1):
    """Write a hidden title popup whose opening invokes the disc Top Menu.

    The current menu page and selection are saved by navigation() in GPR1 and
    GPR0. JumpTitle(0) enters the indexed Top Menu dispatcher, including when
    playback started through title search. menu_page remains accepted for
    callers preparing one stream per page; the dispatcher chooses the page.

    A popup is not rendered or auto-activated while hidden. On popup-on, its
    default-selected auto-action button executes once (libbluray's
    GC_CTRL_POPUP -> _select_page -> _render_page). The only graphics object
    and palette entry are transparent, including the activation state.
    """
    if type(menu_page) is not int or not 0 <= menu_page < 65534:
        raise ValueError('Invalid Blu-ray menu page')
    if frame_rate_code not in (1, 2, 3, 4, 6, 7):
        raise ValueError('Invalid Blu-ray popup frame rate')
    # Button 1: automatic action, default selection, self-neighbours, no sound.
    button = u16(1) + u16(1) + b'\x80' + u16(0) + u16(0) + u16(1) * 4
    button += u16(0) + u16(0) + b'\0'  # normal object
    button += b'\xff' + u16(0) + u16(0) + b'\0'  # selected object
    button += b'\xff' + u16(0) + u16(0) + u16(1) + jump_title(0)
    bog = u16(1) + b'\x01' + button
    page = b'\0\0' + bytes(8) + bytes(4) + b'\0' + u16(1) + u16(0xffff) + b'\0\x01' + bog
    # Multiplexed (stream_model=0), popup (ui_model=1), no user timeout.
    ic = b'\x40' + bytes(10) + u24(0) + b'\x01' + page
    ics = u16(1920) + u16(1080) + bytes([frame_rate_code << 4]) + u16(0) + b'\x80\xc0' + u24(len(ic)) + ic
    palette = b'\0\0\0\x10\x80\x80\0'  # id/version, index0: Y/Cr/Cb/alpha
    pixels = b'\0\x02\0\0' * 2  # two transparent pixels, EOL; two rows
    obj = u16(0) + b'\0\xc0' + u24(len(pixels) + 4) + u16(2) + u16(2) + pixels
    # Unlike the static menu, the title popup should be available from the
    # clip's beginning. A half-second PTS delays its packets until well after
    # movie playback starts, so an early seek can skip the initial display set.
    path.write_bytes(segment(0x18, ics, pts=0) + segment(0x14, palette, pts=0)
                     + segment(0x15, obj, pts=0) + segment(0x80, b'', pts=0))

def navigation(root, pages, titles, per_page=4, config=None, *, first_play_mode):
    # Index titles map to dedicated movie objects. An absent top menu has the
    # explicit HDMV object reference 0xffff, not a playable dummy object.
    def entry(obj,interactive=False): return u32(0x40000000)+u16(0x4000 if interactive else 0)+u16(obj)+bytes(4)
    # AppInfo describes the first video the player presents, which is the menu
    # when enabled and otherwise the first title's encoded output (not its source).
    video_rates={'1080p24':0x61,'1080p24_exact':0x62,'1080i60':0x44,'1080i50':0x43}
    if first_play_mode not in video_rates:
        raise ValueError('Unknown Blu-ray first-play output mode: '+str(first_play_mode))
    settings=config or {};menu_enabled=settings.get('menu_enabled',True)
    if menu_enabled:
        # Append the dispatcher so existing title -> object mappings stay stable.
        top_menu_object=pages+titles+1
        index=entry(0,True)+entry(top_menu_object,True)+u16(titles)+b''.join(entry(pages+1+i) for i in range(titles))
    else:
        index=entry(0)+entry(0xffff)+u16(titles)+b''.join(entry(1+i) for i in range(titles))
    header=b'INDX0200'+u32(78)+u32(0)+bytes(24)
    app=u32(34)+bytes([0,video_rates[first_play_mode]])+bytes(32)
    (root/'BDMV/index.bdmv').write_bytes(header+app+u32(len(index))+index)
    if menu_enabled:
        first=settings.get('first_button',1);after=settings.get('after_play','menu')
        initial=first if first<=min(titles,per_page) else 1
        objs=[[move_immediate(0,initial),move_immediate(1,1),jump_title(0)]]
        for i in range(pages): objs.append([move_immediate(1,i+1),set_stream(),select_saved_button(),play(i),jump_obj(i+1)])
        for i in range(titles):
            page=i//per_page;slot=i%per_page+1
            default=first if first<=min(titles-page*per_page,per_page) else 1
            selected=slot if settings.get('remember_selection',True) else default
            if after in ('next','loop') and i+1<titles:post=jump_title(i+2)
            elif after=='loop':post=jump_title(1)
            else:post=jump_title(0)
            objs.append([move_immediate(1,page+1),move_immediate(0,selected),set_stream(),play(pages+i),post])
        # JumpObject with an indirect operand reads GPR1; unlike a direct jump
        # from a title's popup, entering through JumpTitle(0) also sets PSR4 to
        # Top Menu. Standard ROOT_MENU and popup-on share this same route.
        objs.append([jump_obj_register(1)])
    else:
        objs=[[jump_title(1)]]
        for i in range(titles):
            commands=[set_stream(),play(i)]
            if i+1<titles:commands.append(jump_title(i+2))
            elif settings.get('no_menu_end_action','stop')=='loop':commands.append(jump_title(1))
            # End the last movie object to stop. TerminatePL is only legal in
            # interactive graphics commands and must not be used here.
            objs.append(commands)
    data=bytes(4)+u16(len(objs))
    # Mask menu calls when no top menu exists. Players must not suspend the
    # current title to attempt an unavailable menu and lose its post commands.
    flags=0 if menu_enabled else 0x4000
    for obj in objs: data+=u16(flags)+u16(len(obj))+b''.join(obj)
    (root/'BDMV/MovieObject.bdmv').write_bytes(b'MOBJ0200'+bytes(32)+u32(len(data))+data)
    for name in ['index.bdmv','MovieObject.bdmv']:
        shutil.copy2(root/'BDMV'/name,root/'BDMV/BACKUP'/name)

def set_still(path):
    data=bytearray(path.read_bytes()); off=int.from_bytes(data[8:12],'big')
    # PlayList length/reserved/counts = 10, PlayItem length=2, still flag after 29 body bytes.
    data[off+10+2+29]=2
    path.write_bytes(data)
