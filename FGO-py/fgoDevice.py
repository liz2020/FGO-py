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
    """LDPlayer device using emu module for fast screenshots + ADB for touch."""
    def __init__(self, index: int):
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from emu.ldplayer import LDPlayerBackend
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

        self._index = index
        self._width = item.get("width", 1280)
        self._height = item.get("height", 720)
        self._pid = item.get("pid", 0) or 0
        self._opengl = LDOpenGL(
            console.install_dir, index,
            pid=self._pid, width=self._width, height=self._height,
        )
        self._serial = console.adb_serial(index)

        # Set up ADB touch via airtest Android (connect without screenshot override)
        self._android = Android(self._serial)
        self.name = f"ldplayer:{index}"

    @property
    def available(self):
        return self._android.available if self._android.name else False

    def screenshot(self):
        """Fast screenshot via LDOpenGL DLL."""
        import cv2
        img = self._opengl.screenshot()
        if img is None:
            # Fallback to ADB
            return self._android.screenshot()
        # Resize to standard 1280x720 if needed
        if img.shape[:2] != (720, 1280):
            img = cv2.resize(img, (1280, 720), interpolation=cv2.INTER_CUBIC)
        return img

    def touch(self, pos):
        self._android.touch(pos)

    def press(self, key):
        self._android.press(key)

    def swipe(self, begin, end):
        self._android.swipe(begin, end)

    def pinch(self):
        self._android.pinch()

    def invoke169(self):
        self._android.invoke169()

    def revoke169(self):
        self._android.revoke169()


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
