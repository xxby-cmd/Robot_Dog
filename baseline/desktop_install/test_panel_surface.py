"""Worker/Tk ownership and shared ROS cleanup contracts, without a GUI server."""
import importlib.util
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch


class Widget:
    def __init__(self):
        self.owner=threading.get_ident()
        self.jobs={}
        self.counter=0
        self.destroyed=0

    def pack(self,**kwargs):pass
    def title(self,value):pass
    def geometry(self,value):pass
    def minsize(self,*args):pass
    def winfo_screenwidth(self):return 1280
    def winfo_screenheight(self):return 768

    def after(self,delay,callback):
        if threading.get_ident()!=self.owner:
            raise AssertionError('Tk called from worker')
        self.counter+=1
        job='after-'+str(self.counter)
        self.jobs[job]=callback
        return job

    def after_cancel(self,job):
        self.jobs.pop(job,None)

    def fire(self,job):
        self.jobs.pop(job)()

    def destroy(self):
        self.destroyed+=1


class Executor:
    def __init__(self):self.nodes=[]
    def add_node(self,node):self.nodes.append(node)
    def remove_node(self,node):self.nodes.remove(node)
    def shutdown(self,**kwargs):pass


class PanelLifecycle(unittest.TestCase):
    def surface(self,active=True,parent=True):
        ros=types.ModuleType('rclpy')
        ros.active=active
        ros.shutdown_calls=0
        ros.ok=lambda:ros.active
        ros.init=lambda:setattr(ros,'active',True)
        def shutdown():
            ros.active=False
            ros.shutdown_calls+=1
        ros.shutdown=shutdown
        executors=types.ModuleType('rclpy.executors')
        executors.SingleThreadedExecutor=Executor
        path=Path(__file__).resolve().parents[1]/'go2_jazzy/panel_surface.py'
        spec=importlib.util.spec_from_file_location('surface_contract_fixture',path)
        module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'rclpy':ros,'rclpy.executors':executors}):
            spec.loader.exec_module(module)
        widget=Widget()
        with patch.object(module.tk,'Tk',return_value=widget),patch.object(module.tk,'Frame',return_value=widget):
            surface=module.PanelSurface(object() if parent else None,'Fixture',430,700)
        return surface,widget,ros

    def test_worker_posts_without_tk_and_callback_runs_on_owner(self):
        surface,widget,_=self.surface()
        seen=[]
        errors=[]
        def work():
            try:surface.after(0,lambda:seen.append(threading.get_ident()))
            except Exception as error:errors.append(error)
        worker=threading.Thread(target=work)
        worker.start();worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors,[])
        self.assertEqual(seen,[])
        widget.fire(surface.dispatch_job)
        timer=next(iter(surface.pending))
        widget.fire(timer)
        self.assertEqual(seen,[threading.get_ident()])
        self.assertEqual(surface.pending,set())
        surface.finish()
        self.assertEqual(widget.jobs,{})

    def test_closing_pane_drops_queued_worker_callback(self):
        surface,widget,_=self.surface()
        seen=[]
        worker=threading.Thread(target=lambda:surface.after(0,lambda:seen.append(True)))
        worker.start();worker.join(timeout=2)
        surface.finish()
        self.assertEqual(widget.jobs,{})
        self.assertEqual(seen,[])
        self.assertIsNone(surface.after(0,lambda:seen.append(True)))

    def test_pane_does_not_shutdown_shared_ros(self):
        surface,widget,ros=self.surface(active=True)
        destroyed=[]
        node=types.SimpleNamespace(destroy_node=lambda:destroyed.append(True))
        surface.attach(node)
        surface.finish();surface.finish()
        self.assertEqual(ros.shutdown_calls,0)
        self.assertTrue(ros.active)
        self.assertEqual(destroyed,[True])
        self.assertEqual(widget.destroyed,1)

    def test_standalone_shuts_down_its_own_ros_once(self):
        surface,widget,ros=self.surface(active=False,parent=False)
        self.assertTrue(ros.active)
        surface.finish();surface.finish()
        self.assertEqual(ros.shutdown_calls,1)
        self.assertEqual(widget.destroyed,1)
