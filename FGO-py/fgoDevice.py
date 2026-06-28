from fgoAndroid import Android
from fgoDetect import setup
from fgoLogging import getLogger
from fgoSchedule import schedule
logger=getLogger('Device')

helpers={}
def regHelper(func):
    helpers[func.__name__]=func
    return func
def convert(text):
    if text is None:return None
    if not text.startswith('/'):return text
    try:return(lambda args:helpers[args[0][1:]](*args[1:]))(text.split('_'))
    except Exception as e:return logger.exception(e)

@regHelper
def gw(*args):
    import netifaces
    return f'{netifaces.gateways()["default"][netifaces.AF_INET][0]}:5555'
@regHelper
def bs4(*args):
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,rf'SOFTWARE\BlueStacks_bgp64_hyperv\Guests\Android{f"_{args[0]}"if args else""}\Config')as key:return f'127.0.0.1:{winreg.QueryValueEx(key,"BstAdbPort")[0]}'
@regHelper
def bs5(*args):
    import os,re,winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\BlueStacks_nxt')as key:dir=winreg.QueryValueEx(key,'UserDefinedDir')[0]
    with open(os.path.join(dir,'bluestacks.conf'))as f:return'127.0.0.1:'+re.search(rf'bst\.instance\.{"_".join(args)}\.status\.adb_port="(\d*)"',f.read()).group(1)


class LDPlayerDevice:
    """LDPlayer device using emu module for fast screenshots + Win32 PostMessage for input."""

    # Win32 constants
    WM_MOUSEMOVE = 0x0200
    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    MK_LBUTTON = 0x0001

    def __init__(self, index: int):
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from emu.ldplayer import LDPlayerBackend, LDConsole
        from emu.ldopengl import LDOpenGL

        self.backend = LDPlayerBackend()
        console = self.backend._ensure_console()
        if console is None:
            raise RuntimeError("LDPlayer not detected")

        # Get instance info
        raw = console.list_instances()
        item = next((i for i in raw if i["index"] == index), None)
        if item is None:
            raise RuntimeError(f"LDPlayer instance {index} not found")
        if not item.get("is_running"):
            raise RuntimeError(f"LDPlayer instance {index} is not running")

        self._index = index
        self._width = item.get("width", 1280)
        self._height = item.get("height", 720)
        self._pid = item.get("pid", 0) or 0
        self._console = console
        self._opengl = LDOpenGL(
            console.install_dir, index,
            pid=self._pid, width=self._width, height=self._height,
        )
        self.name = f"ldplayer:{index}"
        # Detect running FGO package
        self.package = self._detect_fgo_package()
        # Find the RenderWindow HWND for Win32 input
        self._render_hwnd = self._find_render_hwnd()
        if not self._render_hwnd:
            raise RuntimeError(f"Could not find RenderWindow for LDPlayer instance {index}")
        # Get render window client size for coordinate scaling
        import ctypes, ctypes.wintypes
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetClientRect(self._render_hwnd, ctypes.byref(rect))
        self._render_w = rect.right
        self._render_h = rect.bottom
        # Scale: game coords (1280x720) → render window client area
        self._scale_x = self._render_w / 1280.0
        self._scale_y = self._render_h / 720.0

    def _find_render_hwnd(self):
        """Find the RenderWindow child HWND belonging to this LDPlayer instance."""
        import ctypes, ctypes.wintypes
        user32 = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        result = [None]

        def enum_top(hwnd, _):
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls_buf, 256)
            if cls_buf.value != "LDPlayerMainFrame":
                return True
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != self._pid:
                return True
            # Found the main frame for our PID — find RenderWindow child
            def enum_child(child_hwnd, _):
                child_cls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(child_hwnd, child_cls, 256)
                if child_cls.value == "RenderWindow":
                    result[0] = child_hwnd
                    return False
                return True
            user32.EnumChildWindows(hwnd, WNDENUMPROC(enum_child), 0)
            return result[0] is None  # stop if found
        user32.EnumWindows(WNDENUMPROC(enum_top), 0)
        return result[0]

    @property
    def available(self):
        return True  # If we got here, the instance is running

    def screenshot(self):
        """Fast screenshot via LDOpenGL DLL."""
        import cv2
        img = self._opengl.screenshot()
        if img is None:
            return None
        # Resize to standard 1280x720 if needed
        if img.shape[:2] != (720, 1280):
            img = cv2.resize(img, (1280, 720), interpolation=cv2.INTER_CUBIC)
        return img

    def touch(self, pos, wait=0):
        """Touch at game coordinates (1280x720 space) via Win32 PostMessage."""
        import ctypes, time
        user32 = ctypes.windll.user32
        x = int(pos[0] * self._scale_x)
        y = int(pos[1] * self._scale_y)
        lparam = x | (y << 16)
        user32.PostMessageW(self._render_hwnd, self.WM_MOUSEMOVE, 0, lparam)
        time.sleep(0.01)
        user32.PostMessageW(self._render_hwnd, self.WM_LBUTTONDOWN, self.MK_LBUTTON, lparam)
        time.sleep(0.05)
        user32.PostMessageW(self._render_hwnd, self.WM_LBUTTONUP, 0, lparam)

    def press(self, key):
        """Press a mapped key (touch at key position)."""
        from fgoConst import KEYMAP
        if key in KEYMAP:
            self.touch(KEYMAP[key])

    def swipe(self, begin, end, duration=300):
        """Swipe from begin to end in game coordinates via Win32 PostMessage."""
        import ctypes, time
        user32 = ctypes.windll.user32
        x1, y1 = int(begin[0] * self._scale_x), int(begin[1] * self._scale_y)
        x2, y2 = int(end[0] * self._scale_x), int(end[1] * self._scale_y)
        steps = max(5, duration // 20)
        # Mouse down at start
        lp_start = x1 | (y1 << 16)
        user32.PostMessageW(self._render_hwnd, self.WM_MOUSEMOVE, 0, lp_start)
        time.sleep(0.01)
        user32.PostMessageW(self._render_hwnd, self.WM_LBUTTONDOWN, self.MK_LBUTTON, lp_start)
        # Interpolate move
        step_delay = duration / 1000.0 / steps
        for i in range(1, steps + 1):
            t = i / steps
            cx = int(x1 + (x2 - x1) * t)
            cy = int(y1 + (y2 - y1) * t)
            lp = cx | (cy << 16)
            user32.PostMessageW(self._render_hwnd, self.WM_MOUSEMOVE, self.MK_LBUTTON, lp)
            time.sleep(step_delay)
        # Mouse up at end
        lp_end = x2 | (y2 << 16)
        user32.PostMessageW(self._render_hwnd, self.WM_LBUTTONUP, 0, lp_end)

    def pinch(self):
        pass  # Not needed for FGO

    def _detect_fgo_package(self) -> str:
        """Detect which FGO package is installed/running on this instance."""
        import subprocess
        from fgoConst import PACKAGE_TO_REGION
        for pkg in PACKAGE_TO_REGION:
            try:
                result = subprocess.run(
                    [str(self._console.ldconsole), "adb", "--index", str(self._index),
                     "--command", f"shell pm path {pkg}"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if "package:" in result.stdout:
                    return pkg
            except Exception:
                continue
        return "com.bilibili.fatego"  # default to CN

    def invoke169(self):
        pass  # LDPlayer already configured at correct resolution

    def revoke169(self):
        pass


class Device:
    def __init__(self,name=None):
        if not name:self.I=self.O=Android()
        elif name.startswith('ldplayer:'):
            # LDPlayer device: ldplayer:0, ldplayer:2, etc.
            index = int(name.split(':')[1])
            ld = LDPlayerDevice(index)
            self.I = self.O = ld
            self.name = ld.name
            self.press = ld.press
            self.swipe = ld.swipe
            setup(ld)
            return
        elif'|'in name:
            self.I,self.O=[self.createDevice(i)for i in name.split('|')]
            self.name='|'.join((self.I.name,self.O.name))
        else:
            self.I=self.O=self.createDevice(name)
            self.name=self.I.name
        self.press=self.I.press
        self.swipe=self.I.swipe
        setup(self.O)
    @staticmethod
    def createDevice(name,*args,**kwargs):
        return Android(convert(name),*args,**kwargs)
    @property
    def available(self):return self.I.available and(self.I is self.O or self.O.available)
    def perform(self,pos,wait):[(self.press(i),schedule.sleep(j*.001))for i,j in zip(pos,wait)]
    def touch(self,pos,wait=0):(self.I.touch(pos),schedule.sleep(wait*.001))
    enumDevices=Android.enumDevices
    def __getattr__(self,attr):return getattr(self.I,attr,getattr(self.O,attr))

# def connect(name=None,*args,**kwargs):
#     global device
#     device=Device(name,*args,**kwargs)
device=Device()
