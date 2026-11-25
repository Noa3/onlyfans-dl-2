"""Local duplicate-management tab widgets."""
from tkinter import ttk


class DuplicatesTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        ttk.Label(self,text='Duplicate files in the library',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(self,text='Compares media file contents (SHA-256). Extra copies are deleted only after confirmation and verification of the keeper.',
                  style='Muted.TLabel',wraplength=900).pack(anchor='w',pady=(4,12))
        folder = ttk.Frame(self)
        folder.pack(fill='x')
        ttk.Label(folder,text='Library folder',width=14).pack(side='left')
        ttk.Entry(folder,textvariable=app.duplicate_dir).pack(side='left',fill='x',expand=True)
        app.button(folder,'Browse…',app.browse_duplicate_dir,side='left',padx=(8,0))
        app.button(folder,'Scan for duplicates',app.scan_duplicates,accent=True,side='left',padx=(8,0))
        ttk.Label(self,textvariable=app.duplicate_status,style='Muted.TLabel',wraplength=900).pack(anchor='w',pady=(10,0))
        table = ttk.Frame(self)
        table.pack(fill='both',expand=True,pady=(10,0))
        self.tree = ttk.Treeview(table,columns=('file','size','status'),show='tree headings',
                                 selectmode='extended',height=12)
        self.tree.heading('#0',text='Duplicate group / file')
        self.tree.column('#0',width=520,stretch=True)
        for column,label,width in (('file','File',0),('size','Size',100),('status','Status',110)):
            self.tree.heading(column,text=label)
            self.tree.column(column,width=width,stretch=column == 'file',
                             anchor='e' if column != 'status' else 'w')
        scrollbar = ttk.Scrollbar(table,orient='vertical',command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side='left',fill='both',expand=True)
        scrollbar.pack(side='right',fill='y')
        actions = ttk.Frame(self)
        actions.pack(fill='x',pady=(10,0))
        app.button(actions,'Use selected copy as keeper',app.keep_selected_duplicate,side='left')
        app.button(actions,'Delete other copies',app.delete_duplicates,side='left',padx=(8,0))
        app.button(actions,'Open folder',app.open_duplicate_folder,busy=False,side='left',padx=(8,0))
        ttk.Label(self,textvariable=app.duplicate_summary,style='Muted.TLabel').pack(anchor='w',pady=(8,0))
        ttk.Label(self,text='Keeper and extra copies are re-hashed before deletion. A missing, changed or unsafe keeper stops deletion for that group.',
                  style='Muted.TLabel',wraplength=900).pack(anchor='w',pady=(4,0))
