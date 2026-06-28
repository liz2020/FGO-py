import base64,cv2,json,time,threading
from flask import Flask,redirect,render_template,request,url_for
import fgoDevice
import fgoKernel
from fgoLogging import getLogger
from fgoTeamupParser import IniParser
logger=getLogger('Web')

teamup=IniParser('fgoTeamup.ini')
app=Flask(__name__,static_folder='fgoWebUI',template_folder='fgoWebUI')

# Progress reporting to emu manager
_emu_manager_url = None
_instance_index = 0

def _report_progress(current, total, status="running", detail=""):
    """Report farming progress to the emu manager (fire-and-forget)."""
    if not _emu_manager_url:
        return
    import urllib.request
    try:
        data = json.dumps({
            "instance_index": _instance_index,
            "current": current,
            "total": total,
            "status": status,
            "detail": detail,
        }).encode()
        req = urllib.request.Request(
            f"{_emu_manager_url}/api/scripts/fgo/progress",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass

@app.route('/')
def root():
    return redirect('index')

@app.route('/index')
def index():
    # X-Script-Base is set by the emu manager reverse proxy
    script_base = request.headers.get('X-Script-Base', '')
    manager_url = '/' if script_base else None
    return render_template('index.html',teamups=teamup.sections(),config=config,device=fgoDevice.device.name,manager_url=manager_url)

@app.route('/api/connect',methods=['POST'])
def connect():
    fgoDevice.device=fgoDevice.Device(request.form['serial'])
    return fgoDevice.device.name

@app.route('/api/teamup/load',methods=['POST'])
def teamupLoad():
    return {i:eval(j)for i,j in teamup[request.form['teamName']].items()}

@app.route('/api/teamup/save',methods=['POST'])
def teamupSave():
    teamup[request.form['teamName']]=json.loads(request.form['data'])
    with open('fgoTeamup.ini','w')as f:
        teamup.write(f)
    return ''

@app.route('/api/apply',methods=['POST'])
def apply():
    data=json.loads(request.form['data'])
    fgoKernel.Main.teamIndex=data['teamIndex']
    fgoKernel.ClassicTurn.skillInfo=data['skillInfo']
    fgoKernel.ClassicTurn.houguInfo=data['houguInfo']
    fgoKernel.ClassicTurn.masterSkill=data['masterSkill']
    return ''

def _run_with_progress(main_instance, apple_total):
    """Run a Main instance while reporting progress to the emu manager."""
    total = apple_total + 1  # appleTotal is extra runs from apples, +1 for the initial run
    _report_progress(0, total, "running", "Starting...")

    def monitor():
        while not getattr(main_instance, '_done', False):
            bc = getattr(main_instance, 'battleCount', 0)
            _report_progress(bc, total, "running")
            time.sleep(3)

    t = threading.Thread(target=monitor, daemon=True)
    t.start()
    try:
        main_instance()
        bc = getattr(main_instance, 'battleCount', 0)
        _report_progress(bc, total, "done", "Complete")
    except Exception as e:
        _report_progress(0, total, "error", str(e))
        raise
    finally:
        main_instance._done = True

@app.route('/api/run/main',methods=['POST'])
def runMain():
    if not fgoDevice.device.available:
        return 'Device not available'
    m = fgoKernel.Main(**{i:int(j)for i,j in request.form.items()})
    _run_with_progress(m, int(request.form.get('appleTotal', 0)))
    return 'Done'

@app.route('/api/run/battle',methods=['POST'])
def runBattle():
    if not fgoDevice.device.available:
        return 'Device not available'
    fgoKernel.Battle()()
    return 'Done'

@app.route('/api/run/classic',methods=['POST'])
def runClassic():
    if not fgoDevice.device.available:
        return 'Device not available'
    m = fgoKernel.Main(**{i:int(j)for i,j in request.form.items()},battleClass=lambda:fgoKernel.Battle(fgoKernel.ClassicTurn))
    _run_with_progress(m, int(request.form.get('appleTotal', 0)))
    return 'Done'

@app.route('/api/pause',methods=['POST'])
def pause():
    fgoKernel.schedule.pause()

@app.route('/api/stop',methods=['POST'])
def stop():
    fgoKernel.schedule.stop()

@app.route('/api/stopLater',methods=['POST'])
def stopLater():
    fgoKernel.schedule.stopLater(int(request.form['value']))

@app.route('/api/screenshot',methods=['POST'])
def screenshot():
    if not fgoDevice.device.available:
        return 'Device not available'
    return base64.b64encode(cv2.imencode('.png',fgoKernel.Detect().im)[1].tobytes())

@app.route('/api/bench',methods=['POST'])
def bench():
    if not fgoDevice.device.available:
        return 'Device not available'
    return(lambda bench:f'{f"点击 {bench[0]:.2f}ms"if bench[0]else""}{", "if all(bench)else""}{f"截图 {bench[1]:.2f}ms"if bench[1]else""}')(fgoKernel.bench(15))

def main(config, port=15000):
    globals()['config']=config
    # Set emu manager URL for progress reporting
    global _emu_manager_url, _instance_index
    _emu_manager_url = 'http://127.0.0.1:15100'
    # Extract instance index from device name if available
    try:
        if hasattr(fgoDevice, 'device') and fgoDevice.device:
            name = getattr(fgoDevice.device, 'name', '')
            if 'ldplayer:' in name:
                _instance_index = int(name.split(':')[1])
    except Exception:
        pass
    app.run(host='127.0.0.1', port=port)
