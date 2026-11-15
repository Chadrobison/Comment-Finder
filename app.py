"""Local Tk desktop UI. Only the main thread touches widgets."""
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk, filedialog, messagebox
from core import Collector, Settings, LIMITATION, StorageError, filtered, save_csv

RESULTS = Path.home() / 'Documents' / 'Comment Finder Results'

class App:
    def __init__(self, root, source_factory=None, results_root=RESULTS):
        self.root, self.source_factory, self.results_root = root, source_factory, results_root
        self.events, self.cancel_event = queue.Queue(), threading.Event()
        self.running, self.closing = False, False
        self.rows, self.visible, self.folder = [], [], None
        root.title('Comment Finder')
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
        ttk.Label(self.advanced, text='Login username (optional)').grid(row=2, column=0, sticky='w')
        ttk.Entry(self.advanced, textvariable=self.login).grid(row=2, column=1, sticky='ew')
        ttk.Button(self.advanced, text='Choose session file…', command=self.pick_session).grid(row=2, column=2)
        ttk.Button(self.advanced, text='Clear session', command=lambda: (self.login.set(''), self.session.set(''))).grid(row=2, column=3)
        ttk.Label(self.advanced, textvariable=self.session, wraplength=700).grid(row=3, column=0, columnspan=4, sticky='w')
        ttk.Label(self.advanced, wraplength=850, text='Limits bound how much is examined, not how many matches are found. This Instaloader version requires a session to read comments. Private content must also be accessible to your session. Session files contain sensitive authentication data and can execute code when loaded if malicious. Use only your own trusted file. No passwords are stored.').grid(row=4, column=0, columnspan=4, sticky='w', pady=6)
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

    def start(self):
        if self.running:
            return
        try:
            settings = Settings(self.author.get(), self.owner.get(), self.own.get(),
                **{k:v.get() for k,v in self.limits.items()}, login_user=self.login.get().strip(), session_file=self.session.get()).validate()
        except ValueError as error:
            messagebox.showerror('Check your settings', str(error))
            return
        if settings.session_file and not messagebox.askokcancel('Trust this session file?',
            'Loading a malicious Instaloader session file can execute code. Continue only if this is your own file from a trusted source. No password will be stored.'):
            return
        self.rows, self.folder = [], None
        self.refresh()
        self.cancel_event = threading.Event()
        self.running = True
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
            if kind == 'match':
                self.rows.append(data)
                dirty = True
            elif kind == 'folder':
                self.folder = Path(data)
            elif kind == 'progress':
                self.progress.set(f"Account: @{data['account']}  •  Post: {data['post'] or 'looking up posts'}  •  {data['parents']} comments + {data['replies']} replies examined  •  {data['matches']} matches")
            elif kind == 'done':
                self.running = False
                self.start_button.configure(state='normal')
                self.cancel_button.configure(state='disabled')
                self.status.set(data['message'] + ('\n' + data['detail'] if data.get('detail') else ''))
        if dirty:
            self.refresh()
        if self.closing and not self.running:
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
        if self.running:
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

    report = {'status': 'failed', 'mode': 'offline_synthetic', 'checks': [],
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
            with patch.object(instaloader.Instaloader, 'load_session_from_file', forbidden), \
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
    App(root)
    root.mainloop()
