"""Tk/ttk desktop UI. Only the main thread reads or writes Tk objects."""
from __future__ import annotations

import base64
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any, Callable

from . import __version__
from .api import ApiClient, load_rules
from .auth import (Credentials, SessionError, forget_session, load_session, parse_session,
                   save_session, MAX_IMPORT_BYTES)
from .browser import capture_session
from .common import AppError, Cancelled, Control
from .downloads import CATEGORIES, Options, parse_profiles, run_downloads
from .preview import PreviewResult, build_preview
from .settings import DEFAULT_RULES_SOURCE, config_dir, load_preferences, save_preferences

HELP_TEXT = """QUICK START

1. Session: choose Browser login, or Paste copied request from your normal logged-in browser.
2. Use Test session. The app fetches public signing rules separately; those requests never carry your account cookies.
3. Downloads: leave All active subscriptions selected to include everyone automatically. Skip creators is optional; no selection popup is needed.
4. Choose your output folder and content types. Try Scan only, then Start download.

PASTE COPIED REQUEST — NO FOUR-FIELD HUNT

In Chrome, Edge, or Firefox, log into your own account normally. Open developer tools (F12), select Network, then reload the page. Filter for /api2/v2/ and choose a successful logged-in request. Right-click it and copy it as cURL; the bash form is preferred when offered. Return here, click Paste copied request, paste, and import.

The app extracts USER_ID (from user-id or auth_id), USER_AGENT, X_BC, and SESS_COOKIE. It parses the text locally and NEVER executes the copied command. Raw request headers and session JSON also work. HAR imports work only when they contain the needed request headers/cookies. Sanitized HAR exports often omit cookies.

Treat copied requests as passwords. Never post them in chat or support tickets. Clear clipboard history separately if you copied one; clearing the current clipboard cannot erase clipboard managers or synchronized history.

BROWSER LOGIN

The helper opens an isolated, temporary browser—not your normal browser profile. You log in and complete any normal verification yourself. It waits for a completed /users/me GET response with matching account JSON, then captures request headers and closes. Capture is not proof that the desktop client can reuse the session. Password/2FA POST bodies are not read. No browser profile, traces, or storage-state file are saved by this app.

If a security challenge blocks the helper, stop and use Paste copied request from your normal browser. There are no CAPTCHA workarounds or stealth settings. Install the helper with install_browser_windows.bat, or install requirements-browser.txt and run python -m playwright install chromium. Installed Google Chrome or Edge can also be selected.

SESSION STORAGE

Private fields are hidden again after import, clearing fields, or starting Test session. Turn off Show private values before screenshots. A failed/stopped test is not ready; changed inputs need a new test.

By default the session remains in this running app's memory and is discarded on exit (Python/OS memory is not guaranteed to be securely erased). Save session uses a supported operating-system credential store. Plaintext fallback is refused. Load saved reads it on demand. Forget saved removes that saved entry; Clear fields removes the in-memory form. Neither action revokes the server session—use the website's account/security controls to do that. The "Load the saved session automatically when the app starts" checkbox loads that saved entry at launch and stays quiet if nothing is saved.

Preferences and rules cache are nonsecret, but profile names, output paths, local filenames, and the media manifest are still private metadata. Protect your device and backups. Other software running as your OS user may be able to access the keychain. The application has no telemetry or hosted authentication service.

SIGNING RULES AND ACCESS

This is an unofficial client based on the supplied script. Signing rules and endpoint behavior can change. Refresh rules after a signature-related rejection, check the system clock, and re-import a current session. A schema-valid rules file is not proof that the platform still accepts it. The default source is community-maintained; you may choose a different trusted HTTPS JSON URL or a local file. Its server sees an ordinary connection/IP address, but not your account session.

Locked media, DRM-marked media, DASH/HLS manifests, purchases you do not own, and unsupported external media hosts are skipped. There is no DRM decryption, paywall bypass, purchase automation, or access-control bypass. Use only your own account and content you are entitled and permitted to save.

FILES AND PROGRESS

Completed files are atomically renamed from .part files. Interrupted files resume only when a server validator (ETag/Last-Modified) and a valid matching byte range are available. Otherwise they restart safely. Server length checks detect truncated responses, not every possible kind of file corruption. Existing nonempty files from the original script are trusted by media ID/name initially; previously truncated legacy files may need manual removal and re-download.

A local .ofdl.sqlite3 manifest avoids repeated downloads of the same media across content categories. Exact old paths are checked first. Only after an exact miss does a lazy per-creator metadata scan look for original flat/category/album layouts and the original gifs/ folder; file contents are not hashed or decoded for duplicate detection. Each creator tree is indexed at most once per run, and legacy manifest writes are batched. Full files and optional previews have distinct identities/names. Already indexed files are checked for matching size, not cryptographically re-hashed. Do not run two copies against the same download folder. The folder lock is acquired when writing starts.

Activity shows the newest newly completed media on the right. Image/GIF thumbnails are reduced in a background worker. For videos, a single poster frame is attempted only when ffmpeg is already available in PATH; otherwise a lightweight video placeholder is shown. Existing/legacy files are not previewed during adoption, avoiding thousands of thumbnail jobs on a large first migration. Open file always uses the operating system's normal viewer/player. Three progress bars run in the footer: the overall pipeline (discovery/paging, then queue percentage with an estimated time to finish), the per-run file count, and the current file with transfer speed. The header button switches between light and dark mode.

Optional automatic checks on the Downloads tab re-run a download every chosen number of minutes, but only while this window stays open; they never register an OS autostart and skip silently when the session or options are not ready.

Scan only makes account/API requests, but no media downloads or output-folder writes. It lists candidate files before existing-file checks, so its planned count is not a count of new downloads. Stop/pause are cooperative; an in-flight network operation may need to finish or hit its timeout first. Discovery happens before downloading, so the overall file total is unknown during the scan.

CREATOR SCOPE

All active subscriptions is the default for new settings. Start download or Scan only fetches your current active subscriptions automatically; no list needs to be loaded or highlighted. Skip creators always takes priority. An empty Skip creators field means no exclusions. Names, @names and profile URLs work, separated by commas, spaces or new lines.

Only these creators is optional. Choose it to enable the creator text box and enter a specific list, including creators with accessible purchases who are no longer active subscriptions. An empty list in this mode is an error, never a request to download everyone. The visible summary describes the effective scope; Activity reports the actual included/skipped counts once subscriptions are fetched. Merely changing options or opening the app never starts a download.

Earlier saved creator lists stay in Only these creators mode rather than being silently expanded. To switch to everyone, choose All active subscriptions and Save preferences. Disabled creator/date fields retain their values but do not affect the run. Days is enabled only for Last N days; Date is enabled only for Since date. All dates disables both.

Date filters use UTC and apply locally to posts, messages, purchases and stories. Undated media is excluded when a date filter is set. All dates + existing-file skipping is the safest repeat-run mode: it also finds newly added media on older posts. The old filename-derived “latest date” shortcut was deliberately removed because it can miss such files.

TROUBLESHOOTING

HTTP 400: Desktop 2.0.1 preserves the user-id header used for signing. A continuing rejection needs the safe diagnostic, not repeated logins. Activity now labels API vs browser failures. API diagnostics include allowlisted categories, response type, a small numeric code when available, a public rules fingerprint and a coarse clock hint. Unknown response text is withheld. A refresh-required category is not proof of bad credentials/signature; the server-Date clock hint is not an authoritative time check. Share only the Safe diagnostic line, not credentials or a full media log.

401: import a fresh session. 403: check account access, session and signing rules; a security challenge may prevent this client from working. 429: respect the service's rate limit and try later. HTTP redirects from the API/rules source are refused to avoid credential forwarding. Choose the final rules URL, not a redirect link. Downloads allow only HTTPS OnlyFans hosts and validate every redirect.

An incomplete scan is reported as an issue, not as a fully successful backup. API schema changes can require a code update. The app uses ordinary Requests connections with TLS verification enabled; it intentionally ignores .netrc and ambient proxy environment settings. Corporate proxies or unusual certificate setups may require an environment-specific adjustment by a developer.

No live authenticated OnlyFans integration test was performed when this package was built. See TEST_REPORT.md for the exact local verification performed.
"""


def readable_bytes(value: float) -> str:
    for suffix in ('B','KiB','MiB','GiB','TiB'):
        if value < 1024 or suffix == 'TiB':
            return f'{value:.1f} {suffix}'
        value /= 1024
    return ''


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f'{seconds // 3600}h {seconds % 3600 // 60:02d}m'
    if seconds >= 60:
        return f'{seconds // 60}m {seconds % 60:02d}s'
    return f'{seconds}s'


LIGHT_PALETTE = {
    'window_bg':'#f3f5f8','text':'#243447','muted':'#5d6d7e','heading':'#15263b',
    'border':'#d9e0e8','field':'#ffffff','field_disabled':'#e8edf2','field_disabled_text':'#7c8996',
    'button_bg':'#e4eaf0','button_active':'#d4e0eb','button_text':'#243447',
    'accent':'#1769aa','accent_active':'#125b95','accent_disabled':'#afc1cf','accent_disabled_text':'#f1f4f7',
    'header_bg':'#15263b','header_text':'#ffffff','header_muted':'#bfcedd',
    'canvas':'#f3f5f8','progress_trough':'#e2e8ef','tab_bg':'#e4eaf0','tab_selected':'#ffffff',
}
DARK_PALETTE = {
    'window_bg':'#1e2229','text':'#e6e9ee','muted':'#9aa4b2','heading':'#f5f7fa',
    'border':'#3a4149','field':'#2a2f37','field_disabled':'#242830','field_disabled_text':'#6b7480',
    'button_bg':'#2f353d','button_active':'#3a424c','button_text':'#e6e9ee',
    'accent':'#3b82f6','accent_active':'#2f6fd6','accent_disabled':'#33415a','accent_disabled_text':'#7d8aa0',
    'header_bg':'#11151a','header_text':'#ffffff','header_muted':'#9aa4b2',
    'canvas':'#1e2229','progress_trough':'#2a2f37','tab_bg':'#2a2f37','tab_selected':'#1e2229',
}


class App:
    def __init__(self, root: tk.Tk, *, preferences_path: Path | None = None):
        self.root = root
        self.preferences_path = preferences_path
        self.worker: threading.Thread | None = None
        self.control = Control()
        self.events: queue.Queue[tuple[str,Any]] = queue.Queue()
        self.success_callback: Callable[[Any],None] | None = None
        self.task_title = ''
        self.task_quiet = False
        self.closing = False
        self.poll_id: str | None = None
        self.imported_auth: Credentials | None = None
        self.action_widgets: list[Any] = []
        self.auth_vars = {name:tk.StringVar(root) for name in ('user_id','user_agent','x_bc','sess_cookie')}
        self.credential_entries: dict[str,ttk.Entry] = {}
        self.show_values = tk.BooleanVar(root,False)
        self.status = tk.StringVar(root,'Ready')
        self.session_status = tk.StringVar(root,'No session loaded · credentials stay in memory unless explicitly saved')
        self.rules_status = tk.StringVar(root,'Rules are refreshed on demand and cached for 15 minutes.')
        self.file_status = tk.StringVar(root,'No file in progress')
        self.stats = tk.StringVar(root,'No download run yet')
        self.preview_title = tk.StringVar(root,'Latest completed media')
        self.preview_details = tk.StringVar(root,'')
        self.preview_status = tk.StringVar(root,'No completed media to preview yet.')
        self.preview_path: Path | None = None
        self.preview_photo = None
        self.preview_generation = 0
        self.preview_requests: queue.Queue[Any] = queue.Queue(maxsize=1)
        self.output_dir = tk.StringVar(root,str(Path.home()/'Downloads'/'OnlyFans'))
        self.rules_source = tk.StringVar(root,DEFAULT_RULES_SOURCE)
        self.browser_channel = tk.StringVar(root,'Chromium')
        self.creator_mode = tk.StringVar(root, 'all')
        self.creator_summary = tk.StringVar(root, '')
        self.skip_profiles = tk.StringVar(root,'')
        self.days = tk.StringVar(root,'7')
        self.since = tk.StringVar(root,'')
        self.date_mode = tk.StringVar(root,'All dates')
        self.flags = {name:tk.BooleanVar(root,name != 'previews') for name in
                      ('photos','videos','audio','albums','subfolders','previews',*CATEGORIES)}
        self.theme_mode = tk.StringVar(root,'light')
        self.autoload_session = tk.BooleanVar(root,False)
        self.auto_check = tk.BooleanVar(root,False)
        self.auto_check_interval = tk.StringVar(root,'30')
        self.pipeline_status = tk.StringVar(root,'Overall progress: idle')
        self.overall_status = tk.StringVar(root,'Files: idle')
        self._loading = False
        self._auto_check_id: str | None = None
        self.pipeline_started = 0.0
        self.pipeline_total = 0
        self._apply_theme()
        self._build()
        self._apply_theme()
        self.preview_thread = threading.Thread(target=self._preview_loop,name='ofdl-preview',daemon=True)
        self.preview_thread.start()
        self._load_preferences()
        self.creator_mode.trace_add('write', self._sync_download_controls)
        self.creator_mode.trace_add('write', self._update_creator_summary)
        self.skip_profiles.trace_add('write', self._update_creator_summary)
        self.date_mode.trace_add('write', self._sync_download_controls)
        self._sync_download_controls()
        self._update_creator_summary()
        for var in [*self.auth_vars.values(), self.rules_source]:
            var.trace_add('write', self._session_inputs_changed)
        self.auto_check.trace_add('write', self._auto_check_changed)
        self.auto_check_interval.trace_add('write', self._auto_check_changed)
        self.autoload_session.trace_add('write', lambda *a: self._persist_silently())
        self._reschedule_auto_check()
        if self.autoload_session.get():
            root.after(300, self._autoload_session)
        root.protocol('WM_DELETE_WINDOW',self.close)
        root.bind('<Destroy>',self._on_destroy,add='+')
        self.poll_id = root.after(80,self._poll)

    def _apply_theme(self):
        p = DARK_PALETTE if self.theme_mode.get() == 'dark' else LIGHT_PALETTE
        self.root.title(f'OnlyFans DL · Desktop {__version__}')
        if not getattr(self,'_geometry_set',False):
            width = min(1060,max(950,self.root.winfo_screenwidth()-60))
            height = min(850,max(620,self.root.winfo_screenheight()-80))
            self.root.geometry(f'{width}x{height}')
            self.root.minsize(950,620)
            self._geometry_set = True
        family = 'Segoe UI' if os.name == 'nt' else 'DejaVu Sans'
        self.root.configure(bg=p['window_bg'])
        style = ttk.Style(self.root)
        style.theme_use('clam')
        self.root.option_add('*Font',(family,10))
        style.configure('.',font=(family,10),background=p['window_bg'],foreground=p['text'])
        style.configure('TFrame',background=p['window_bg'])
        style.configure('TLabel',background=p['window_bg'],foreground=p['text'])
        style.configure('Muted.TLabel',background=p['window_bg'],foreground=p['muted'])
        style.configure('Heading.TLabel',font=(family,17,'bold'),background=p['window_bg'],foreground=p['heading'])
        style.configure('TLabelframe',background=p['window_bg'],bordercolor=p['border'])
        style.configure('TLabelframe.Label',font=(family,10,'bold'),background=p['window_bg'],foreground=p['heading'])
        style.configure('TButton',padding=(12,7),background=p['button_bg'],foreground=p['button_text'],borderwidth=0)
        style.map('TButton',background=[('active',p['button_active']),('disabled',p['field_disabled'])],
                  foreground=[('disabled',p['field_disabled_text'])])
        style.configure('Accent.TButton',background=p['accent'],foreground='white',font=(family,10,'bold'))
        style.map('Accent.TButton',background=[('active',p['accent_active']),('disabled',p['accent_disabled'])],
                  foreground=[('disabled',p['accent_disabled_text'])])
        style.configure('TCheckbutton',background=p['window_bg'],foreground=p['text'])
        style.map('TCheckbutton',background=[('active',p['window_bg'])],foreground=[('disabled',p['field_disabled_text'])])
        style.configure('TRadiobutton',background=p['window_bg'],foreground=p['text'])
        style.map('TRadiobutton',background=[('active',p['window_bg'])],foreground=[('disabled',p['field_disabled_text'])])
        style.configure('TEntry',fieldbackground=p['field'],foreground=p['text'],insertcolor=p['text'],padding=5)
        style.map('TEntry', fieldbackground=[('disabled', p['field_disabled'])],
                  foreground=[('disabled', p['field_disabled_text'])])
        style.configure('TSpinbox', fieldbackground=p['field'], foreground=p['text'], insertcolor=p['text'], padding=5)
        style.map('TSpinbox', fieldbackground=[('disabled', p['field_disabled'])],
                  foreground=[('disabled', p['field_disabled_text'])])
        style.configure('TCombobox',fieldbackground=p['field'],foreground=p['text'],background=p['field'],
                        arrowcolor=p['text'],selectbackground=p['field'],selectforeground=p['text'],
                        bordercolor=p['border'],lightcolor=p['field'],darkcolor=p['field'])
        style.map('TCombobox',fieldbackground=[('readonly',p['field']),('disabled',p['field_disabled'])],
                  foreground=[('readonly',p['text']),('disabled',p['field_disabled_text'])],
                  background=[('readonly',p['field'])])
        for option in ('*TCombobox*Listbox.background','*TCombobox*Listbox.foreground'):
            self.root.option_add(option,p['field'] if option.endswith('background') else p['text'])
        self.root.option_add('*TCombobox*Listbox.selectBackground',p['accent'])
        self.root.option_add('*TCombobox*Listbox.selectForeground','white')
        style.configure('TNotebook',background=p['window_bg'],borderwidth=0)
        style.configure('TNotebook.Tab',padding=(22,10),background=p['tab_bg'],foreground=p['text'])
        style.map('TNotebook.Tab',background=[('selected',p['tab_selected'])],foreground=[('selected',p['accent'])])
        for orient in ('Vertical','Horizontal'):
            style.configure(f'{orient}.TScrollbar',background=p['button_bg'],troughcolor=p['window_bg'],
                            bordercolor=p['border'],arrowcolor=p['text'])
        style.configure('Horizontal.TProgressbar',background=p['accent'],troughcolor=p['progress_trough'],borderwidth=0)
        if hasattr(self,'header'):
            self.header.configure(bg=p['header_bg'])
            self.header_title.configure(bg=p['header_bg'],fg=p['header_text'])
            self.theme_button.configure(bg=p['header_bg'],fg=p['header_text'],
                                        activebackground=p['header_bg'],activeforeground=p['header_text'],
                                        text='Light mode' if self.theme_mode.get() == 'dark' else 'Dark mode')
        for canvas in (getattr(self,'session_canvas',None),getattr(self,'downloads_canvas',None)):
            if canvas is not None:
                canvas.configure(bg=p['canvas'])
        for widget in (getattr(self,'log_text',None),getattr(self,'help_text',None),getattr(self,'profiles_text',None)):
            if widget is not None:
                widget.configure(bg=p['field'],fg=p['text'],insertbackground=p['text'],selectbackground=p['accent'])

    def toggle_theme(self):
        self.theme_mode.set('light' if self.theme_mode.get() == 'dark' else 'dark')
        self._apply_theme()
        self._persist_silently()

    def button(self,parent,text,command,*,accent=False,busy=True,**pack):
        widget = ttk.Button(parent,text=text,command=command,style='Accent.TButton' if accent else 'TButton')
        widget.pack(**pack)
        if busy:
            self.action_widgets.append(widget)
        return widget

    def _build(self):
        palette = DARK_PALETTE if self.theme_mode.get() == 'dark' else LIGHT_PALETTE
        self.header = tk.Frame(self.root,bg=palette['header_bg'],height=82)
        self.header.pack(fill='x')
        self.header.pack_propagate(False)
        self.theme_button = tk.Button(self.header,text='Dark mode',command=self.toggle_theme,
                                      bg=palette['header_bg'],fg=palette['header_text'],relief='flat',
                                      activebackground=palette['header_bg'],activeforeground=palette['header_text'],
                                      borderwidth=0,highlightthickness=0,cursor='hand2',font=('TkDefaultFont',10,'bold'))
        self.theme_button.pack(side='right',padx=22)
        self.header_title = tk.Label(self.header,text='OF  /  DOWNLOADER',bg=palette['header_bg'],fg=palette['header_text'],font=('TkDefaultFont',20,'bold'))
        self.header_title.pack(side='left',padx=24)
        container = ttk.Frame(self.root,padding=(18,14,18,0))
        container.pack(fill='both',expand=True)
        self.tabs = ttk.Notebook(container)
        self.tabs.pack(fill='both',expand=True)
        self.session_tab = ttk.Frame(self.tabs)
        self.downloads_tab = ttk.Frame(self.tabs)
        self.activity_tab = ttk.Frame(self.tabs,padding=20)
        self.help_tab = ttk.Frame(self.tabs,padding=20)
        for frame,label in ((self.session_tab,'1  Session'),(self.downloads_tab,'2  Downloads'),(self.activity_tab,'3  Activity'),(self.help_tab,'Help & privacy')):
            self.tabs.add(frame,text=label)
        self.session_content,self.session_canvas = self._scrollable_content(self.session_tab)
        self.downloads_content,self.downloads_canvas = self._scrollable_content(self.downloads_tab)
        self.root.bind('<MouseWheel>',self._scroll_wheel,add='+')
        self.root.bind('<Button-4>',self._scroll_wheel,add='+')
        self.root.bind('<Button-5>',self._scroll_wheel,add='+')
        self._session_tab()
        self._downloads_tab()
        self._activity_tab()
        self._help_tab()
        footer = ttk.Frame(self.root,padding=(24,8,24,10))
        footer.pack(side='bottom',fill='x',before=container)
        ttk.Label(footer,textvariable=self.status,font=('TkDefaultFont',11,'bold')).pack(anchor='w')
        ttk.Label(footer,textvariable=self.pipeline_status,style='Muted.TLabel').pack(anchor='w',pady=(4,1))
        self.pipeline_progress = ttk.Progressbar(footer,mode='determinate',maximum=100)
        self.pipeline_progress.pack(fill='x')
        ttk.Label(footer,textvariable=self.overall_status,style='Muted.TLabel').pack(anchor='w',pady=(4,1))
        self.overall_progress = ttk.Progressbar(footer,mode='determinate',maximum=100)
        self.overall_progress.pack(fill='x')
        ttk.Label(footer,textvariable=self.file_status,style='Muted.TLabel').pack(anchor='w',pady=(4,1))
        self.file_progress = ttk.Progressbar(footer,mode='determinate',maximum=100)
        self.file_progress.pack(fill='x',pady=(0,6))
        controls = ttk.Frame(footer)
        controls.pack(fill='x')
        self.button(controls,'Start download',lambda:self.start_download(False),accent=True,side='left')
        self.button(controls,'Scan only',lambda:self.start_download(True),side='left',padx=(8,0))
        self.pause_button = self.button(controls,'Pause',self.pause,busy=False,side='left',padx=(8,0))
        self.stop_button = self.button(controls,'Stop',self.stop,busy=False,side='left',padx=(8,0))
        self.pause_button.state(['disabled'])
        self.stop_button.state(['disabled'])
        self.button(controls,'Open output folder',self.open_output,busy=False,side='right')

    def _scrollable_content(self,parent):
        canvas = tk.Canvas(parent,highlightthickness=0,bg='#f3f5f8',height=1)
        scrollbar = ttk.Scrollbar(parent,orient='vertical',command=canvas.yview)
        scrollbar.pack(side='right',fill='y')
        canvas.pack(side='left',fill='both',expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        content = ttk.Frame(canvas,padding=20)
        window = canvas.create_window((0,0),window=content,anchor='nw')
        content.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda event:canvas.itemconfigure(window,width=event.width))
        return content,canvas

    def _scroll_wheel(self,event):
        if isinstance(event.widget,(tk.Text,ttk.Combobox,ttk.Spinbox)):
            return
        for content,canvas in ((self.session_content,self.session_canvas),
                               (self.downloads_content,self.downloads_canvas)):
            widget = event.widget
            while widget is not None:
                if widget is content or widget is canvas:
                    if canvas.yview() == (0.0,1.0):
                        return
                    delta = getattr(event,'delta',0)
                    amount = (-1 if event.num==4 else 1) if event.num in (4,5) else (-1 if delta>0 else 1)
                    canvas.yview_scroll(amount*3,'units')
                    return 'break'
                widget = getattr(widget,'master',None)

    def _session_tab(self):
        parent = self.session_content
        ttk.Label(parent,text='Connect your account',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(parent,text='Use either setup method below, then test the session.',style='Muted.TLabel').pack(anchor='w',pady=(4,14))
        methods = ttk.Frame(parent)
        methods.pack(fill='x')
        self.button(methods,'Browser login',self.browser_login,accent=True,side='left')
        combo = ttk.Combobox(methods,textvariable=self.browser_channel,values=['Chromium','Google Chrome','Microsoft Edge'],state='readonly',width=17)
        combo.pack(side='left',padx=(8,16))
        self.button(methods,'Paste copied request',self.import_dialog,side='left')
        self.button(methods,'Import JSON / HAR',self.import_file,side='left',padx=(8,0))
        fields = ttk.LabelFrame(parent,text='Session fields · automatically filled by either setup method',padding=12)
        fields.pack(fill='x',pady=(16,10))
        fields.columnconfigure(1,weight=1)
        for row,(name,label) in enumerate((('user_id','USER_ID'),('user_agent','USER_AGENT'),('x_bc','X_BC'),('sess_cookie','SESS_COOKIE'))):
            ttk.Label(fields,text=label,width=15).grid(row=row,column=0,sticky='w',pady=4)
            entry = ttk.Entry(fields,textvariable=self.auth_vars[name],show='' if name=='user_agent' else '*')
            entry.grid(row=row,column=1,sticky='ew',pady=4)
            self.credential_entries[name] = entry
        ttk.Checkbutton(fields,text='Show private values',variable=self.show_values,command=self.toggle_visibility).grid(row=4,column=1,sticky='w',pady=(6,0))
        actions = ttk.Frame(parent)
        actions.pack(fill='x')
        self.button(actions,'Test session',self.test_session,accent=True,side='left')
        self.button(actions,'Save session securely',self.save_keychain,side='left',padx=(8,0))
        self.button(actions,'Load saved',self.load_keychain,side='left',padx=(8,0))
        self.button(actions,'Forget saved',self.forget_keychain,side='left',padx=(8,0))
        self.button(actions,'Clear fields',self.clear_fields,side='left',padx=(8,0))
        ttk.Checkbutton(parent,text='Load the saved session automatically when the app starts',
                        variable=self.autoload_session,command=self._persist_silently).pack(anchor='w',pady=(10,0))
        ttk.Label(parent,textvariable=self.session_status,style='Muted.TLabel',wraplength=900).pack(anchor='w',pady=(10,12))
        rules = ttk.LabelFrame(parent,text='Signing rules · independent of your login details',padding=12)
        rules.pack(fill='x')
        row = ttk.Frame(rules)
        row.pack(fill='x')
        ttk.Entry(row,textvariable=self.rules_source).pack(side='left',fill='x',expand=True)
        self.button(row,'Browse…',self.browse_rules,side='left',padx=(8,0))
        self.button(row,'Refresh rules',self.refresh_rules,side='left',padx=(8,0))
        ttk.Label(rules,textvariable=self.rules_status,style='Muted.TLabel',wraplength=900).pack(anchor='w',pady=(8,0))
        ttk.Label(parent,text='Never share session values or copied requests. Save session uses your OS credential store, not a plaintext configuration file.',
                  wraplength=900,style='Muted.TLabel').pack(anchor='w',pady=(12,0))

    def _downloads_tab(self):
        parent = self.downloads_content
        ttk.Label(parent, text='Choose what to download', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(parent, text='Choose everyone or a specific list. Add exceptions in Skip creators, then click Start download.',
                  style='Muted.TLabel', wraplength=850).pack(anchor='w', pady=(4, 12))
        creators = ttk.LabelFrame(parent, text='Creators', padding=12)
        creators.pack(fill='x', pady=(0, 12))
        for value, label in (('all', 'All active subscriptions (automatic)'),
                             ('only', 'Only these creators (optional)')):
            radio = ttk.Radiobutton(creators, text=label, variable=self.creator_mode, value=value)
            radio.pack(anchor='w', pady=(0, 5))
            self.action_widgets.append(radio)
        ttk.Label(creators, text='Specific creator usernames or profile URLs, separated by commas or new lines.',
                  style='Muted.TLabel').pack(anchor='w', pady=(4, 5))
        self.profiles_text = scrolledtext.ScrolledText(creators, height=2, wrap='word', undo=False,
                                                       borderwidth=1, relief='solid', font=('TkDefaultFont', 10))
        self.profiles_text.pack(fill='x')
        self.profiles_text.bind('<<Modified>>', self._profiles_modified)
        self.profiles_text.edit_modified(False)
        row = ttk.Frame(creators)
        row.pack(fill='x', pady=(10, 4))
        ttk.Label(row, text='Skip creators', width=15).pack(side='left')
        self.skip_entry = ttk.Entry(row, textvariable=self.skip_profiles)
        self.skip_entry.pack(side='left', fill='x', expand=True)
        self.action_widgets.append(self.skip_entry)
        ttk.Label(creators, text='Optional: leave empty to skip nobody. Skip creators overrides either mode above.',
                  style='Muted.TLabel', wraplength=850).pack(anchor='w')
        ttk.Separator(creators).pack(fill='x', pady=(10, 8))
        ttk.Label(creators, textvariable=self.creator_summary, wraplength=850).pack(anchor='w')
        output = ttk.Frame(parent)
        output.pack(fill='x')
        ttk.Label(output,text='Output folder',width=15).pack(side='left')
        ttk.Entry(output,textvariable=self.output_dir).pack(side='left',fill='x',expand=True)
        self.button(output,'Browse…',self.browse_output,side='left',padx=(8,0))
        types = ttk.LabelFrame(parent,text='What to download',padding=12)
        types.pack(fill='x',pady=(12,10))
        row = ttk.Frame(types)
        row.pack(fill='x')
        for name,label in (('photos','Photos / GIFs'),('videos','Videos'),('audio','Audio')):
            ttk.Checkbutton(row,text=label,variable=self.flags[name]).pack(side='left',padx=(0,24))
        row = ttk.Frame(types)
        row.pack(fill='x',pady=(10,0))
        for name in CATEGORIES:
            ttk.Checkbutton(row,text=name.title(),variable=self.flags[name]).pack(side='left',padx=(0,22))
        settings = ttk.LabelFrame(parent,text='Organization & date filter',padding=12)
        settings.pack(fill='x')
        row = ttk.Frame(settings)
        row.pack(fill='x')
        ttk.Checkbutton(row,text='Photo albums',variable=self.flags['albums']).pack(side='left',padx=(0,20))
        ttk.Checkbutton(row,text='Category subfolders',variable=self.flags['subfolders']).pack(side='left',padx=(0,20))
        ttk.Checkbutton(row,text='Allow previews when full files are absent',variable=self.flags['previews']).pack(side='left')
        row = ttk.Frame(settings)
        row.pack(fill='x',pady=(12,0))
        self.date_choice = ttk.Combobox(row, textvariable=self.date_mode,
                                       values=['All dates', 'Last N days', 'Since date'], state='readonly', width=14)
        self.date_choice.pack(side='left')
        self.days_label = ttk.Label(row, text='Days')
        self.days_label.pack(side='left', padx=(18, 6))
        self.days_spinbox = ttk.Spinbox(row, from_=1, to=36500, textvariable=self.days, width=6)
        self.days_spinbox.pack(side='left')
        self.since_label = ttk.Label(row, text='Date (YYYY-MM-DD, UTC)')
        self.since_label.pack(side='left', padx=(18, 6))
        self.since_entry = ttk.Entry(row, textvariable=self.since, width=14)
        self.since_entry.pack(side='left')
        self.action_widgets.extend((self.date_choice, self.days_spinbox, self.since_entry))
        ttk.Label(parent,text='Existing files are skipped automatically. All dates is safest for repeat runs; it also catches media added to older posts.',wraplength=900,style='Muted.TLabel').pack(anchor='w',pady=(12,0))
        auto = ttk.LabelFrame(parent,text='Automatic checks while the app is open',padding=12)
        auto.pack(fill='x',pady=(12,0))
        row = ttk.Frame(auto)
        row.pack(fill='x')
        ttk.Checkbutton(row,text='Check for new content automatically',variable=self.auto_check).pack(side='left')
        ttk.Label(row,text='every').pack(side='left',padx=(14,6))
        self.auto_check_spinbox = ttk.Spinbox(row,from_=1,to=10080,textvariable=self.auto_check_interval,width=6)
        self.auto_check_spinbox.pack(side='left')
        ttk.Label(row,text='minutes').pack(side='left',padx=(6,0))
        ttk.Label(auto,text='Runs only while this window stays open; it never starts the app or a download that is not already configured. Default is 30 minutes.',
                  style='Muted.TLabel',wraplength=900).pack(anchor='w',pady=(6,0))
        row = ttk.Frame(parent)
        row.pack(fill='x',pady=(12,0))
        self.button(row,'Save preferences',self.save_settings,side='left')
        ttk.Label(row,text='Saves only nonsecret settings—not your session.',style='Muted.TLabel').pack(side='left',padx=12)

    def _sync_download_controls(self, *args):
        """Reapply conditional states, including after global busy-state restoration."""
        busy = self.worker is not None
        only = self.creator_mode.get() == 'only' and not busy
        self.profiles_text.configure(state='normal' if only else 'disabled',
                                     background='white' if only else '#e8edf2',
                                     foreground='#243447' if only else '#7c8996')
        for widget, label, mode in ((self.days_spinbox, self.days_label, 'Last N days'),
                                    (self.since_entry, self.since_label, 'Since date')):
            enabled = not busy and self.date_mode.get() == mode
            widget.state(['!disabled'] if enabled else ['disabled'])
            label.configure(style='TLabel' if enabled else 'Muted.TLabel')

    def _profiles_modified(self, event=None):
        if self.profiles_text.edit_modified():
            self.profiles_text.edit_modified(False)
            self._update_creator_summary()

    def _update_creator_summary(self, *args):
        try:
            excluded = set(parse_profiles(self.skip_profiles.get()))
        except AppError:
            self.creator_summary.set('Check Skip creators: enter usernames or HTTPS profile URLs, not post links or folder paths.')
            return
        if self.creator_mode.get() == 'all':
            if excluded:
                count = len(excluded)
                self.creator_summary.set(f'All active subscriptions, except {count} creator(s) in Skip creators. The current list is fetched when you start.')
            else:
                self.creator_summary.set('All active subscriptions will be included. Skip creators is empty; no selection step is needed.')
            return
        try:
            names = parse_profiles(self.profiles_text.get('1.0', 'end-1c'))
        except AppError:
            self.creator_summary.set('Check Only these creators: enter usernames or HTTPS profile URLs, separated by commas or new lines.')
            return
        included = [name for name in names if name not in excluded]
        if not names:
            self.creator_summary.set('No creators selected. Enter names above, or choose All active subscriptions.')
        elif not included:
            self.creator_summary.set('No creators remain after Skip creators; nothing will be downloaded. Change the list or exclusions.')
        else:
            preview = ', '.join(included[:6])
            if len(included) > 6:
                preview += f' (+{len(included) - 6} more)'
            self.creator_summary.set(f'Only this list: {len(included)} included, {len(names) - len(included)} skipped. Included: {preview}')

    def _activity_tab(self):
        parent = self.activity_tab
        ttk.Label(parent,text='Activity & results',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(parent,textvariable=self.stats,wraplength=900).pack(anchor='w',pady=(8,14))

        content = ttk.Frame(parent)
        content.pack(fill='both',expand=True)
        right = ttk.Frame(content,width=390,padding=(16,0,0,0))
        right.pack(side='right',fill='y')
        right.pack_propagate(False)
        left = ttk.Frame(content)
        left.pack(side='left',fill='both',expand=True)

        self.log_text = scrolledtext.ScrolledText(left,wrap='word',state='disabled',width=40,height=20,font=('TkFixedFont',9),borderwidth=0,padx=10,pady=10)
        self.log_text.pack(fill='both',expand=True)
        row = ttk.Frame(left)
        row.pack(fill='x',pady=(12,0))
        self.button(row,'Save log…',self.save_log,busy=False,side='left')
        self.button(row,'Clear log',self.clear_log,busy=False,side='left',padx=(8,0))
        ttk.Label(row,text='Logs omit session headers and signed download URLs.',style='Muted.TLabel').pack(side='right')

        ttk.Label(right,text='Latest media',font=('TkDefaultFont',11,'bold')).pack(anchor='w')
        # Bottom items are packed first so the Open button always stays visible; the
        # preview frame expands to absorb whatever height the window offers.
        self.preview_open_button = self.button(right,'Open file',self.open_preview,busy=False,side='bottom',anchor='w')
        self.preview_open_button.state(['disabled'])
        ttk.Label(right,textvariable=self.preview_status,style='Muted.TLabel',wraplength=360,justify='left').pack(side='bottom',anchor='w',pady=(8,10))
        ttk.Label(right,textvariable=self.preview_details,style='Muted.TLabel',wraplength=360,justify='left').pack(side='bottom',anchor='w',pady=(5,0))
        ttk.Label(right,textvariable=self.preview_title,font=('TkDefaultFont',10,'bold'),wraplength=360).pack(side='bottom',anchor='w')
        preview_box = ttk.Frame(right)
        preview_box.pack(fill='both',expand=True,pady=(10,10))
        self.preview_image_label = ttk.Label(preview_box,text='No preview yet',anchor='center',justify='center')
        self.preview_image_label.pack(fill='both',expand=True)

    def _help_tab(self):
        parent = self.help_tab
        ttk.Label(parent,text='Setup, privacy & troubleshooting',style='Heading.TLabel').pack(anchor='w',pady=(0,12))
        text = scrolledtext.ScrolledText(parent,wrap='word',font=('TkDefaultFont',10),borderwidth=0,padx=14,pady=12)
        text.pack(fill='both',expand=True)
        text.insert('1.0',HELP_TEXT)
        text.configure(state='disabled')

    def _preview_loop(self):
        while True:
            item = self.preview_requests.get()
            if item is None:
                return
            token,payload = item
            try:
                result = build_preview(Path(payload['path']),str(payload['kind']),(320,240))
            except Exception:
                result = PreviewResult(str(payload.get('kind','file')),None,'Preview generation failed; use Open file.')
            self.events.put(('preview_ready',(token,payload,result)))

    def _queue_preview(self,payload: dict[str,Any]):
        path = Path(str(payload.get('path','')))
        self.preview_generation += 1
        token = self.preview_generation
        self.preview_path = path
        profile = str(payload.get('profile',''))
        kind = str(payload.get('kind','file'))
        relative = str(payload.get('relative',path.name))
        size = int(payload.get('size',0) or 0)
        self.preview_title.set(f'@{profile} · {kind}' if profile else kind.title())
        self.preview_details.set(f'{relative}\n{readable_bytes(size)}' if size else relative)
        self.preview_status.set('Preparing preview…')
        self.preview_photo = None
        self.preview_image_label.configure(image='',text='Preparing preview…')
        if path.is_file():
            self.preview_open_button.state(['!disabled'])
        else:
            self.preview_open_button.state(['disabled'])
        try:
            while True:
                self.preview_requests.get_nowait()
        except queue.Empty:
            pass
        try:
            self.preview_requests.put_nowait((token,payload))
        except queue.Full:
            pass

    def _apply_preview(self,item):
        token,payload,result = item
        if token != self.preview_generation:
            return
        self.preview_status.set(result.message)
        if result.png:
            try:
                encoded = base64.b64encode(result.png).decode('ascii')
                self.preview_photo = tk.PhotoImage(data=encoded)
                self.preview_image_label.configure(image=self.preview_photo,text='')
                return
            except tk.TclError:
                self.preview_photo = None
                self.preview_status.set('Preview could not be displayed; use Open file.')
        label = {'video':'VIDEO','audio':'AUDIO','photo':'IMAGE','gif':'GIF'}.get(str(payload.get('kind')),'FILE')
        self.preview_image_label.configure(image='',text=label)

    def open_preview(self):
        path = self.preview_path
        if path is None or not path.is_file():
            messagebox.showinfo('Media preview','The selected file is no longer available.',parent=self.root)
            return
        try:
            if os.name == 'nt':
                os.startfile(path)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open',str(path)])
            else:
                subprocess.Popen(['xdg-open',str(path)])
        except OSError:
            messagebox.showerror('Open file','The operating system could not open this file.',parent=self.root)

    def toggle_visibility(self):
        for name,entry in self.credential_entries.items():
            entry.configure(show='' if self.show_values.get() or name=='user_agent' else '*')

    def _mask_credentials(self):
        self.show_values.set(False)
        self.toggle_visibility()

    def _session_inputs_changed(self, *args):
        self.session_status.set('Session fields or rules source changed · test again before downloading')

    def get_auth(self) -> Credentials:
        fields = {name:var.get().strip() for name,var in self.auth_vars.items()}
        extras = {}
        if self.imported_auth and all(getattr(self.imported_auth,name)==value for name,value in fields.items()):
            extras = dict(self.imported_auth.extra_cookies)
        return Credentials(**fields,extra_cookies=extras).validate()

    def apply_credentials(self,auth: Credentials):
        auth.validate()
        self._mask_credentials()
        self.imported_auth = auth
        for name,var in self.auth_vars.items():
            var.set(getattr(auth,name))
        self.session_status.set('Session fields loaded · not yet tested · not saved unless you choose Save session securely')
        self.log('Session fields loaded; values are not logged.')

    def clear_fields(self):
        self._mask_credentials()
        self.imported_auth = None
        for var in self.auth_vars.values():
            var.set('')
        self.session_status.set('Session fields cleared. A saved keychain entry, if any, is unchanged.')

    def import_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title('Import your copied request')
        dialog.geometry('850x510')
        dialog.transient(self.root)
        parent = ttk.Frame(dialog,padding=18)
        parent.pack(fill='both',expand=True)
        ttk.Label(parent,text='Paste one logged-in API request',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(parent,text='Accepts cURL, raw request headers, or session JSON. Parsed locally; never executed. This text contains private session credentials.',wraplength=790,style='Muted.TLabel').pack(anchor='w',pady=(8,12))
        text = scrolledtext.ScrolledText(parent,height=12,wrap='word',undo=False,font=('TkFixedFont',10))
        text.pack(fill='both',expand=True)
        clear_clip = tk.BooleanVar(dialog,False)
        ttk.Checkbutton(parent,text='Clear the current clipboard after import (clipboard history is not erased)',variable=clear_clip).pack(anchor='w',pady=10)
        actions = ttk.Frame(parent)
        actions.pack(fill='x')
        def paste():
            try:
                value = self.root.clipboard_get()
                if len(value.encode('utf-8')) > MAX_IMPORT_BYTES:
                    raise SessionError('Clipboard content exceeds 10 MB; copy one API request.')
                text.delete('1.0','end')
                text.insert('1.0',value)
            except (tk.TclError,SessionError):
                messagebox.showerror('Clipboard','Could not read suitable request text from the clipboard.',parent=dialog)
        def apply():
            try:
                auth = parse_session(text.get('1.0','end-1c'))
                self.apply_credentials(auth)
                text.delete('1.0','end')
                if clear_clip.get():
                    self.root.clipboard_clear()
                dialog.destroy()
            except SessionError as exc:
                messagebox.showerror('Import failed',str(exc),parent=dialog)
        self.button(actions,'Paste from clipboard',paste,busy=False,side='left')
        self.button(actions,'Import session',apply,accent=True,busy=False,side='right')
        self.button(actions,'Cancel',dialog.destroy,busy=False,side='right',padx=(0,8))
        text.focus_set()

    def import_file(self):
        path = filedialog.askopenfilename(title='Import request/session JSON or HAR',filetypes=[('Session / request files','*.json *.har *.txt'),('All files','*')])
        if not path:
            return
        try:
            if Path(path).stat().st_size > MAX_IMPORT_BYTES:
                raise SessionError('The file exceeds 10 MB. Copy one API request instead.')
            self.apply_credentials(parse_session(Path(path).read_text(encoding='utf-8-sig')))
        except (SessionError,OSError,UnicodeError) as exc:
            detail = str(exc) if isinstance(exc,SessionError) else 'Could not read the selected UTF-8 text file.'
            messagebox.showerror('Import failed',detail,parent=self.root)

    def browser_login(self):
        if not messagebox.askokcancel('Browser session helper','This opens an isolated temporary browser. Log into your own account and complete any normal verification. The app captures session headers locally and closes the helper browser.\n\nYour password is entered only on the website; it is not stored by this app. A blocked browser challenge is not bypassed. Continue?',parent=self.root):
            return
        channel = {'Chromium':'chromium','Google Chrome':'chrome','Microsoft Edge':'msedge'}[self.browser_channel.get()]
        self.run_task('Browser login',lambda control,emit:capture_session(control,channel=channel,emit=emit),self.apply_credentials)

    def _with_client(self,auth,source,control,emit,work):
        rules = load_rules(source,control,emit=emit)
        with ApiClient(auth,rules,control,emit=emit) as client:
            return work(client)

    def test_session(self):
        if self.worker is not None:
            return
        self._mask_credentials()
        try:
            auth = self.get_auth()
        except SessionError as exc:
            messagebox.showerror('Session needed',str(exc),parent=self.root)
            return
        source = self.rules_source.get().strip()
        self.session_status.set('Testing the imported session with the account endpoint · not ready yet')
        def accepted(user):
            try:
                unchanged = self.get_auth() == auth and self.rules_source.get().strip() == source
            except SessionError:
                unchanged = False
            if not unchanged:
                self.session_status.set('Session fields or rules source changed during the test · test again')
                self.log('The result applies to earlier inputs. Current inputs were not validated; test again.')
                return
            self.session_status.set('Session accepted by the account endpoint · ready to select downloads')
            self.log('Session test passed. Content availability is checked separately during the scan.')
        self.run_task('Testing session',lambda control,emit:self._with_client(auth,source,control,emit,lambda client:client.user('me')),accepted)

    def refresh_rules(self):
        source = self.rules_source.get().strip()
        self.run_task('Refreshing public signing rules',lambda control,emit:load_rules(source,control,force=True,emit=emit),
                      lambda result:self.rules_status.set('Rules refreshed and schema-checked. Use Test session to check whether the platform accepts them.'))

    def save_keychain(self):
        try:
            auth = self.get_auth()
        except SessionError as exc:
            messagebox.showerror('Session needed',str(exc),parent=self.root)
            return
        self.run_task('Saving to OS credential store',lambda control,emit:save_session(auth),
                      lambda result:self.session_status.set('Session saved in the supported OS credential store. Use Load saved on your next visit.'))

    def load_keychain(self):
        self.run_task('Loading from OS credential store',lambda control,emit:load_session(),self.apply_credentials)

    def forget_keychain(self):
        if messagebox.askyesno('Forget saved session','Remove the saved OS-keychain entry? This will not log you out of the website or clear the current form.',parent=self.root):
            self.run_task('Removing saved session',lambda control,emit:forget_session(),
                          lambda result:self.session_status.set('Saved keychain entry removed. Current form and website session are unchanged.'))

    def get_options(self,scan_only: bool) -> Options:
        mode = self.creator_mode.get()
        if mode not in {'all', 'only'}:
            raise AppError('Choose All active subscriptions or Only these creators.')
        names = parse_profiles(self.profiles_text.get('1.0', 'end-1c')) if mode == 'only' else []
        excluded = tuple(parse_profiles(self.skip_profiles.get()))
        if self.date_mode.get() not in {'All dates', 'Last N days', 'Since date'}:
            raise AppError('Choose All dates, Last N days, or Since date.')
        since = None
        if self.date_mode.get() == 'Last N days':
            try:
                days = int(self.days.get())
                if not 1 <= days <= 36500:
                    raise ValueError
            except ValueError:
                raise AppError('Days must be an integer between 1 and 36500.') from None
            since = datetime.now(timezone.utc)-timedelta(days=days)
        elif self.date_mode.get() == 'Since date':
            try:
                since = datetime.strptime(self.since.get().strip(),'%Y-%m-%d').replace(tzinfo=timezone.utc)
            except ValueError:
                raise AppError('Enter a valid earliest date in YYYY-MM-DD format.') from None
        output = self.output_dir.get().strip()
        if not output:
            raise AppError('Choose an output folder.')
        values = {name:var.get() for name,var in self.flags.items()}
        return Options(Path(output),names,tuple(name for name in CATEGORIES if values[name]),
                       **{name:values[name] for name in ('photos','videos','audio','albums','subfolders','previews')},
                       since=since,dry_run=scan_only,all_subscriptions=mode == 'all',
                       skip_profiles=excluded).validate()

    def start_download(self,scan_only: bool,*,auto: bool=False):
        try:
            auth = self.get_auth()
            options = self.get_options(scan_only)
        except (AppError,SessionError) as exc:
            if auto:
                self.log(f'Automatic check skipped: {exc}')
                self.status.set('Automatic check skipped · fix the setup and try again')
                self._reschedule_auto_check()
                return
            messagebox.showerror('Check setup',str(exc),parent=self.root)
            return
        source = self.rules_source.get().strip()
        if not auto:
            self.tabs.select(self.activity_tab)
        try:
            self.pipeline_progress.stop()
        except tk.TclError:
            pass
        self.pipeline_progress.configure(mode='determinate',value=0)
        self.overall_progress.configure(value=0)
        self.file_progress.configure(value=0)
        self.pipeline_started = 0.0
        self.pipeline_total = 0
        self.pipeline_status.set('Overall progress: starting…')
        self.overall_status.set('Files: discovering…')
        self.file_status.set('Discovering media before building the download queue')
        self.stats.set('Starting a new scan…')
        def run(control,emit):
            return self._with_client(auth,source,control,emit,lambda client:run_downloads(client,options,control,emit))
        def completed(summary):
            if summary.errors:
                self.status.set('Finished with issues · check the activity log')
            else:
                self.status.set('Scan complete · no media downloaded' if scan_only else 'Download run finished')
            self.update_stats(summary.to_dict())
        self.run_task('Scanning accessible media',run,completed,quiet=auto)

    def run_task(self,title: str,work: Callable,success: Callable | None = None,*,quiet: bool = False):
        if self.worker is not None:
            messagebox.showinfo('Task already running','Stop the current operation before starting another.',parent=self.root)
            return
        self.control = Control()
        self.success_callback = success
        self.task_title = title
        self.task_quiet = quiet
        self.status.set(title)
        for widget in self.action_widgets:
            widget.state(['disabled'])
        self.stop_button.state(['!disabled'])
        self.pause_button.state(['!disabled'])
        self.pause_button.configure(text='Pause')
        def emit(kind,value):
            self.events.put((kind,value))
        def run():
            try:
                result = work(self.control,emit)
                emit('result',result)
            except Cancelled as exc:
                emit('cancelled',str(exc))
            except (AppError,SessionError) as exc:
                emit('error',str(exc))
            except Exception as exc:
                emit('error',f'Operation failed ({type(exc).__name__}). Check installed dependencies, file permissions, and disk space. Raw exception details were withheld to protect session data.')
            finally:
                emit('done',None)
        self.worker = threading.Thread(target=run,name='ofdl-worker',daemon=True)
        self._sync_download_controls()
        self.worker.start()

    def _poll(self):
        self.poll_id = None
        for _ in range(250):
            try:
                kind,value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'log':
                self.log(value)
            elif kind == 'media_complete':
                self._queue_preview(value)
            elif kind == 'preview_ready':
                self._apply_preview(value)
            elif kind == 'phase':
                self.status.set(value)
            elif kind == 'stats':
                self.update_stats(value)
            elif kind == 'pipeline':
                stage = value.get('stage')
                if stage == 'scan':
                    self.pipeline_started = 0.0
                    self.pipeline_progress.stop()
                    self.pipeline_progress.configure(mode='indeterminate')
                    self.pipeline_progress.start(15)
                    self.pipeline_status.set('Overall progress: discovering creators and paging new content…')
                    self.overall_status.set('Files: waiting for discovery to finish')
                elif stage == 'download':
                    self.pipeline_started = time.monotonic()
                    self.pipeline_total = int(value.get('total',0))
                    self.pipeline_progress.stop()
                    self.pipeline_progress.configure(mode='determinate',value=0)
                    self.pipeline_status.set(f'Overall progress: download queue ready · 0/{self.pipeline_total} files · ETA estimating…')
                elif stage == 'done':
                    self.pipeline_progress.stop()
                    self.pipeline_progress.configure(mode='determinate',value=100)
                    self.pipeline_status.set('Overall progress: finished')
            elif kind == 'overall':
                total = max(1,value['total'])
                done = value['done']
                self.overall_progress.configure(value=100*done/total)
                self.overall_status.set(f"Files: {done}/{value['total']} complete")
                if self.pipeline_started:
                    elapsed = time.monotonic()-self.pipeline_started
                    if done > 0 and elapsed > 0:
                        eta = f' · ETA {format_duration((value["total"]-done)*(elapsed/done))}'
                    else:
                        eta = ' · ETA estimating…'
                    self.pipeline_progress.stop()
                    self.pipeline_progress.configure(mode='determinate',value=100*done/total)
                    self.pipeline_status.set(f'Overall progress: {100*done//total}% · {done}/{value["total"]} files{eta}')
            elif kind == 'progress':
                self.file_progress.configure(value=100*value['done']/value['total'] if value['total'] else 0)
                total = readable_bytes(value['total']) if value['total'] else 'unknown size'
                self.file_status.set(f"{value['file']}   |   {readable_bytes(value['done'])} / {total}   |   {readable_bytes(value['speed'])}/s")
            elif kind == 'result':
                if not self.closing:
                    self.status.set('Operation finished')
                    if self.success_callback:
                        self.success_callback(value)
            elif kind == 'cancelled':
                if self.task_title == 'Testing session':
                    self.session_status.set('Session test stopped · not ready; test again before downloading')
                self.status.set('Stopped')
                self.log(value)
            elif kind == 'error':
                if self.task_title == 'Testing session':
                    self.session_status.set('Session test failed · not ready; see the safe diagnostic in Activity')
                self.status.set('Operation failed · check the activity log')
                self.log(value)
                if not self.closing and not self.task_quiet:
                    messagebox.showerror('Operation failed',value,parent=self.root)
            elif kind == 'done':
                self.worker = None
                self.success_callback = None
                self.task_title = ''
                self.task_quiet = False
                for widget in self.action_widgets:
                    widget.state(['!disabled'])
                self._sync_download_controls()
                self.stop_button.state(['disabled'])
                self.pause_button.state(['disabled'])
                self.pause_button.configure(text='Pause')
                if self.task_title == '' and self._auto_check_id is None:
                    self._reschedule_auto_check()
                if self.closing:
                    self.root.destroy()
                    return
        if self.root.winfo_exists():
            self.poll_id = self.root.after(80,self._poll)

    def update_stats(self,values):
        self.stats.set(f"Queued: {values['planned']}  ·  Downloaded: {values['downloaded']}  ·  Existing: {values['existing']}  ·  Duplicates: {values['duplicates']}\n"
                       f"Filtered: {values['filtered']}  ·  Unavailable / unsupported: {values['unavailable']}  ·  Issues: {values['errors']}  ·  Transferred on completed files: {readable_bytes(values['transferred_bytes'])}")

    def log(self,text: str):
        stamp = datetime.now().strftime('%H:%M:%S')
        self.log_text.configure(state='normal')
        self.log_text.insert('end',f'[{stamp}] {text}\n')
        count = int(self.log_text.index('end-1c').split('.')[0])
        if count > 5000:
            self.log_text.delete('1.0',f'{count-4000}.0')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    def clear_log(self):
        self.log_text.configure(state='normal')
        self.log_text.delete('1.0','end')
        self.log_text.configure(state='disabled')

    def save_log(self):
        path = filedialog.asksaveasfilename(title='Save activity log',defaultextension='.txt',filetypes=[('Text files','*.txt')])
        if path:
            try:
                Path(path).write_text(self.log_text.get('1.0','end-1c'),encoding='utf-8')
            except OSError:
                messagebox.showerror('Save failed','Could not write the selected log file.',parent=self.root)

    def pause(self):
        if self.worker:
            paused = self.control.toggle_pause()
            self.pause_button.configure(text='Resume' if paused else 'Pause')
            self.status.set('Paused · an in-flight request may finish first' if paused else 'Resuming')

    def stop(self):
        if self.worker:
            self.control.stop()
            self.status.set('Stop requested · waiting for the current operation to return')
            self.stop_button.state(['disabled'])

    def close(self):
        if self._auto_check_id is not None:
            try:
                self.root.after_cancel(self._auto_check_id)
            except tk.TclError:
                pass
            self._auto_check_id = None
        if self.worker:
            self.closing = True
            self.stop()
        else:
            self.root.destroy()

    def _on_destroy(self,event):
        if event.widget is self.root and self.poll_id is not None:
            try:
                self.root.after_cancel(self.poll_id)
            except tk.TclError:
                pass
            self.poll_id = None
        if event.widget is self.root and hasattr(self,'preview_requests'):
            try:
                while True:
                    self.preview_requests.get_nowait()
            except queue.Empty:
                pass
            try:
                self.preview_requests.put_nowait(None)
            except queue.Full:
                pass

    def browse_output(self):
        path = filedialog.askdirectory(title='Choose download folder')
        if path:
            self.output_dir.set(path)

    def browse_rules(self):
        path = filedialog.askopenfilename(title='Choose signing-rules JSON file',filetypes=[('JSON','*.json')])
        if path:
            self.rules_source.set(path)

    def open_output(self):
        try:
            path = Path(self.output_dir.get()).expanduser().resolve()
            if not path.is_dir():
                messagebox.showinfo('Output folder','This folder has not been created yet. It will be created when downloading starts.',parent=self.root)
                return
            if os.name == 'nt':
                os.startfile(path)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open',str(path)])
            else:
                subprocess.Popen(['xdg-open',str(path)])
        except OSError:
            messagebox.showerror('Open folder','The operating system could not open this folder.',parent=self.root)

    def _preference_values(self) -> dict[str,Any]:
        values = {'output_dir':self.output_dir.get(),'rules_source':self.rules_source.get(),
                  'skip_profiles':self.skip_profiles.get(),'profiles':self.profiles_text.get('1.0','end-1c'),
                  'browser_channel':self.browser_channel.get(),
                  'creator_mode':self.creator_mode.get(), 'date_mode':self.date_mode.get(),
                  'days':self.days.get(), 'since':self.since.get(),
                  'theme':self.theme_mode.get(), 'autoload_session':self.autoload_session.get(),
                  'auto_check':self.auto_check.get(), 'auto_check_interval':self.auto_check_interval.get()}
        values.update({name:var.get() for name,var in self.flags.items()})
        return values

    def _persist_silently(self):
        if self._loading:
            return
        try:
            save_preferences(self._preference_values(),self.preferences_path)
        except OSError:
            pass

    def _auto_check_changed(self, *args):
        if self._loading:
            return
        self._reschedule_auto_check()
        self._persist_silently()

    def _reschedule_auto_check(self):
        if self._auto_check_id is not None:
            try:
                self.root.after_cancel(self._auto_check_id)
            except tk.TclError:
                pass
            self._auto_check_id = None
        if self._loading or self.closing or not self.auto_check.get():
            return
        try:
            minutes = int(str(self.auto_check_interval.get()).strip())
        except (TypeError,ValueError):
            return
        if minutes < 1:
            return
        minutes = min(minutes,10080)
        self._auto_check_id = self.root.after(minutes*60_000,self._auto_check_fire)

    def _auto_check_fire(self):
        self._auto_check_id = None
        if self.closing or not self.auto_check.get():
            return
        if self.worker is not None:
            self._reschedule_auto_check()
            return
        self.log('Automatic check for new content started.')
        self.start_download(False,auto=True)

    def _autoload_session(self):
        if self.worker is not None or self.closing:
            return
        self.run_task('Loading saved session',lambda control,emit:load_session(),self.apply_credentials,quiet=True)

    def _load_preferences(self):
        self._loading = True
        try:
            self._load_preferences_inner()
        finally:
            self._loading = False

    def _load_preferences_inner(self):
        try:
            values = load_preferences(self.preferences_path)
        except ValueError as exc:
            self.log(str(exc))
            return
        for name, var in [('output_dir', self.output_dir), ('rules_source', self.rules_source)]:
            if name in values:
                var.set(str(values[name]))
        self.skip_profiles.set(str(values.get('skip_profiles', '')))
        if values.get('browser_channel') in {'Chromium', 'Google Chrome', 'Microsoft Edge'}:
            self.browser_channel.set(values['browser_channel'])
        # Old saves encoded All dates as days=0. New saves retain inactive values
        # and store the actual modes, so a dormant value cannot activate a filter.
        saved_days = str(values.get('days', '7')).strip()
        explicit_date_mode = values.get('date_mode') in {'All dates', 'Last N days', 'Since date'}
        self.days.set(saved_days if explicit_date_mode or saved_days not in {'', '0'} else '7')
        self.since.set(str(values.get('since', '')))
        if values.get('date_mode') in {'All dates', 'Last N days', 'Since date'}:
            self.date_mode.set(values['date_mode'])
        elif values.get('since'):
            self.date_mode.set('Since date')
        elif values.get('days') and saved_days != '0':
            self.date_mode.set('Last N days')
        else:
            self.date_mode.set('All dates')
        profiles = values.get('profiles', '')
        profiles = profiles if isinstance(profiles, str) else ''
        self.profiles_text.configure(state='normal')
        self.profiles_text.delete('1.0', 'end')
        self.profiles_text.insert('1.0', profiles)
        # Preserve earlier deliberate lists rather than silently widening their scope.
        mode = values.get('creator_mode')
        self.creator_mode.set(mode if mode in {'all', 'only'} else ('only' if profiles.strip() else 'all'))
        for name,var in self.flags.items():
            if isinstance(values.get(name),bool):
                var.set(values[name])
        self.theme_mode.set('dark' if values.get('theme') == 'dark' else 'light')
        self.autoload_session.set(bool(values.get('autoload_session', False)))
        self.auto_check.set(bool(values.get('auto_check', False)))
        interval = str(values.get('auto_check_interval', '30')).strip()
        self.auto_check_interval.set(interval if interval else '30')
        self._apply_theme()
        self._sync_download_controls()
        self._update_creator_summary()

    def save_settings(self):
        try:
            save_preferences(self._preference_values(),self.preferences_path)
            self.log('Nonsecret preferences saved; no session credentials were included.')
            self.status.set('Preferences saved')
        except OSError:
            messagebox.showerror('Save failed','Could not save preferences. Check application-folder permissions.',parent=self.root)


def launch():
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError,OSError):
            pass
    try:
        root = tk.Tk()
    except tk.TclError:
        raise SystemExit('A desktop display is required for the UI. Use --cli for headless operation.') from None
    App(root)
    root.mainloop()
