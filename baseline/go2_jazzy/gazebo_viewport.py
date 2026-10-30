"""Host the existing Gazebo X11 view, without rendering or capturing a second one."""
import ctypes as C
import ctypes.util
from pathlib import Path


class GazeboViewport:
    def __init__(self):
        self.x = C.CDLL(ctypes.util.find_library('X11') or 'libX11.so.6')
        D, W, I, U = C.c_void_p, C.c_ulong, C.c_int, C.c_uint
        signatures = {
            'XOpenDisplay': ([C.c_char_p], D), 'XDefaultRootWindow': ([D], W),
            'XInternAtom': ([D,C.c_char_p,I], W),
            'XGetWindowProperty': ([D,W,W,C.c_long,C.c_long,I,W,C.POINTER(W),C.POINTER(I),C.POINTER(W),C.POINTER(W),C.POINTER(C.c_void_p)], I),
            'XFree': ([C.c_void_p], I), 'XSync': ([D,I], I),
            'XReparentWindow': ([D,W,W,I,I], I), 'XMoveResizeWindow': ([D,W,I,I,U,U], I),
            'XMapWindow': ([D,W], I), 'XUnmapWindow': ([D,W], I),
            'XAddToSaveSet': ([D,W], I), 'XRemoveFromSaveSet': ([D,W], I),
            'XCloseDisplay': ([D], I), 'XSetErrorHandler': ([C.c_void_p], C.c_void_p),
            'XQueryTree': ([D,W,C.POINTER(W),C.POINTER(W),C.POINTER(D),C.POINTER(U)], I),
        }
        for name,(args,result) in signatures.items():
            function=getattr(self.x,name)
            function.argtypes,function.restype=args,result
        self.display=self.x.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError('当前桌面没有可用的X11显示服务')
        self.root=self.x.XDefaultRootWindow(self.display)
        self.window=None
        self.size=None
        self.errors=[]
        callback=C.CFUNCTYPE(I,D,C.c_void_p)
        self.previous=None
        def error(display,event):
            if display == self.display:
                self.errors.append('X11窗口已关闭或尚未就绪')
                return 0
            if self.previous:
                return callback(self.previous)(display,event)
            return 0
        self.handler=callback(error)
        self.previous=self.x.XSetErrorHandler(C.cast(self.handler,C.c_void_p))

    def call(self,name,*args):
        self.errors.clear()
        value=getattr(self.x,name)(self.display,*args)
        self.x.XSync(self.display,False)
        if self.errors:
            raise RuntimeError(self.errors[-1])
        return value

    def property(self,window,name):
        atom=self.x.XInternAtom(self.display,name.encode(),False)
        actual,fmt,count,remaining=C.c_ulong(),C.c_int(),C.c_ulong(),C.c_ulong()
        data=C.c_void_p()
        result=self.call('XGetWindowProperty',window,atom,0,65536,False,0,
                         C.byref(actual),C.byref(fmt),C.byref(count),C.byref(remaining),C.byref(data))
        if result or not data.value:
            return None
        try:
            if fmt.value==32:
                return list(C.cast(data,C.POINTER(C.c_ulong))[:count.value])
            if fmt.value==8:
                return C.string_at(data,count.value).decode('utf8',errors='replace')
        finally:
            self.x.XFree(data)
        return None

    def candidates(self):
        matches=[]
        for window in self.property(self.root,'_NET_CLIENT_LIST') or []:
            try:
                title=self.property(window,'_NET_WM_NAME') or self.property(window,'WM_NAME')
                pids=self.property(window,'_NET_WM_PID') or []
                if title!='Gazebo Sim' or len(pids)!=1:
                    continue
                command=Path(f'/proc/{pids[0]}/cmdline').read_bytes().replace(b'\0',b' ').strip()
                if command.startswith(b'gz sim gui'):
                    matches.append(window)
            except (OSError,RuntimeError):
                continue
        return matches

    def attach(self,parent,width,height):
        if self.window is not None:
            try:
                if not self.property(self.window,'_NET_WM_PID'):
                    self.detach()
            except RuntimeError:
                self.detach()
        if self.window is None:
            matches=self.candidates()
            if len(matches)>1:
                raise RuntimeError('发现多个Gazebo画面，请先关闭多余的仿真实例')
            if not matches:
                return False
            window=matches[0]
            self.call('XAddToSaveSet',window)
            self.window=window
            try:
                self.call('XUnmapWindow',window)
                self.call('XReparentWindow',window,parent,0,0)
                self.call('XMapWindow',window)
            except RuntimeError:
                self.detach()
                raise
        else:
            # Qt may finish a late show/window-state transition after the
            # first embedding. Verify actual ownership rather than trusting
            # the cached window ID and dimensions.
            root,owner,children,count=C.c_ulong(),C.c_ulong(),C.c_void_p(),C.c_uint()
            self.call('XQueryTree',self.window,C.byref(root),C.byref(owner),C.byref(children),C.byref(count))
            if children.value:
                self.x.XFree(children)
            if owner.value != parent:
                self.call('XUnmapWindow',self.window)
                self.call('XReparentWindow',self.window,parent,0,0)
                self.call('XMapWindow',self.window)
                self.size=None
        self.resize(width,height)
        return True

    def resize(self,width,height):
        size=max(1,int(width)),max(1,int(height))
        if self.window is not None and self.size!=size:
            self.call('XMoveResizeWindow',self.window,0,0,*size)
            self.size=size

    def detach(self):
        if self.window is None:
            return
        window,self.window=self.window,None
        self.size=None
        try:
            self.call('XUnmapWindow',window)
            self.call('XReparentWindow',window,self.root,20,40)
            self.call('XMoveResizeWindow',window,20,40,960,580)
            self.call('XRemoveFromSaveSet',window)
            self.call('XMapWindow',window)
        except RuntimeError:
            pass  # A stopped simulator can destroy the native view first.

    def close(self):
        self.detach()
        self.x.XSetErrorHandler(self.previous)
        self.x.XCloseDisplay(self.display)
