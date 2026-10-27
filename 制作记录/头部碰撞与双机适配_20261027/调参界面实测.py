"""Exercise the actual Tk callbacks and observe their real ROS Twist messages.

Runs a short, isolated panel instance with auto-start timers cancelled; it does
not start another Gazebo or replace the currently running user's panel.
"""
import fcntl
import inspect
import json
from pathlib import Path
import runpy
import sys
import time
import tkinter as tk

sys.path.insert(0,str(Path.home()/'bin'))
import teleop_settings
report = {'result':'FAIL','commands':{},'native_input':False}
settings = teleop_settings.CONFIG
original = settings.read_bytes() if settings.exists() else None
flock = fcntl.flock
fcntl.flock = lambda *args: None  # The test never owns or starts a simulation.
mainloop = tk.Tk.mainloop

def children(widget):
    result=[]
    for child in widget.winfo_children():
        result.append(child)
        result.extend(children(child))
    return result

def test_loop(root, *args, **kwargs):
    ns = inspect.currentframe().f_back.f_globals
    root.withdraw()
    for job in root.tk.call('after','info'):
        root.after_cancel(job)
    node=ns['node']
    from geometry_msgs.msg import Twist
    observed=[]
    node.create_subscription(Twist,'/cmd_vel',lambda msg:observed.append((msg.linear.x,msg.angular.z)),10)
    def pump(seconds):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            ns['rclpy'].spin_once(node,timeout_sec=.01)
            root.update()
    try:
        deadline=time.monotonic()+12
        while ns['joint_count'] != 12:
            pump(.05)
            if time.monotonic()>deadline:raise TimeoutError('No live twelve-joint feedback')
        assert ns['controller_client'].wait_for_service(timeout_sec=15),'Controller service discovery timed out'
        future=ns['controller_client'].call_async(ns['ListControllers'].Request())
        deadline=time.monotonic()+8
        while not future.done():
            pump(.05)
            if time.monotonic()>deadline:raise TimeoutError('Controller service not ready')
        ns['controller_ready']=any(c.name=='joint_group_effort_controller' and c.state=='active' for c in future.result().controller)
        ns['controller_since']=time.monotonic()-3
        assert ns['controller_ready']
        buttons=[w for w in children(root) if isinstance(w,tk.Button)]
        tuner=next(w for w in buttons if w.cget('text')=='速度调参')
        tuner.invoke()
        window=ns['settings_window']
        inputs=[w for w in children(window) if isinstance(w,tk.Spinbox)]
        save=next(w for w in children(window) if isinstance(w,tk.Button) and w.cget('text')=='保存并应用')
        selected={'forward':.11,'reverse':.09,'turn':.18}
        for widget,value in zip(inputs,selected.values()):
            widget.setvar(widget.cget('textvariable'),str(value))
        save.invoke()
        assert teleop_settings.load()==selected and ns['speeds']==selected
        report['persisted']=teleop_settings.load()
        inputs[0].setvar(inputs[0].cget('textvariable'),'nan')
        save.invoke()
        assert teleop_settings.load()==selected and ns['speeds']==selected
        inputs[0].setvar(inputs[0].cget('textvariable'),'.11')
        save.invoke()
        for action in ('forward','reverse','left','right'):
            observed.clear()
            ns['key_pressed'](action)
            assert ns['target']==teleop_settings.command(action,selected)
            ns['send'](*ns['target'])
            pump(.12)
            ns['halt']()
            expected=teleop_settings.command(action,selected)
            assert expected in observed,(action,observed)
            report['commands'][action]=expected
            pump(.08)
            assert (0.,0.) in observed
        ns['halt']()
        root.deiconify()
        root.update()
        window.update()
        all_widgets=children(root)+children(window)
        clipped=[str(w.cget('text')) for w in all_widgets if isinstance(w,tk.Button)
                 and w.winfo_ismapped() and w.winfo_rooty()+w.winfo_height()>root.winfo_screenheight()]
        assert not clipped,('Buttons below screen',clipped)
        report['window']={'panel':[root.winfo_width(),root.winfo_height()],
                          'tuner':[window.winfo_width(),window.winfo_height()],
                          'screen':[root.winfo_screenwidth(),root.winfo_screenheight()]}
        import subprocess
        subprocess.run(['python3',str(Path.home()/'bin/capture_guest.py'),str(Path.home()/'speed-controls.png')],check=True,timeout=10)
        report['result']='PASS'
    except Exception as error:
        report['error']=repr(error)
    finally:
        ns['halt']()
        if original is None:
            settings.unlink(missing_ok=True)
        else:
            settings.write_bytes(original)
        node.destroy_node()
        ns['rclpy'].shutdown()
        root.destroy()
        Path.home().joinpath('speed-controls-test.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2),flush=True)

tk.Tk.mainloop=test_loop
try:
    runpy.run_path(str(Path.home()/'bin/robot-dog-panel.py'),run_name='__main__')
finally:
    fcntl.flock=flock
    tk.Tk.mainloop=mainloop
raise SystemExit(0 if report['result']=='PASS' else 1)
