"""Independent HLS recorder tab; may run alongside the download worker."""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .common import AppError, Control
from .live import LiveOptions, record_live
from .live_watch import LiveWatcher, WatchOptions


class LiveTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent,padding=20)
        self.app = app
        self.worker = None
        self.watch_worker = None
        self.watcher = None
        self.watch_control = Control()
        self.watch_fingerprint = None
        self.failed_fingerprint = None
        self.next_auto_check = 0
        self.control = Control()
        self.events = queue.Queue()
        self.url = tk.StringVar(self,'')
        self.folder = tk.StringVar(self,str(Path.home()/'Downloads'/'OnlyFans'/'Live'))
        self.label = tk.StringVar(self,'live')
        self.minutes = tk.StringVar(self,'0')
        self.status = tk.StringVar(self,'Ready · paste an accessible HLS playlist URL')
        self.progress = tk.StringVar(self,'No recording yet')
        self.file_path = tk.StringVar(self,'')
        self.enabled = tk.BooleanVar(self,False)
        self.include = tk.StringVar(self,'')
        self.exclude = tk.StringVar(self,'')
        self.interval = tk.StringVar(self,'60')
        self.parallel = tk.StringVar(self,'2')
        self.watch_status = tk.StringVar(self,'Automatic recording is off')
        pages = ttk.Notebook(self)
        pages.pack(fill='both',expand=True)
        automatic = ttk.Frame(pages,padding=10)
        manual = ttk.Frame(pages,padding=10)
        pages.add(automatic,text='Automatic live recording')
        pages.add(manual,text='Manual HLS URL')
        self._build_automatic(automatic)
        ttk.Label(manual,text='Manual HLS recording',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(manual,text='Paste the HTTPS .m3u8 request URL from a stream you can already watch (browser Network tab). '
                           'Records clear, muxed HLS from OnlyFans/CDN hosts as .ts or fragmented .mp4. '
                           'Recording runs independently of Downloads.',
                  style='Muted.TLabel',wraplength=900).pack(anchor='w',pady=(6,12))
        self.inputs = []
        for title,var,hidden in [('Playlist URL',self.url,True),('Recording label',self.label,False),
                                 ('Output folder',self.folder,False),('Maximum minutes (0 = until stopped)',self.minutes,False)]:
            row = ttk.Frame(manual)
            row.pack(fill='x',pady=5)
            ttk.Label(row,text=title,width=38).pack(side='left')
            entry = ttk.Entry(row,textvariable=var,show='*' if hidden else '')
            entry.pack(side='left',fill='x',expand=True)
            self.inputs.append(entry)
        actions = ttk.Frame(manual)
        actions.pack(fill='x',pady=12)
        self.start_button = ttk.Button(actions,text='Start recording',command=self.start,style='Accent.TButton')
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(actions,text='Stop and save',command=self.stop,state='disabled')
        self.stop_button.pack(side='left',padx=8)
        self.browse_button = ttk.Button(actions,text='Choose folder…',command=self.browse)
        self.browse_button.pack(side='left')
        ttk.Label(manual,textvariable=self.status,wraplength=900).pack(anchor='w',pady=(12,6))
        ttk.Label(manual,textvariable=self.progress,style='Muted.TLabel').pack(anchor='w')
        ttk.Label(manual,textvariable=self.file_path,style='Muted.TLabel',wraplength=900).pack(anchor='w',pady=8)
        ttk.Label(manual,text='URL stays in memory and is not saved in preferences or logs. Uses the Session tab proxy. '
                           'Stop saves complete segments; failures leave a .part file. No pause: pausing a live source would lose segments. '
                           'Encrypted streams, separate audio renditions and byte-range playlists are not supported.',
                  style='Muted.TLabel',wraplength=900).pack(anchor='w',pady=12)
        self.poll_id = self.after(100,self.poll)
        self.bind('<Destroy>',self.on_destroy,add='+')

    @property
    def running(self):
        return self.worker is not None or self.watch_worker is not None

    def _build_automatic(self, parent):
        ttk.Label(parent,text='Record when creators go live',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(parent,text='Uses your Session and active subscriptions. No playlist URLs needed. '
                  'A temporary browser opens for each live stream and records WebRTC audio/video as WebM. '
                  'Keep this app open. Browser login/verification may occasionally need attention.',
                  style='Muted.TLabel',wraplength=900).pack(anchor='w',pady=6)
        ttk.Checkbutton(parent,text='Automatically record live streams (remember on restart)',
                        variable=self.enabled,command=self.auto_changed).pack(anchor='w',pady=6)
        self.auto_inputs = []
        for title, variable in [('Only these creators (blank = all subscriptions)',self.include),
                                ('Skip creators',self.exclude),('Check every seconds (30–3600)',self.interval),
                                ('Parallel recordings (1–4)',self.parallel),('Output folder',self.folder),
                                ('Maximum minutes per stream (0 = unlimited)',self.minutes)]:
            row = ttk.Frame(parent)
            row.pack(fill='x',pady=2)
            ttk.Label(row,text=title,width=47).pack(side='left')
            entry = ttk.Entry(row,textvariable=variable)
            entry.pack(side='left',fill='x',expand=True)
            self.auto_inputs.append(entry)
        actions = ttk.Frame(parent)
        actions.pack(fill='x',pady=8)
        self.setup_button = ttk.Button(actions,text='Set up browser',command=self.install_browser)
        self.setup_button.pack(side='left')
        ttk.Button(actions,text='Choose folder…',command=self.browse).pack(side='left',padx=6)
        ttk.Button(actions,text='Stop selected stream',command=self.stop_selected).pack(side='left',padx=6)
        ttk.Button(actions,text='Stop monitoring and save all',command=self.stop).pack(side='left')
        ttk.Label(parent,textvariable=self.watch_status,wraplength=900).pack(anchor='w',pady=4)
        self.watch_tree = ttk.Treeview(parent,columns=('creator','stream','status','size','path'),
                                      show='headings',height=7,selectmode='extended')
        for name,title,width in [('creator','Creator',110),('stream','Stream',85),('status','Status',300),
                                 ('size','MiB',70),('path','File',250)]:
            self.watch_tree.heading(name,text=title)
            self.watch_tree.column(name,width=width,minwidth=60)
        self.watch_tree.pack(fill='both',expand=True)
        scrollbar = ttk.Scrollbar(parent,orient='horizontal',command=self.watch_tree.xview)
        scrollbar.pack(fill='x')
        self.watch_tree.configure(xscrollcommand=scrollbar.set)
        ttk.Label(parent,text='Stop selected suppresses that stream for this monitoring session. '
                  'New stream IDs can record again. Failed starts retry at most 3 times. '
                  'Purchases/subscriptions are blocked; some players may require an unsupported join action.',
                  style='Muted.TLabel',wraplength=900).pack(anchor='w',pady=6)

    def preference_values(self):
        return {'live_enabled':self.enabled.get(), 'live_include':self.include.get(),
                'live_exclude':self.exclude.get(), 'live_interval':self.interval.get(),
                'live_parallel':self.parallel.get(), 'live_folder':self.folder.get(),
                'live_minutes':self.minutes.get()}

    def load_preferences(self, values):
        self.enabled.set(values.get('live_enabled') is True)
        for key,var in [('live_include',self.include),('live_exclude',self.exclude),
                        ('live_interval',self.interval),('live_parallel',self.parallel),
                        ('live_folder',self.folder),('live_minutes',self.minutes)]:
            if key in values:
                var.set(str(values[key]))

    def auto_changed(self):
        self.failed_fingerprint = None
        self.next_auto_check = 0
        if not self.enabled.get():
            self.watch_control.stop()
            self.watch_status.set('Stopping automatic recordings…' if self.watch_worker else 'Automatic recording is off')
        self.app._persist_silently()

    def _fingerprint(self):
        return (tuple(var.get() for var in self.app.auth_vars.values()), self.app.proxy.get(),
                self.app.rules_source.get(), self.app.browser_channel.get(), self.include.get(),
                self.exclude.get(), self.interval.get(), self.parallel.get(), self.folder.get(), self.minutes.get(),
                tuple(sorted(self.app.imported_auth.extra_cookies.items())) if self.app.imported_auth else ())

    def maybe_watch(self):
        if self.app.closing or not self.enabled.get() or time.monotonic() < self.next_auto_check:
            return
        self.next_auto_check = time.monotonic() + 1
        fingerprint = self._fingerprint()
        if self.watch_worker is not None:
            if fingerprint != self.watch_fingerprint:
                self.watch_control.stop()
                self.watch_status.set('Settings changed · saving recordings before restarting monitor')
            return
        if self.worker is not None or fingerprint == self.failed_fingerprint:
            return
        try:
            auth = self.app.get_auth()
            if not self.folder.get().strip():
                raise AppError('Choose a recording folder.')
            options = WatchOptions(Path(self.folder.get().strip()), self.include.get(), self.exclude.get(),
                                   int(self.interval.get()), int(self.parallel.get()), float(self.minutes.get())*60,
                                   {'Chromium':'chromium','Google Chrome':'chrome','Microsoft Edge':'msedge'}[self.app.browser_channel.get()],
                                   self.app.proxy.get().strip()).validate()
        except (AppError, ValueError, KeyError) as exc:
            self.watch_status.set('Waiting for valid Session and live settings · ' + (str(exc) if isinstance(exc,AppError) else 'Check numeric options.'))
            return
        source = self.app.rules_source.get().strip()
        self.watch_control = Control()
        self.watch_fingerprint = fingerprint
        self.app._persist_silently()
        self.watch_tree.delete(*self.watch_tree.get_children())
        self.watcher = LiveWatcher(auth,source,options,self.watch_control,
                                   lambda kind,value:self.events.put((kind,value)))
        watcher = self.watcher
        self.watch_status.set('Starting live monitor…')
        self.start_button.state(['disabled'])
        self.setup_button.state(['disabled'])
        def run():
            try:
                watcher.run()
            except AppError as exc:
                self.events.put(('watch_error',str(exc)))
            except Exception:
                self.events.put(('watch_error','Live monitor failed; private details withheld. Disable and re-enable to retry.'))
            finally:
                self.events.put(('watch_done',None))
        self.watch_worker = threading.Thread(target=run,name='ofdl-live-watch',daemon=True)
        self.watch_worker.start()

    def stop_selected(self):
        if self.watcher:
            self.watcher.stop_selected(self.watch_tree.selection())

    def install_browser(self):
        if self.running or self.app.closing:
            return
        from .live_browser import setup_browser
        self.control = Control()
        channel = {'Chromium':'chromium','Google Chrome':'chrome','Microsoft Edge':'msedge'}[self.app.browser_channel.get()]
        self.setup_button.state(['disabled'])
        self.watch_status.set('Setting up browser · this may take several minutes…')
        def run():
            try:
                setup_browser(self.control,channel)
                self.events.put(('setup_result','Browser dependencies ready. Enable automatic recording.'))
            except AppError as exc:
                self.events.put(('setup_result',str(exc)))
            except Exception:
                self.events.put(('setup_result','Browser setup failed. Run the included browser installer.'))
            finally:
                self.events.put(('done',None))
        self.worker = threading.Thread(target=run,name='ofdl-live-setup',daemon=True)
        self.worker.start()

    def browse(self):
        path = filedialog.askdirectory(parent=self,title='Recording output folder')
        if path:
            self.folder.set(path)

    def start(self):
        if self.running or self.app.closing:
            return
        try:
            if not self.folder.get().strip():
                raise AppError('Choose an output folder for the recording.')
            try:
                minutes = float(self.minutes.get().strip())
            except ValueError:
                raise AppError('Maximum minutes must be a number (0 means until stopped).') from None
            options = LiveOptions(self.url.get().strip(),Path(self.folder.get().strip()),
                                  self.label.get().strip(),minutes*60,self.app.proxy.get().strip(),
                                  self.app.auth_vars['user_agent'].get().strip() or 'Mozilla/5.0').validate()
        except AppError as exc:
            messagebox.showerror('Live recording setup',str(exc),parent=self)
            return
        self.control = Control()
        self.file_path.set('')
        self.progress.set('Waiting for complete media segments…')
        self.status.set('Starting live recording…')
        self.start_button.state(['disabled'])
        self.browse_button.state(['disabled'])
        self.stop_button.state(['!disabled'])
        for entry in self.inputs:
            entry.state(['disabled'])
        def emit(kind, value):
            self.events.put((kind,value))
        def run():
            try:
                emit('result',record_live(options,self.control,emit))
            except AppError as exc:
                emit('error',str(exc))
            except Exception:
                emit('error','Live recording failed. Check dependencies, stream format and output permissions; raw details were withheld.')
            finally:
                emit('done',None)
        self.worker = threading.Thread(target=run,name='ofdl-live',daemon=True)
        self.worker.start()

    def stop(self):
        if not self.app.closing:
            self.enabled.set(False)
            self.app._persist_silently()
        self.watch_control.stop()
        if self.watch_worker:
            self.watch_status.set('Stopping monitor · saving recordings…')
        if self.running:
            self.control.stop()
            self.status.set('Stopping · saving complete segments…')
            self.stop_button.state(['disabled'])

    def poll(self):
        self.poll_id = None
        for _ in range(200):
            try:
                kind,value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'live_status':
                self.status.set(value)
            elif kind in {'watch_status','setup_result'}:
                self.watch_status.set(value)
            elif kind == 'watch_error':
                self.failed_fingerprint = self.watch_fingerprint
                self.watch_status.set(value)
            elif kind == 'watch_done':
                self.watch_worker = None
                self.watcher = None
                self.start_button.state(['!disabled'])
                self.setup_button.state(['!disabled'])
                if not self.enabled.get():
                    self.watch_status.set('Automatic recording is off · recordings stopped')
            elif kind == 'watch_row':
                key = value['key']
                status = value['status']
                if status == 'Recording' and not value.get('audio',True):
                    status += ' · VIDEO ONLY'
                columns = (value['creator'],value['stream_id'],status,
                           f"{value.get('bytes',0)/1048576:.1f}",value.get('path',''))
                if self.watch_tree.exists(key):
                    self.watch_tree.item(key,values=columns)
                else:
                    self.watch_tree.insert('','end',iid=key,values=columns)
            elif kind == 'watch_remove':
                if self.watch_tree.exists(value):
                    self.watch_tree.delete(value)
            elif kind == 'live_progress':
                self.status.set('Recording live stream…')
                self.progress.set(f"{value['segments']} segments · {value['media_seconds']:.1f}s media · {value['bytes']/1048576:.1f} MiB · {value['elapsed']:.0f}s elapsed")
                self.file_path.set(value['path'])
            elif kind == 'result':
                self.status.set(f'Recording {value.reason} · {value.segments} complete segment(s) saved')
                self.file_path.set(str(value.path) if value.path else 'No complete segments received; no recording file created.')
            elif kind == 'error':
                self.status.set(value)
                if not self.app.closing:
                    messagebox.showerror('Live recording stopped',value,parent=self)
            elif kind == 'done':
                self.worker = None
                self.setup_button.state(['!disabled'])
                self.start_button.state(['!disabled'])
                self.browse_button.state(['!disabled'])
                self.stop_button.state(['disabled'])
                for entry in self.inputs:
                    entry.state(['!disabled'])
        self.maybe_watch()
        if self.winfo_exists():
            self.poll_id = self.after(100,self.poll)

    def on_destroy(self, event):
        if event.widget is self:
            self.control.stop()
            self.watch_control.stop()
            if self.poll_id is not None:
                self.after_cancel(self.poll_id)
                self.poll_id = None
