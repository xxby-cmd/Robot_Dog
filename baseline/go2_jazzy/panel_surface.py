"""Shared lifecycle for the same ROS/Tk view in a window or the workspace."""
import signal
import queue
import threading
import tkinter as tk
import rclpy
from rclpy.executors import SingleThreadedExecutor


class PanelSurface:
    def __init__(self, parent, title, width, height):
        self.standalone = parent is None
        self.owns_ros = not rclpy.ok()
        if self.owns_ros:
            rclpy.init()
        self.root = tk.Tk() if self.standalone else tk.Frame(parent)
        if not self.standalone:
            self.root.pack(fill='both', expand=True)
        if self.standalone:
            self.root.title(title)
            width = min(width, max(320, self.root.winfo_screenwidth()-32))
            height = min(height, max(300, self.root.winfo_screenheight()-85))
            self.root.geometry(f'{width}x{height}+10+30')
            self.root.minsize(min(width, 380), min(height, 360))
        self.executor = SingleThreadedExecutor()
        self.node = None
        self.finished = False
        self.pending = set()
        self.owner_thread = threading.get_ident()
        self.requests = queue.SimpleQueue()
        self.dispatch_job = self.root.after(30, self.drain)

    def drain(self):
        if self.finished:
            return
        for _ in range(30):
            try:
                milliseconds, callback, args = self.requests.get_nowait()
            except queue.Empty:
                break
            self.after(milliseconds, callback, *args)
        self.dispatch_job = self.root.after(30, self.drain)

    def after(self, milliseconds, callback, *args):
        if self.finished:
            return None
        # Worker threads never call Tcl. A zero-delay Tcl callback could run
        # before the worker receives/assigns its timer ID, besides Tk's own
        # thread lifetime constraints. Register every timer on Tk's thread.
        if threading.get_ident() != self.owner_thread:
            self.requests.put((milliseconds, callback, args))
            return None
        def invoke():
            self.pending.discard(job)
            if not self.finished:
                callback(*args)
        job = self.root.after(milliseconds, invoke)
        self.pending.add(job)
        return job

    def after_idle(self, callback, *args):
        return self.after('idle', callback, *args)

    def attach(self, node):
        self.node = node
        self.executor.add_node(node)

    def spin(self, limit):
        for _ in range(limit):
            self.executor.spin_once(timeout_sec=0)

    def finish(self):
        if self.finished:
            return
        self.finished = True
        self.root.after_cancel(self.dispatch_job)
        for job in self.pending:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self.pending.clear()
        if self.node is not None:
            self.executor.remove_node(self.node)
            self.node.destroy_node()
        self.executor.shutdown(timeout_sec=0)
        if self.owns_ros and rclpy.ok():
            rclpy.shutdown()
        self.root.destroy()

    def run(self, close):
        self.close = close
        if self.standalone:
            self.root.protocol('WM_DELETE_WINDOW', close)
            signal.signal(signal.SIGTERM, lambda *_: self.after_idle(close))
            signal.signal(signal.SIGINT, lambda *_: self.after_idle(close))
            self.root.mainloop()
        return self
