"""Local Tk desktop UI. Only the main thread touches widgets."""
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk, filedialog, messagebox, simpledialog
import auth
from core import Collector, Settings, LIMITATION, StorageError, Stop, username, filtered, save_csv

APP_VERSION = '1.1.1'
RESULTS = Path.home() / 'Documents' / 'Comment Finder Results'

class App:
    def __init__(self, root, source_factory=None, results_root=RESULTS, session_store=None):
        self.root, self.source_factory, self.results_root = root, source_factory, results_root
        self.events, self.cancel_event = queue.Queue(), threading.Event()
        self.running, self.closing = False, False
        self.auth_running = False
        self.credentials = None
        self.session_store = session_store
        self.auth_cancel = threading.Event()
        self.login_dialog = None
        self.code_request = None
        self.rows, self.visible, self.folder = [], [], None
        root.title(f'Comment Finder {APP_VERSION}')
        root.geometry('1100x820')
        root.minsize(850, 720)
        style = ttk.Style(root)
        style.configure('TLabel', font=('Helvetica', 12))
        style.configure('TButton', padding=(12, 6))
        style.configure('Title.TLabel', font=('Helvetica', 25, 'bold'))
        style.configure('Treeview', rowheight=30, font=('Helvetica', 11))
        style.configure('Treeview.Heading', font=('Helvetica', 11, 'bold'))
        # Keep every control reachable on smaller displays, including expanded settings.
        self.canvas = tk.Canvas(root, highlightthickness=0)
        page_scroll = ttk.Scrollbar(root, orient='vertical', command=self.canvas.yview)
        page_scroll.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=page_scroll.set)
        outer = ttk.Frame(self.canvas, padding=22)
        page = self.canvas.create_window(0, 0, window=outer, anchor='nw')
        def layout(event=None):
            self.canvas.itemconfigure(page, width=self.canvas.winfo_width(),
                                      height=max(outer.winfo_reqheight(), self.canvas.winfo_height()))
            self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        outer.bind('<Configure>', layout)
        self.canvas.bind('<Configure>', layout)
        def wheel(event):
            if not isinstance(event.widget, (tk.Text, ttk.Treeview)):
                step = -1 if event.delta > 0 else 1
                self.canvas.yview_scroll(step * 3, 'units')
        root.bind('<MouseWheel>', wheel)
        ttk.Label(outer, text='Comment Finder', style='Title.TLabel').pack(anchor='w')
        ttk.Label(outer, text='A focused search. A clear record of what was checked.').pack(anchor='w', pady=(4, 18))
        account_bar = ttk.Frame(outer)
        account_bar.pack(fill='x', pady=(0, 12))
        self.auth_button = ttk.Button(account_bar, text='Log in to Instagram…', command=self.show_login)
        self.auth_button.pack(side='left')
        self.logout_button = ttk.Button(account_bar, text='Log out', command=self.logout, state='disabled')
        self.logout_button.pack(side='left', padx=8)
        self.account_status = tk.StringVar(value='Not logged in')
        ttk.Label(account_bar, textvariable=self.account_status).pack(side='left', padx=4)
        form = ttk.Frame(outer)
        form.pack(fill='x')
        self.author, self.owner = tk.StringVar(), tk.StringVar()
        self.own = tk.BooleanVar()
        for col, (label, var) in enumerate([('Whose comments?', self.author), ('Search comments on whose posts?', self.owner)]):
            box = ttk.Frame(form)
            box.grid(row=0, column=col, sticky='ew', padx=(0, 16))
            form.columnconfigure(col, weight=1)
            ttk.Label(box, text=label).pack(anchor='w')
            ttk.Entry(box, textvariable=var).pack(fill='x', pady=5)
        ttk.Checkbutton(outer, text='Also search their own posts', variable=self.own).pack(anchor='w', pady=5)
        self.advanced_button = ttk.Button(outer, text='▸ Advanced Settings', command=self.toggle_advanced)
        self.advanced_button.pack(anchor='w', pady=4)
        self.advanced = ttk.Frame(outer, padding=10)
        self.limits = {}
        for i, (key, label, default) in enumerate([('max_posts','Posts per account',100), ('max_comments','Comments per post',500), ('max_replies','Replies per comment',100), ('max_total','Comments + replies total',10000)]):
            var = tk.StringVar(value=str(default))
            self.limits[key] = var
            ttk.Label(self.advanced, text=label).grid(row=i//2, column=(i%2)*2, sticky='w', padx=4)
            ttk.Entry(self.advanced, textvariable=var, width=10).grid(row=i//2, column=(i%2)*2+1, padx=8, pady=3)
        self.login, self.session = tk.StringVar(), tk.StringVar()
        ttk.Label(self.advanced, text='Legacy session username').grid(row=2, column=0, sticky='w')
        ttk.Entry(self.advanced, textvariable=self.login).grid(row=2, column=1, sticky='ew')
        ttk.Button(self.advanced, text='Choose session file…', command=self.pick_session).grid(row=2, column=2)
        ttk.Button(self.advanced, text='Clear session', command=lambda: (self.login.set(''), self.session.set(''))).grid(row=2, column=3)
        ttk.Label(self.advanced, textvariable=self.session, wraplength=700).grid(row=3, column=0, columnspan=4, sticky='w')
        ttk.Label(self.advanced, wraplength=850, text='Limits bound how much is examined, not how many matches are found. Use Log in to Instagram above for normal sign-in. An optional legacy session file overrides in-app login for the next search. Private content must be accessible to that account. Legacy session files contain sensitive authentication data and can execute code when loaded if malicious. Use only your own trusted file. No passwords are stored.').grid(row=4, column=0, columnspan=4, sticky='w', pady=6)
        actions = ttk.Frame(outer)
        actions.pack(fill='x', pady=8)
        self.start_button = ttk.Button(actions, text='Start Search', command=self.start)
        self.start_button.pack(side='left')
        self.cancel_button = ttk.Button(actions, text='Cancel', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left', padx=8)
        self.progress = tk.StringVar(value='Ready • No search has been run')
        ttk.Label(outer, textvariable=self.progress, wraplength=1000).pack(anchor='w', pady=5)
        self.status = tk.StringVar(value='Enter two usernames to begin. Results stay on this Mac.')
        ttk.Label(outer, textvariable=self.status, wraplength=1000).pack(anchor='w', pady=5)
        ttk.Separator(outer).pack(fill='x', pady=8)
        filters = ttk.Frame(outer)
        filters.pack(fill='x')
        ttk.Label(filters, text='Search results').pack(side='left')
        self.query, self.kind = tk.StringVar(), tk.StringVar(value='All')
        ttk.Entry(filters, textvariable=self.query).pack(side='left', fill='x', expand=True, padx=10)
        ttk.Combobox(filters, textvariable=self.kind, values=['All', 'comment', 'reply'], state='readonly', width=12).pack(side='left')
        self.count = tk.StringVar(value='0 shown')
        ttk.Label(filters, textvariable=self.count).pack(side='left', padx=8)
        self.query.trace_add('write', lambda *a: self.refresh())
        self.kind.trace_add('write', lambda *a: self.refresh())
        table_box = ttk.Frame(outer)
        table_box.pack(fill='both', expand=True, pady=10)
        columns = ('timestamp_utc','author_username','post_owner','text','kind')
        self.table = ttk.Treeview(table_box, columns=columns, show='headings', selectmode='browse', height=7)
        for key, title, width in zip(columns, ['Date (UTC)','Author','Post owner','Comment text','Type'], [180,120,120,400,80]):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=60, stretch=key=='text')
        scrollbar = ttk.Scrollbar(table_box, command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.table.pack(fill='both', expand=True)
        self.table.bind('<<TreeviewSelect>>', self.select)
        self.detail = tk.Text(outer, height=4, wrap='word', font=('Helvetica',12), relief='solid', borderwidth=1)
        self.detail.pack(fill='x')
        self.detail.configure(state='disabled')
        bottom = ttk.Frame(outer)
        bottom.pack(fill='x', pady=8)
        self.open_button = ttk.Button(bottom, text='Open Post', command=self.open_post, state='disabled')
        self.open_button.pack(side='left')
        ttk.Button(bottom, text='Export Results…', command=self.export).pack(side='left', padx=8)
        ttk.Button(bottom, text='Open Results Folder', command=self.open_folder).pack(side='left')
        ttk.Label(outer, text=LIMITATION + ' Coverage and run status are saved beside each search.', wraplength=1000, foreground='#775716').pack(anchor='w', pady=4)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.poll_id = root.after(100, self.poll)

    def get_session_store(self):
        if self.session_store is None:
            self.session_store = auth.SessionStore()
        return self.session_store

    def restore_saved_session(self):
        """Production startup only: load this app's explicitly remembered JSON session."""
        if self.running or self.auth_running:
            return
        try:
            self.credentials = self.get_session_store().load()
            if self.credentials:
                self.account_status.set(f'Logged in as @{self.credentials.username} • remembered session')
                self.logout_button.configure(state='normal')
                self.status.set('Remembered session restored locally. Instagram will check whether it is still valid when you search.')
        except Exception:
            self.credentials = None
            self.account_status.set('Not logged in')
            self.status.set('The remembered session could not be read. Log in again or use Log out to remove it.')
            self.logout_button.configure(state='normal')

    def show_login(self):
        if self.running or self.auth_running or self.closing:
            return
        if self.login_dialog is not None and self.login_dialog.winfo_exists():
            self.login_dialog.lift()
            return
        dialog = self.login_dialog = tk.Toplevel(self.root)
        dialog.title('Log in to Instagram')
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=22)
        frame.pack(fill='both', expand=True)
        user = tk.StringVar(value=self.credentials.username if self.credentials else '')
        password = tk.StringVar()
        remember = tk.BooleanVar(value=False)
        # Exposed widget references permit offline integration tests, never persistence.
        self.login_username_var, self.login_password_var, self.remember_var = user, password, remember
        ttk.Label(frame, text='Your Instagram account', style='Title.TLabel').pack(anchor='w', pady=(0, 12))
        ttk.Label(frame, text='Username').pack(anchor='w')
        username_entry = ttk.Entry(frame, textvariable=user, width=42)
        username_entry.pack(fill='x', pady=(4, 10))
        ttk.Label(frame, text='Password').pack(anchor='w')
        self.password_entry = ttk.Entry(frame, textvariable=password, show='•', width=42)
        self.password_entry.pack(fill='x', pady=(4, 10))
        ttk.Checkbutton(frame, text='Remember this session on this Mac', variable=remember).pack(anchor='w')
        ttk.Label(frame, wraplength=430, text='Your password is used only for this login and is never saved. A remembered session grants account access and is saved only in this app’s private local folder. Instagram may ask for a verification code.').pack(anchor='w', pady=12)
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Log in', command=self.submit_login).pack(side='left')
        ttk.Button(buttons, text='Cancel', command=self.dismiss_login).pack(side='left', padx=8)
        dialog.protocol('WM_DELETE_WINDOW', self.dismiss_login)
        dialog.bind('<Return>', lambda event: self.submit_login())
        dialog.bind('<Escape>', lambda event: self.dismiss_login())
        username_entry.focus_set()

    def dismiss_login(self):
        if hasattr(self, 'login_password_var'):
            self.login_password_var.set('')
        if self.login_dialog is not None:
            self.login_dialog.destroy()
            self.login_dialog = None

    def submit_login(self):
        if self.auth_running or self.running or self.closing or self.login_dialog is None:
            return
        password = self.login_password_var.get()
        self.login_password_var.set('')
        try:
            login_user = username(self.login_username_var.get())
            if not password:
                raise ValueError('Enter your Instagram password. It will not be saved.')
        except ValueError as error:
            messagebox.showerror('Check your login', str(error), parent=self.login_dialog)
            return
        remember = self.remember_var.get()
        self.dismiss_login()
        self.auth_cancel = threading.Event()
        self.auth_running = True
        self.auth_button.configure(state='disabled')
        self.logout_button.configure(state='disabled')
        self.start_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.status.set('Logging in… You can cancel. Your password is not saved.')

        def request_code():
            request = {'ready': threading.Event(), 'code': None}
            self.events.put(('auth_code', request))
            while not request['ready'].wait(.1):
                if self.auth_cancel.is_set():
                    raise Stop('cancelled')
            code = request.pop('code', None)
            if self.auth_cancel.is_set():
                raise Stop('cancelled')
            return code

        def work():
            nonlocal password
            try:
                credentials = auth.login(login_user, password, self.auth_cancel, request_code,
                                         remember=False, store=self.get_session_store())
                if self.auth_cancel.is_set():
                    raise Stop('cancelled')
                warning = ''
                try:
                    if remember:
                        self.get_session_store().save(credentials)
                    else:
                        self.get_session_store().clear()
                except Exception:
                    warning = (' Login succeeded, but the remembered session could not be updated. '
                               'An older saved session may remain; use Log out to remove it.')
                if self.auth_cancel.is_set():
                    cleanup_warning = ''
                    if remember:
                        try:
                            self.get_session_store().clear()
                        except Exception:
                            cleanup_warning = ' The new saved session could not be removed; use Log out to retry.'
                    self.events.put(('auth_done', {'message': 'Login cancelled. No search was started.' + cleanup_warning,
                                                   'cleanup_failed': bool(cleanup_warning)}))
                    return
                self.events.put(('auth_done', {'credentials': credentials, 'remembered': remember and not warning,
                                               'save_attempted': remember,
                                               'message': 'Logged in. You can start your search.' + warning}))
            except Stop:
                self.events.put(('auth_done', {'message': 'Login cancelled. No search was started.'}))
            except auth.AuthError as error:
                self.events.put(('auth_done', {'message': str(error)}))
            except Exception:
                self.events.put(('auth_done', {'message': 'Login could not be completed. Check your connection and try again later.'}))
            finally:
                password = ''
        self.auth_worker = threading.Thread(target=work, daemon=False)
        self.auth_worker.start()

    def logout(self):
        if self.running or self.auth_running:
            return
        self.dismiss_login()
        self.credentials = None
        self.login.set('')
        self.session.set('')
        self.account_status.set('Not logged in')
        try:
            self.get_session_store().clear()
            self.status.set('Logged out of this app. Its remembered session was removed; other Instagram devices are unchanged.')
            self.logout_button.configure(state='disabled')
        except Exception:
            self.status.set('Logged out in this window, but the saved session could not be removed. It may be restored on next launch; try Log out again.')
            self.logout_button.configure(state='normal')

    def toggle_advanced(self):
        if self.advanced.winfo_manager():
            self.advanced.pack_forget()
            self.advanced_button.configure(text='▸ Advanced Settings')
        else:
            self.advanced.pack(after=self.advanced_button, fill='x')
            self.advanced_button.configure(text='▾ Advanced Settings')

    def pick_session(self):
        path = filedialog.askopenfilename(title='Choose your own trusted Instaloader session')
        if path:
            self.session.set(path)
            self.status.set('The selected legacy file will be used for the next search instead of in-app login.')

    def start(self):
        if self.running or self.auth_running or self.closing:
            return
        try:
            using_saved = self.credentials is not None and not self.session.get()
            settings = Settings(self.author.get(), self.owner.get(), self.own.get(),
                **{k:v.get() for k,v in self.limits.items()},
                login_user=self.credentials.username if using_saved else self.login.get().strip(),
                session_data=self.credentials.data if using_saved else None,
                session_file=self.session.get()).validate()
        except ValueError as error:
            messagebox.showerror('Check your settings', str(error))
            return
        if settings.session_file and not messagebox.askokcancel('Trust this session file?',
            'Loading a malicious Instaloader session file can execute code. Continue only if this is your own file from a trusted source. No password will be stored.'):
            return
        self.dismiss_login()
        self.rows, self.folder = [], None
        self.refresh()
        self.cancel_event = threading.Event()
        self.running = True
        self.auth_button.configure(state='disabled')
        self.logout_button.configure(state='disabled')
        self.start_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.status.set('Searching… Results are saved as they are found. Settings changes apply to the next search.')
        self.progress.set('Starting…')
        def work():
            try:
                if self.source_factory:
                    source = self.source_factory(settings, self.cancel_event)
                else:
                    from source import InstagramSource
                    source = InstagramSource(settings, self.cancel_event)
                Collector(settings, source, self.cancel_event, lambda k,v: self.events.put((k,v)), self.results_root).run()
            except Exception as error:
                self.events.put(('done', dict(message='Could not start collection: ' + str(error), detail='')))
        self.worker = threading.Thread(target=work, daemon=False)
        self.worker.start()

    def cancel(self):
        if self.auth_running:
            self.auth_cancel.set()
            if self.code_request is not None:
                self.code_request['ready'].set()
            self.cancel_button.configure(state='disabled')
            self.status.set('Cancelling login… An active request may need to finish. No new request will start.')
            return
        self.cancel_event.set()
        self.cancel_button.configure(state='disabled')
        self.status.set('Cancelling… An active request or library pause may need to finish. Requests use a 30-second socket timeout, not a total time limit. Collected results will be kept.')

    def poll(self):
        dirty = False
        for _ in range(500):
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'auth_code':
                self.code_request = data
                if not self.auth_cancel.is_set() and not self.closing:
                    self.status.set('Instagram requested a verification code. Enter it in the dialog, or cancel.')
                    data['code'] = simpledialog.askstring('Instagram verification code',
                        'Enter the code from your authenticator or Instagram message:', parent=self.root)
                    if data['code'] is None:
                        self.auth_cancel.set()
                if self.auth_cancel.is_set():
                    data['code'] = None
                data['ready'].set()
                self.code_request = None
            elif kind == 'auth_done':
                if self.auth_worker.is_alive():
                    self.events.put((kind, data))
                    break
                self.auth_running = False
                if data.get('credentials') is not None and self.auth_cancel.is_set():
                    data['message'] = 'Login cancelled. No search was started.'
                    if data.get('save_attempted'):
                        try:
                            self.get_session_store().clear()
                        except Exception:
                            data['message'] += ' The new saved session could not be removed; use Log out to retry.'
                            data['cleanup_failed'] = True
                    data.pop('credentials', None)
                if data.get('credentials') is not None:
                    self.credentials = data['credentials']
                    self.login.set('')
                    self.session.set('')
                    suffix = ' • remembered session' if data['remembered'] else ' • this window only'
                    self.account_status.set(f'Logged in as @{self.credentials.username}' + suffix)
                self.auth_button.configure(state='normal')
                self.logout_button.configure(state='normal' if self.credentials or data.get('cleanup_failed') else 'disabled')
                self.start_button.configure(state='normal')
                self.cancel_button.configure(state='disabled')
                self.status.set(data['message'])
            elif kind == 'match':
                self.rows.append(data)
                dirty = True
            elif kind == 'folder':
                self.folder = Path(data)
            elif kind == 'progress':
                self.progress.set(f"Account: @{data['account']}  •  Post: {data['post'] or 'looking up posts'}  •  {data['parents']} comments + {data['replies']} replies examined  •  {data['matches']} matches")
            elif kind == 'done':
                if hasattr(self, 'worker') and self.worker.is_alive():
                    self.events.put((kind, data))
                    break
                self.running = False
                self.auth_button.configure(state='normal')
                self.logout_button.configure(state='normal' if self.credentials else 'disabled')
                self.start_button.configure(state='normal')
                self.cancel_button.configure(state='disabled')
                self.status.set(data['message'] + ('\n' + data['detail'] if data.get('detail') else ''))
        if dirty:
            self.refresh()
        if self.closing and not self.running and not self.auth_running:
            self.root.destroy()
            return
        self.poll_id = self.root.after(100, self.poll)

    def refresh(self):
        selection = self.table.selection()
        selected = self.visible[int(selection[0])] if selection else None
        self.visible = filtered(self.rows, self.query.get(), self.kind.get())
        self.table.delete(*self.table.get_children())
        for i, row in enumerate(self.visible):
            self.table.insert('', 'end', iid=str(i), values=[row[k][:19].replace('T', ' ') if k == 'timestamp_utc' else row[k].replace('\n',' ') if isinstance(row[k],str) else row[k] for k in self.table['columns']])
        self.count.set(f'{len(self.visible)} shown / {len(self.rows)} collected')
        if selected in self.visible:
            self.table.selection_set(str(self.visible.index(selected)))
        self.select()

    def selected(self):
        ids = self.table.selection()
        return self.visible[int(ids[0])] if ids else None

    def select(self, event=None):
        row = self.selected()
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        if row:
            self.detail.insert('1.0', f"{row['timestamp_utc']}  •  @{row['author_username']}  •  {row['kind']}\n{row['text']}")
        self.detail.configure(state='disabled')
        self.open_button.configure(state='normal' if row else 'disabled')

    def open_post(self):
        row = self.selected()
        if row:
            webbrowser.open(row['post_url'])

    def export(self):
        path = filedialog.asksaveasfilename(title='Export currently filtered results', defaultextension='.csv', initialfile='filtered_comments.csv', filetypes=[('CSV','*.csv')])
        if path:
            try:
                save_csv(self.visible, path)
                messagebox.showinfo('Export saved', f'Exported {len(self.visible)} currently shown results. Coverage remains in the search folder.')
            except (OSError, ValueError, StorageError) as error:
                messagebox.showerror('Export failed', str(error))

    def open_folder(self):
        try:
            folder = self.folder or self.results_root
            folder.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(folder)])
        except OSError as error:
            messagebox.showerror('Could not open folder', str(error))

    def close(self):
        self.dismiss_login()
        if self.running or self.auth_running:
            self.closing = True
            self.cancel()
        else:
            self.root.after_cancel(self.poll_id)
            self.root.destroy()

def smoke_test(report_path=None):
    """Exercise the packaged GUI offline; never start a collection or load a session."""
    import json
    import platform
    import socket
    import traceback
    from unittest.mock import patch
    from core import atomic_write

    report = {'status': 'failed', 'mode': 'offline_synthetic', 'app_version': APP_VERSION, 'checks': [],
              'platform': platform.system(), 'architecture': platform.machine(),
              'frozen': bool(getattr(sys, 'frozen', False))}
    root = None

    def forbidden(*args, **kwargs):
        raise AssertionError('Network, collection and session loading are forbidden in smoke-test mode.')

    try:
        # Guard sockets before importing the backend. No requests can be sent,
        # even if future code accidentally adds networking to initialization.
        with patch.object(socket.socket, 'connect', forbidden), \
             patch.object(socket.socket, 'connect_ex', forbidden), \
             patch.object(socket, 'create_connection', forbidden):
            import certifi
            import instaloader
            from source import InstagramSource
            if not Path(certifi.where()).is_file():
                raise AssertionError('The bundled certificate file is missing.')
            report['instaloader_version'] = instaloader.__version__
            report['checks'].append('backend_import_and_certificates')
            import compatibility
            from types import SimpleNamespace
            calls = []
            def synthetic_query(doc_id, variables, referer=None, web_api=False):
                calls.append((doc_id, variables, referer, web_api))
                return {'synthetic_only': True}
            context = SimpleNamespace(doc_id_graphql_query=synthetic_query)
            if not compatibility.install_metadata_routes(context):
                raise AssertionError('The pinned metadata routing adapter was not active.')
            variables = {'synthetic_only': True}
            for metadata_id in compatibility._METADATA_PHASES:
                context.doc_id_graphql_query(metadata_id, variables)
                if calls[-1][3] is not True or calls[-1][1] is not variables:
                    raise AssertionError('Metadata routing did not preserve the query and select web_api.')
            context.doc_id_graphql_query('SYNTHETIC_UNRELATED_QUERY', variables)
            if calls[-1][3] is not False:
                raise AssertionError('Metadata routing changed an unrelated query.')
            report['checks'].append('metadata_routing_backport_active')
            with patch.object(instaloader.Instaloader, 'load_session_from_file', forbidden), \
                 patch.object(instaloader.Instaloader, 'load_session', forbidden), \
                 patch.object(auth.SessionStore, 'load', forbidden), \
                 patch.object(InstagramSource, 'posts', forbidden):
                root = tk.Tk()
                root.title('Comment Finder — OFFLINE SYNTHETIC SMOKE TEST')
                app = App(root, source_factory=forbidden)
                root.title('Comment Finder — OFFLINE SYNTHETIC SMOKE TEST')
                callback_errors = []

                def callback_error(exc_type, value, tb):
                    callback_errors.append(''.join(traceback.format_exception(exc_type, value, tb)))
                    root.quit()
                root.report_callback_exception = callback_error
                root.update_idletasks()
                if not root.winfo_ismapped():
                    raise AssertionError('The Tk window did not open.')
                report['checks'].append('tk_window_opened')
                if app.account_status.get() != 'Not logged in' or app.credentials is not None:
                    raise AssertionError('Smoke mode unexpectedly loaded authentication data.')
                report['checks'].append('login_ui_starts_without_session_loading')
                app.show_login()
                root.update_idletasks()
                if not app.password_entry.cget('show') or app.remember_var.get():
                    raise AssertionError('Login defaults must mask passwords and leave Remember unchecked.')
                app.login_password_var.set('SYNTHETIC_ONLY_PASSWORD')
                app.dismiss_login()
                if app.login_password_var.get():
                    raise AssertionError('Dismissing login did not clear the password field.')
                report['checks'].append('login_dialog_masked_password_and_opt_in_remember')
                row = dict(author_username='synthetic_author', post_owner='synthetic_owner',
                           post_id='SYNTHETIC_POST', post_shortcode='SYNTHETIC_ONLY',
                           post_url='https://example.invalid/SYNTHETIC_ONLY',
                           comment_id='SYNTHETIC_COMMENT', parent_comment_id='', kind='comment',
                           timestamp_unix=1767225600, timestamp_utc='2026-01-01T00:00:00+00:00',
                           text='SYNTHETIC fixture only. No account data.\nLong comment detail.')
                reply = dict(row, comment_id='SYNTHETIC_REPLY', parent_comment_id='SYNTHETIC_COMMENT',
                             kind='reply', text='SYNTHETIC reply fixture.')
                app.events.put(('match', row))
                app.events.put(('match', reply))
                app.events.put(('progress', dict(account='synthetic_owner', post='SYNTHETIC_ONLY',
                                                parents=1, replies=1, matches=2)))
                complete = []

                def verify():
                    try:
                        if len(app.rows) != 2 or len(app.table.get_children()) != 2:
                            raise AssertionError('Synthetic results were not displayed.')
                        if '2 matches' not in app.progress.get():
                            raise AssertionError('Progress did not update.')
                        report['checks'].append('queue_progress_and_results')
                        app.kind.set('reply')
                        if len(app.visible) != 1 or app.visible[0]['kind'] != 'reply':
                            raise AssertionError('Result type filtering failed.')
                        app.query.set('not-in-synthetic-data')
                        if app.visible:
                            raise AssertionError('Text filtering failed.')
                        app.query.set('SYNTHETIC')
                        app.table.selection_set('0')
                        app.select()
                        if 'SYNTHETIC reply fixture.' not in app.detail.get('1.0', 'end'):
                            raise AssertionError('Selected comment detail did not display.')
                        root.update_idletasks()
                        report['checks'].extend(['result_filtering', 'selected_comment_detail'])
                        complete.append(True)
                    except Exception:
                        callback_errors.append(traceback.format_exc())
                    finally:
                        root.quit()

                root.after(350, verify)
                root.after(8000, root.quit)
                root.mainloop()
                if callback_errors:
                    raise AssertionError('\n'.join(callback_errors))
                if not complete:
                    raise AssertionError('Smoke-test UI did not finish within eight seconds.')
                report['status'] = 'passed'
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        if root is not None:
            try:
                # Tk timers survive widget destruction inside the interpreter.
                # Cancel smoke callbacks, including App.poll, before teardown so
                # another Tk root in the test process cannot invoke deleted Tcl commands.
                for timer_id in root.tk.call('after', 'info'):
                    root.after_cancel(timer_id)
                root.destroy()
            except tk.TclError:
                pass
    if report_path:
        try:
            atomic_write(report_path, lambda stream: json.dump(report, stream, indent=2))
        except Exception:
            report['status'] = 'failed'
            report['report_write_error'] = traceback.format_exc()
    if sys.stdout is not None:
        print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Comment Finder local desktop app')
    parser.add_argument('--smoke-test', nargs='?', const='', metavar='REPORT_PATH',
                        help='Run an offline synthetic GUI check, optionally writing a JSON report, then exit.')
    arguments = parser.parse_args()
    if arguments.smoke_test is not None:
        sys.exit(smoke_test(arguments.smoke_test or None))
    root = tk.Tk()
    application = App(root)
    application.restore_saved_session()
    root.mainloop()
