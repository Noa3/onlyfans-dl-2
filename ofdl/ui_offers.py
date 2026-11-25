"""Free/trial tab widgets; account and worker coordination stays in App."""
from tkinter import ttk


class OffersTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        ttk.Label(self,text='Free subscriptions & free trials',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(self,text='Scan account-connected sources, then select free pages or trial links and claim them here. '
                           'Only $0 offers are sent; free pages are re-checked immediately before subscribing. '
                           'Trials renew automatically at the regular price unless cancelled on the website.',
                  style='Muted.TLabel',wraplength=900).pack(anchor='w',pady=(4,12))
        sources = ttk.LabelFrame(self,text='Scan sources',padding=12)
        sources.pack(fill='x')
        for fields in (
                [('Active subscriptions',app.scan_active),('Expired subscriptions',app.scan_expired),
                 ('Newest chat messages (trial links)',app.scan_messages)],
                [('Inbox chat partners',app.scan_inbox),('My custom lists',app.scan_lists),
                 ('Website promo banners (0-price only)',app.scan_promotions)]):
            row = ttk.Frame(sources)
            row.pack(fill='x',pady=(0,6))
            for index,(label,var) in enumerate(fields):
                ttk.Checkbutton(row,text=label,variable=var).pack(side='left',padx=(18 if index else 0,0))
        ttk.Label(sources,text='Scans account-connected sources with bounded pagination; this is not a platform-wide directory.',
                  style='Muted.TLabel',wraplength=880).pack(anchor='w',pady=(6,0))
        row = ttk.Frame(sources)
        row.pack(fill='x',pady=(8,0))
        ttk.Checkbutton(row,text='Deep scan (more chat pages, chat partners, lists and list users)',
                        variable=app.deep_offers).pack(side='left')
        row = ttk.Frame(sources)
        row.pack(fill='x',pady=(10,0))
        ttk.Label(row,text='Also check creators').pack(side='left')
        self.names_entry = ttk.Entry(row,textvariable=app.offer_names)
        self.names_entry.pack(side='left',fill='x',expand=True,padx=8)
        app.button(row,'Scan now',app.scan_offers,accent=True,side='left')
        ttk.Label(sources,text='Optional creator names or profile URLs, separated by commas. Active subscriptions appear as already subscribed.',
                  style='Muted.TLabel',wraplength=880).pack(anchor='w',pady=(8,0))
        ttk.Label(self,textvariable=app.offer_status,style='Muted.TLabel',wraplength=900).pack(anchor='w',pady=(10,0))
        self.bar = ttk.Progressbar(self,mode='indeterminate')
        self.bar.pack(fill='x',pady=(6,0))
        filters = ttk.Frame(self)
        filters.pack(fill='x',pady=(10,0))
        ttk.Label(filters,text='Show',style='Muted.TLabel').pack(side='left')
        self.filter_box = ttk.Combobox(filters,textvariable=app.offer_filter,state='readonly',width=13,
                                      values=['All offers','Free only','Trials only'])
        self.filter_box.pack(side='left',padx=(8,14))
        self.state_box = ttk.Combobox(filters,textvariable=app.offer_status_filter,state='readonly',width=17,
                                     values=['All statuses','Available only','Already subscribed'])
        self.state_box.pack(side='left')
        ttk.Label(filters,text='Search',style='Muted.TLabel').pack(side='left',padx=(14,6))
        ttk.Entry(filters,textvariable=app.offer_search,width=18).pack(side='left')
        self.action_button = app.button(filters,'Subscribe / claim selected',app.subscribe_selected,
                                        accent=True,side='right')
        self.action_button.state(['disabled'])
        app.button(filters,'Open profile / link',app.open_offer_profile,busy=False,side='right',padx=(0,8))
        table = ttk.Frame(self)
        table.pack(fill='both',expand=True,pady=(10,0))
        columns = ('creator','kind','price','source','status','details')
        self.tree = ttk.Treeview(table,columns=columns,show='headings',selectmode='extended',height=12)
        for column,label,width in (('creator','Creator',170),('kind','Type',90),('price','Scanned price',90),
                                    ('source','Found via',180),('status','Status',140),('details','Details',330)):
            self.tree.heading(column,text=label,command=lambda name=column:app._sort_offers_by(name))
            self.tree.column(column,width=width,anchor='w',stretch=column in {'creator','source','details'})
        scrollbar = ttk.Scrollbar(table,orient='vertical',command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side='left',fill='both',expand=True)
        scrollbar.pack(side='right',fill='y')
        self.tree.bind('<<TreeviewSelect>>',app._offer_selection_changed)
        ttk.Label(self,textvariable=app.offer_summary,style='Muted.TLabel').pack(anchor='w',pady=(8,0))
        ttk.Label(self,text='Review current price and renewal settings on the website before enabling long trials.',
                  style='Muted.TLabel').pack(anchor='w')
