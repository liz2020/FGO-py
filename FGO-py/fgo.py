import argparse,os,sys
from fgoConst import VERSION

os.chdir(os.path.dirname(os.path.abspath(__file__)))
try:
    with open("../.git/HEAD")as f:head=f.read().strip()
except (FileNotFoundError, OSError):
    head=''

parser=argparse.ArgumentParser(description=f'FGO-py {VERSION}')
parser.add_argument('entrypoint',help='Program entry point (default: %(default)s)',type=str.lower,choices=['cli','web'],default='web',nargs='?')
parser.add_argument('-v','--version',help='Show FGO-py version',action='version',version=VERSION)
parser.add_argument('-l','--loglevel',help='Change the console log level (default: %(default)s)',type=str.upper,choices=['DEBUG','INFO','WARNING','CRITICAL','ERROR'],default='INFO')
parser.add_argument('-c','--config',help='Config file path (default: %(default)s)',type=str,default='fgoConfig.json')
parser.add_argument('-d','--device',help='Device serial (e.g. 127.0.0.1:5555 or ldplayer:0)',type=str,default=None)
parser.add_argument('-p','--port',help='Web server port (default: %(default)s)',type=int,default=15000)
parser.add_argument('--no-color',help='Disable colored console output',action='store_true')
arg=parser.parse_args()

if arg.no_color:os.environ['NO_COLOR']='1'

match arg.entrypoint:
    case'cli':from fgoCli import main
    case'web':from fgoWebServer import main

import fgoLogging
fgoLogging.logger.handlers[-1].setLevel(arg.loglevel)

from fgoConfig import Config
config=Config(arg.config)
if not config.runOnce:config.runOnce=VERSION
elif config.runOnce!=VERSION:
    from fgoRunOnce import runOnce
    if runOnce(config):
        config.runOnce=VERSION
        config.save()
        sys.exit()
    config.runOnce=VERSION

if not config.farming:
    from fgoKernel import farming
    farming.stop=True

# Connect to device if specified via CLI
if arg.device:
    import fgoDevice
    fgoDevice.device=fgoDevice.Device(arg.device)

try:main(config,port=arg.port)
except Exception as e:fgoLogging.logger.exception(e)
finally:config.save()
