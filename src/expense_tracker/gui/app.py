"""Tkinter desktop GUI for receipt review and report generation."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from expense_tracker.gui.services import (
    AppPaths,
    build_new_receipt_draft,
    check_and_start_upload_server,
    default_app_paths,
    delete_receipt,
    ensure_owners_config,
    ensure_receipt_flow_dirs,
    exe_build_time,
    generate_report,
    is_splittable_item,
    list_reports,
    load_app_state,
    normalize_store_image_paths,
    open_html_report,
    open_path,
    receipt_to_edit_payload,
    reopen_failed_receipt,
    save_receipt_edit,
    split_quantity_items,
    step_down_items,
    trigger_ingestion,
)
from expense_tracker.pipelines.receipt_ingestion import OCR_PROMPT
from expense_tracker.schemas.domain import FailedOcrRecord, ReceiptRecord
from expense_tracker.schemas.enums import ItemCategory, OcrStatus, OwnerMode

# Default zoom factor for the receipt image preview column.
DEFAULT_IMAGE_ZOOM = 1.3


def _previous_month_string(today: date | None = None) -> str:
    today = today or date.today()
    if today.month == 1:
        return f"{today.year - 1:04d}-12"
    return f"{today.year:04d}-{today.month - 1:02d}"


@dataclass
class ReceiptItemDialogResult:
    payload: dict


class ReceiptItemDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        owner_ids: list[str],
        item_payload: dict | None = None,
        owner_names: dict[str, str] | None = None,
    ):
        super().__init__(parent)
        self.title("Receipt Item")
        self.resizable(False, False)
        self.transient(parent)
        self.result: ReceiptItemDialogResult | None = None
        self.owner_ids = owner_ids
        self.owner_names = owner_names or {}
        payload = item_payload or {
            "id": "",
            "name": "",
            "normalized_name": "",
            "category": ItemCategory.OTHER.value,
            "quantity": "1",
            "unit_price": "0.00",
            "total_price": "0.00",
            "owner_id": owner_ids[0] if owner_ids else "",
            "owner_marker": "",
        }
        self.existing_splits: list[dict] = list(payload.get("splits") or [])
        self.split_result: list[dict] | None = None

        self.vars = {
            "name": tk.StringVar(value=payload["name"]),
            "normalized_name": tk.StringVar(value=payload["normalized_name"]),
            "category": tk.StringVar(value=payload["category"]),
            "quantity": tk.StringVar(value=payload["quantity"]),
            "unit_price": tk.StringVar(value=payload["unit_price"]),
            "total_price": tk.StringVar(value=payload["total_price"]),
            "owner_id": tk.StringVar(value=payload["owner_id"]),
            "owner_marker": tk.StringVar(value=payload.get("owner_marker", "")),
        }
        self.item_id = payload.get("id", "")

        form = ttk.Frame(self, padding=14)
        form.grid(sticky="nsew")
        labels = [
            ("Name", "name"),
            ("Normalized", "normalized_name"),
            ("Category", "category"),
            ("Quantity", "quantity"),
            ("Unit Price", "unit_price"),
            ("Total Price", "total_price"),
            ("Owner", "owner_id"),
            ("Marker", "owner_marker"),
        ]
        for row, (label, key) in enumerate(labels):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
            if key == "category":
                widget = ttk.Combobox(form, textvariable=self.vars[key], values=[item.value for item in ItemCategory], state="readonly", width=24)
            elif key == "owner_id":
                widget = ttk.Combobox(form, textvariable=self.vars[key], values=owner_ids, state="readonly", width=24)
            else:
                widget = ttk.Entry(form, textvariable=self.vars[key], width=28)
            widget.grid(row=row, column=1, sticky="ew", pady=4)

        buttons = ttk.Frame(form)
        buttons.grid(row=len(labels), column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(buttons, text="Split…", command=self._open_split_dialog).pack(side="left")
        ok_cancel = ttk.Frame(buttons)
        ok_cancel.pack(side="right")
        ttk.Button(ok_cancel, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(ok_cancel, text="OK", command=self._submit).pack(side="right")

        self.bind("<Return>", lambda event: self._submit())
        self.bind("<Escape>", lambda event: self.destroy())
        # 居中显示在屏幕中央
        self.update_idletasks()
        x = (self.winfo_screenwidth() - self.winfo_reqwidth()) // 2
        y = (self.winfo_screenheight() - self.winfo_reqheight()) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()
        self.wait_visibility()
        self.focus()

    def _open_split_dialog(self) -> None:
        """打开 Split 界面，把所选归属人 + 份数暂存到 self.split_result。"""
        dialog = ItemSplitDialog(
            self,
            self.owner_ids,
            self.owner_names,
            self.split_result or self.existing_splits,
        )
        self.wait_window(dialog)
        if dialog.result:
            self.split_result = dialog.result.splits

    def _submit(self) -> None:
        payload = {"id": self.item_id}
        for key, variable in self.vars.items():
            payload[key] = variable.get().strip()
        payload["splits"] = self.split_result if self.split_result is not None else self.existing_splits
        self.result = ReceiptItemDialogResult(payload=payload)
        self.destroy()


@dataclass
class ItemSplitDialogResult:
    splits: list[dict]  # [{"owner_id": str, "shares": int}]


class ItemSplitDialog(tk.Toplevel):
    """Split 输入界面：勾选归属人并填写整数份数（仅输入接口，不执行拆分逻辑）。"""

    def __init__(
        self,
        parent: tk.Misc,
        owner_ids: list[str],
        owner_names: dict[str, str] | None = None,
        splits: list[dict] | None = None,
    ):
        super().__init__(parent)
        self.title("Split Item by Owners")
        self.resizable(False, False)
        self.transient(parent)
        self.result: ItemSplitDialogResult | None = None
        owner_names = owner_names or {}
        existing = {str(s.get("owner_id")): int(s.get("shares", 1)) for s in (splits or [])}

        form = ttk.Frame(self, padding=14)
        form.grid(sticky="nsew")
        ttk.Label(form, text="勾选要分摊的归属人，并填写各归属人的份数（正整数）：").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        def _validate_int(value: str) -> bool:
            return value.isdigit() or value == ""

        validate_cmd = (self.register(_validate_int), "%P")

        self.check_vars: dict[str, tk.BooleanVar] = {}
        self.share_vars: dict[str, tk.StringVar] = {}
        self._entries: dict[str, ttk.Entry] = {}

        for row, owner_id in enumerate(owner_ids, start=1):
            name = owner_names.get(owner_id, owner_id)
            checked = owner_id in existing
            check_var = tk.BooleanVar(value=checked)
            share_var = tk.StringVar(value=str(existing.get(owner_id, 1)))
            self.check_vars[owner_id] = check_var
            self.share_vars[owner_id] = share_var

            ttk.Checkbutton(
                form,
                text=f"{name} ({owner_id})",
                variable=check_var,
                command=lambda oid=owner_id: self._toggle_entry(oid),
            ).grid(row=row, column=0, sticky="w", pady=3)
            entry = ttk.Entry(form, textvariable=share_var, width=10, validate="key", validatecommand=validate_cmd)
            entry.grid(row=row, column=1, sticky="w", pady=3)
            if not checked:
                entry.configure(state="disabled")
            self._entries[owner_id] = entry

        buttons = ttk.Frame(form)
        buttons.grid(row=len(owner_ids) + 1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="OK", command=self._submit).pack(side="right")

        self.bind("<Return>", lambda event: self._submit())
        self.bind("<Escape>", lambda event: self.destroy())
        # 居中显示在屏幕中央
        self.update_idletasks()
        x = (self.winfo_screenwidth() - self.winfo_reqwidth()) // 2
        y = (self.winfo_screenheight() - self.winfo_reqheight()) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()
        self.wait_visibility()
        self.focus()

    def _toggle_entry(self, owner_id: str) -> None:
        """勾选归属人时启用份数输入框并默认填 1（方便平均分），取消勾选时禁用。"""
        entry = self._entries[owner_id]
        if self.check_vars[owner_id].get():
            entry.configure(state="normal")
            if not self.share_vars[owner_id].get().strip():
                self.share_vars[owner_id].set("1")
        else:
            entry.configure(state="disabled")

    def _submit(self) -> None:
        splits: list[dict] = []
        for owner_id in self.check_vars:
            if not self.check_vars[owner_id].get():
                continue
            raw = self.share_vars[owner_id].get().strip()
            if not raw.isdigit() or int(raw) <= 0:
                messagebox.showerror(
                    "Split Item",
                    f"归属人「{self.owner_names.get(owner_id, owner_id)}」的份数必须是正整数。",
                    parent=self,
                )
                return
            splits.append({"owner_id": owner_id, "shares": int(raw)})
        if not splits:
            messagebox.showerror("Split Item", "请至少勾选一位归属人。", parent=self)
            return
        self.result = ItemSplitDialogResult(splits=splits)
        self.destroy()


class ExpenseTrackerGui(tk.Tk):
    def __init__(self, paths: AppPaths | None = None):
        super().__init__()
        self.paths = paths or default_app_paths()
        self.title("Expense Tracker GUI")
        self.geometry("1480x900")
        self.minsize(1220, 780)
        # 自动全屏：F11 切换全屏，Esc 退出全屏
        self.bind("<F11>", self._toggle_fullscreen)
        self.bind("<Escape>", self._exit_fullscreen)
        self.after(50, self._enter_fullscreen)

        self.owner_ids: list[str] = []
        self.owner_names: dict[str, str] = {}
        self.current_receipt_payload: dict | None = None
        self.receipt_index: dict[str, ReceiptRecord] = {}
        self.failed_records: list[FailedOcrRecord] = []
        self.report_entries = []
        self._draft_item_counter = 0
        # 拖拽分配归属时的视觉反馈状态
        self._drag_ghost: tk.Toplevel | None = None
        self._drag_start: tuple[int, int] = (0, 0)
        self._drag_item_name: str | None = None
        self._receipt_photo = None  # keep reference to avoid GC dropping the image
        self._image_original = None  # PIL image of the current receipt
        self._image_zoom = DEFAULT_IMAGE_ZOOM  # initial zoom factor
        # 预览旋转角度（0/90/180/270，仅影响预览显示，不改动磁盘原图）
        self._image_rotation = 0

        self._build_style()
        self._build_shell()
        self.refresh_all()
        # UI 就绪后再探测上传服务（不阻塞首屏），未运行则自动启动
        self.after(500, self._auto_check_upload_server)

    def _auto_check_upload_server(self) -> None:
        message = check_and_start_upload_server(self.paths.project_root)
        self.status_var.set(message)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Header.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Subtle.TLabel", foreground="#5f6b66")

    def _toggle_fullscreen(self, event: tk.Event | None = None) -> None:
        # 最大化（保留标题栏）而非无边框全屏
        if self.state() == "zoomed":
            self.state("normal")
        else:
            self.state("zoomed")

    def _exit_fullscreen(self, event: tk.Event | None = None) -> None:
        self.state("normal")

    def _enter_fullscreen(self) -> None:
        self.state("zoomed")

    def _build_shell(self) -> None:
        top = ttk.Frame(self, padding=(14, 12))
        top.pack(fill="x")
        header_row = ttk.Frame(top)
        header_row.pack(fill="x")
        ttk.Label(header_row, text="Expense Tracker Desktop", style="Header.TLabel").pack(side="left")
        ttk.Label(header_row, text=f"Build: {exe_build_time()}", style="Subtle.TLabel").pack(side="right")
        ttk.Label(
            top,
            text=f"Store: {self.paths.store_path} | Reports: {self.paths.reports_dir}",
            style="Subtle.TLabel",
        ).pack(anchor="w", pady=(2, 8))

        toolbar = ttk.Frame(top)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Refresh All", command=self.refresh_all).pack(side="left")
        ttk.Button(toolbar, text="New Receipt", command=self.new_receipt).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Save Receipt", command=self.save_current_receipt).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Delete Receipt", command=self.delete_current_receipt).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Open Image", command=self.open_current_receipt_image).pack(side="left", padx=(8, 0))
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=(12, 12), pady=2)
        ttk.Button(toolbar, text="Trigger Ingestion", command=self.trigger_ingestion_dialog).pack(side="left")
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=(12, 12), pady=2)
        # GLM OCR 是固定默认引擎，不提供勾选框；勾选此项才切换为外部
        # DeepSeek V4-Flash 解析（默认开启）。
        self.use_llm_parser_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            toolbar,
            text="Use DeepSeek V4-Flash (external)",
            variable=self.use_llm_parser_var,
            command=self._on_use_llm_parser_toggled,
        ).pack(side="left")
        self.use_preprocess_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            toolbar,
            text="图片预处理（黑底抠图矫正）",
            variable=self.use_preprocess_var,
        ).pack(side="left", padx=(10, 0))
        self.engine_label = ttk.Label(
            toolbar,
            text="Engine: DeepSeek V4-Flash (external)" if self.use_llm_parser_var.get() else "Engine: GLM OCR",
            style="Subtle.TLabel",
        )
        self.engine_label.pack(side="left", padx=(10, 0))

        # Dashboard stats (PRD 8.1)
        self.stats_frame = ttk.Frame(top)
        self.stats_frame.pack(fill="x", pady=(8, 4))
        self.stats_vars = {
            "total": tk.StringVar(value="0"),
            "success": tk.StringVar(value="0"),
            "failed": tk.StringVar(value="0"),
            "pending": tk.StringVar(value="0"),
        }
        for label, key, color in [
            ("Total Receipts", "total", "#3366CC"),
            ("Success", "success", "#2A7A3B"),
            ("Failed OCR", "failed", "#CC3333"),
            ("Pending Review", "pending", "#CC8800"),
        ]:
            frame = ttk.Frame(self.stats_frame)
            frame.pack(side="left", padx=(0, 28))
            ttk.Label(frame, text=label, style="Subtle.TLabel").pack(side="left")
            value_label = ttk.Label(frame, textvariable=self.stats_vars[key], font=("Segoe UI", 14, "bold"), foreground=color)
            value_label.pack(side="left", padx=(6, 0))

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(top, textvariable=self.status_var, style="Subtle.TLabel").pack(anchor="e", pady=(2, 0))

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self.receipts_tab = ttk.Frame(notebook, padding=10)
        self.failed_tab = ttk.Frame(notebook, padding=10)
        self.reports_tab = ttk.Frame(notebook, padding=10)
        self.log_tab = ttk.Frame(notebook, padding=10)
        notebook.add(self.receipts_tab, text="Receipts")
        notebook.add(self.failed_tab, text="Failed OCR")
        notebook.add(self.reports_tab, text="Reports")
        notebook.add(self.log_tab, text="Log")

        self._build_receipts_tab()
        self._build_failed_tab()
        self._build_reports_tab()
        self._build_log_tab()

    def _build_receipts_tab(self) -> None:
        paned = ttk.Panedwindow(self.receipts_tab, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        owner_pane = ttk.Frame(paned)
        image_pane = ttk.Frame(paned)
        paned.add(left, weight=1)
        paned.add(right, weight=3)
        paned.add(owner_pane, weight=1)
        paned.add(image_pane, weight=2)

        # Owner 归属槽（详情与图片预览之间）：把 Formal Items 里选中的行
        # 拖到某个槽上松开，即可批量把该 owner 分配给这些 item。
        self.owner_slots: dict[str, tk.Label] = {}
        ttk.Label(owner_pane, text="Assign Owner", style="Subtle.TLabel").pack(anchor="w", pady=(0, 4))
        self.owner_slots_frame = ttk.Frame(owner_pane)
        self.owner_slots_frame.pack(fill="both", expand=True)

        ttk.Label(left, text="Scanned Receipts").pack(anchor="w", pady=(0, 6))
        self.receipt_tree = ttk.Treeview(
            left,
            columns=("date", "total", "status"),
            show="tree headings",
            selectmode="browse",
        )
        # 树列 #0 显示月份节点/小票 ID，其余三列显示日期/金额/状态
        self.receipt_tree.heading("#0", text="ID")
        self.receipt_tree.column("#0", width=120, anchor="w")
        for column, heading, width in (
            ("date", "Date", 90),
            ("total", "Total", 90),
            ("status", "Status", 100),
        ):
            self.receipt_tree.heading(column, text=heading)
            self.receipt_tree.column(column, width=width, anchor="w")
        self.receipt_tree.pack(fill="both", expand=True)
        self.receipt_tree.bind("<<TreeviewSelect>>", self._on_receipt_selected)

        # Dedicated image column on the right (scrollable both ways, wheel zoom).
        image_header = ttk.Frame(image_pane)
        image_header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(image_header, text="Receipt Image").pack(side="left")
        ttk.Button(image_header, text="⟲ Rotate", width=8, command=lambda: self._rotate_receipt_image(-90)).pack(side="right", padx=(4, 0))
        ttk.Button(image_header, text="⟳ Rotate", width=8, command=lambda: self._rotate_receipt_image(90)).pack(side="right")
        self.image_canvas = tk.Canvas(image_pane, highlightthickness=0, width=300)
        image_vscroll = ttk.Scrollbar(image_pane, orient="vertical", command=self.image_canvas.yview)
        image_hscroll = ttk.Scrollbar(image_pane, orient="horizontal", command=self.image_canvas.xview)
        image_host = ttk.Frame(self.image_canvas)
        image_host.bind("<Configure>", lambda event: self.image_canvas.configure(scrollregion=self.image_canvas.bbox("all")))
        self.image_canvas.create_window((0, 0), window=image_host, anchor="nw")
        self.image_canvas.configure(
            yscrollcommand=image_vscroll.set,
            xscrollcommand=image_hscroll.set,
        )
        self.image_canvas.grid(row=1, column=0, sticky="nsew")
        image_vscroll.grid(row=1, column=1, sticky="ns")
        image_hscroll.grid(row=2, column=0, sticky="ew")
        image_pane.rowconfigure(1, weight=1)
        image_pane.columnconfigure(0, weight=1)
        self.receipt_image_label = ttk.Label(image_host, text="No image loaded", anchor="center", justify="center")
        self.receipt_image_label.pack(fill="both", expand=True)
        self.receipt_image_label.bind("<MouseWheel>", self._on_image_wheel)

        canvas = tk.Canvas(right, highlightthickness=0)
        scroll = ttk.Scrollbar(right, orient="vertical", command=canvas.yview)
        form_host = ttk.Frame(canvas)
        form_host.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=form_host, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # Formal Items 块放最前（在字段表单之前）
        self.is_verified_var = tk.BooleanVar(value=False)
        items_frame = ttk.LabelFrame(form_host, text="Formal Items", padding=12)
        items_frame.pack(fill="both", expand=True)
        item_toolbar = ttk.Frame(items_frame)
        item_toolbar.pack(fill="x", pady=(0, 6))
        ttk.Button(item_toolbar, text="Add Item", command=self.add_item).pack(side="left")
        ttk.Button(item_toolbar, text="Edit Item", command=self.edit_selected_item).pack(side="left", padx=(8, 0))
        ttk.Button(item_toolbar, text="Delete Item", command=self.delete_selected_item).pack(side="left", padx=(8, 0))
        ttk.Button(item_toolbar, text="Step Down", command=self.step_down_selected_items).pack(side="left", padx=(8, 0))
        # 拆分下拉：quantity>1 整数的 item 可拆成多个 quantity=1 的 item
        ttk.Label(item_toolbar, text="Split:").pack(side="left", padx=(8, 0))
        self.split_combo = ttk.Combobox(item_toolbar, state="readonly", width=26)
        self.split_combo.pack(side="left", padx=(4, 0))
        self.split_combo.bind("<<ComboboxSelected>>", self._on_split_selected)
        # Verify 勾选框放在 Formal Items 工具栏最右侧
        ttk.Checkbutton(item_toolbar, text="Verified", variable=self.is_verified_var).pack(side="right", padx=(0, 4))

        self.items_tree = ttk.Treeview(
            items_frame,
            columns=("name", "category", "quantity", "unit_price", "total_price", "owner"),
            show="headings",
            height=8,
            selectmode="extended",
        )
        for column, heading, width in (
            ("name", "Name", 180),
            ("category", "Category", 100),
            ("quantity", "Qty", 70),
            ("unit_price", "Unit", 80),
            ("total_price", "Total", 80),
            ("owner", "Owner", 100),
        ):
            self.items_tree.heading(column, text=heading)
            self.items_tree.column(column, width=width, anchor="w")
        self.items_tree.pack(fill="both", expand=True)
        # 双击行直接打开 Edit Item 对话框
        self.items_tree.bind("<Double-1>", lambda event: self.edit_selected_item())
        # 拖拽到 Owner 归属槽松手时批量分配归属（带 item name 跟随鼠标的反馈）
        self.items_tree.bind("<ButtonPress-1>", self._on_item_press)
        self.items_tree.bind("<B1-Motion>", self._on_item_motion)
        self.items_tree.bind("<ButtonRelease-1>", self._on_item_release)

        form = ttk.LabelFrame(form_host, text="Receipt Details", padding=12)
        form.pack(fill="x", pady=(10, 0))
        self.receipt_vars = {
            "id": tk.StringVar(),
            "merchant": tk.StringVar(),
            "purchase_date": tk.StringVar(),
            "currency": tk.StringVar(value="EUR"),
            "total_amount": tk.StringVar(),
            "payment_method": tk.StringVar(),
            "default_owner_id": tk.StringVar(),
            "owner_mode": tk.StringVar(value=OwnerMode.NORMAL.value),
            "receipt_owner_marker": tk.StringVar(),
            "image_path": tk.StringVar(),
            "image_hash": tk.StringVar(),
            "ocr_status": tk.StringVar(value=OcrStatus.PENDING.value),
            "ocr_attempts": tk.StringVar(value="0"),
            "ocr_failure_reason": tk.StringVar(),
        }

        row = 0
        self._form_entry(form, row, "Receipt ID", self.receipt_vars["id"], state="readonly"); row += 1
        self._form_entry(form, row, "Merchant", self.receipt_vars["merchant"]); row += 1
        self._form_entry(form, row, "Purchase Date", self.receipt_vars["purchase_date"]); row += 1
        self._form_entry(form, row, "Currency", self.receipt_vars["currency"]); row += 1
        self._form_entry(form, row, "Total Amount", self.receipt_vars["total_amount"]); row += 1
        self._form_entry(form, row, "Payment Method", self.receipt_vars["payment_method"]); row += 1
        self.default_owner_combo = self._form_combo(form, row, "Default Owner", self.receipt_vars["default_owner_id"], []); row += 1
        self.owner_mode_combo = self._form_combo(form, row, "Owner Mode", self.receipt_vars["owner_mode"], [mode.value for mode in OwnerMode]); row += 1
        self._form_entry(form, row, "Receipt Marker", self.receipt_vars["receipt_owner_marker"]); row += 1
        self._form_entry(form, row, "Image Path", self.receipt_vars["image_path"]); row += 1
        self._form_entry(form, row, "Image Hash", self.receipt_vars["image_hash"]); row += 1
        self.ocr_status_combo = self._form_combo(form, row, "OCR Status", self.receipt_vars["ocr_status"], [status.value for status in OcrStatus]); row += 1
        self._form_entry(form, row, "OCR Attempts", self.receipt_vars["ocr_attempts"]); row += 1
        self._form_entry(form, row, "OCR Failure", self.receipt_vars["ocr_failure_reason"]); row += 1

        ttk.Label(form, text="Review Notes").grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=(6, 4))
        self.review_notes_text = tk.Text(form, height=4, width=45)
        self.review_notes_text.grid(row=row, column=1, sticky="ew", pady=(6, 4))
        row += 1
        ttk.Label(form, text="OCR Raw Text").grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=(6, 4))
        self.ocr_raw_text = tk.Text(form, height=6, width=45)
        self.ocr_raw_text.grid(row=row, column=1, sticky="ew", pady=(6, 4))
        form.columnconfigure(1, weight=1)

        removed_frame = ttk.LabelFrame(form_host, text="Removed Audit Items", padding=12)
        removed_frame.pack(fill="both", expand=True, pady=(10, 0))
        self.removed_tree = ttk.Treeview(
            removed_frame,
            columns=("name", "reason", "total_price"),
            show="headings",
            height=5,
        )
        for column, heading, width in (
            ("name", "Name", 200),
            ("reason", "Reason", 220),
            ("total_price", "Total", 90),
        ):
            self.removed_tree.heading(column, text=heading)
            self.removed_tree.column(column, width=width, anchor="w")
        self.removed_tree.pack(fill="both", expand=True)

    def _build_failed_tab(self) -> None:
        toolbar = ttk.Frame(self.failed_tab)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="Refresh Failed OCR", command=self.refresh_failed_records).pack(side="left")
        ttk.Button(toolbar, text="Open Archived Image", command=self.open_failed_archived_image).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Open Original Path", command=self.open_failed_original_path).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Review & Resubmit", command=self.reopen_failed_dialog).pack(side="left", padx=(8, 0))

        self.failed_tree = ttk.Treeview(
            self.failed_tab,
            columns=("image_path", "attempts", "reason", "created_at"),
            show="headings",
        )
        for column, heading, width in (
            ("image_path", "Image", 360),
            ("attempts", "Attempts", 90),
            ("reason", "Failure Reason", 320),
            ("created_at", "Created", 170),
        ):
            self.failed_tree.heading(column, text=heading)
            self.failed_tree.column(column, width=width, anchor="w")
        self.failed_tree.pack(fill="both", expand=True)

    def _build_reports_tab(self) -> None:
        controls = ttk.Frame(self.reports_tab)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Label(controls, text="Report Month").pack(side="left")
        self.report_month_var = tk.StringVar(value=_previous_month_string())
        ttk.Entry(controls, textvariable=self.report_month_var, width=12).pack(side="left", padx=(8, 0))
        self.write_schema_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text="Write Schema", variable=self.write_schema_var).pack(side="left", padx=(10, 0))
        ttk.Button(controls, text="Generate Report", command=self.generate_report_from_form).pack(side="left", padx=(10, 0))
        ttk.Button(controls, text="Refresh Reports", command=self.refresh_reports).pack(side="left", padx=(10, 0))
        ttk.Button(controls, text="Open HTML", command=self.open_selected_report_html).pack(side="left", padx=(10, 0))
        ttk.Button(controls, text="Open JSON", command=self.open_selected_report_json).pack(side="left", padx=(10, 0))

        self.reports_tree = ttk.Treeview(
            self.reports_tab,
            columns=("month", "generated_at", "html_path"),
            show="headings",
        )
        for column, heading, width in (
            ("month", "Month", 100),
            ("generated_at", "Generated At", 220),
            ("html_path", "HTML Path", 760),
        ):
            self.reports_tree.heading(column, text=heading)
            self.reports_tree.column(column, width=width, anchor="w")
        self.reports_tree.pack(fill="both", expand=True)

    def _build_log_tab(self) -> None:
        log_frame = ttk.Frame(self.log_tab)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, wrap="word", font=("Consolas", 10))
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.insert("end", "--- OCR Log started ---\n")
        self.log_text.configure(state="disabled")

    def log_message(self, msg: str) -> None:
        self.log_text.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {msg}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_use_llm_parser_toggled(self) -> None:
        """Toggle the external DeepSeek V4-Flash parser backend.

        When enabled, grounding blocks (text + bounding boxes) from the local
        OCR model are sent to DeepSeek V4-Flash which converts them into the
        ExtractedReceipt JSON (thinking mode disabled).
        """
        if self.use_llm_parser_var.get():
            self.engine_label.configure(text="Engine: DeepSeek V4-Flash (external)")
            self.status_var.set("External parser enabled: OCR text -> DeepSeek V4-Flash")
            self.log_message("External parser enabled: OCR text -> DeepSeek V4-Flash (thinking disabled)")
        else:
            self.engine_label.configure(text="Engine: GLM OCR")
            self.status_var.set("Using local GLM OCR (no API key required)")

    def _form_entry(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, *, state: str = "normal") -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
        ttk.Entry(parent, textvariable=variable, width=48, state=state).grid(row=row, column=1, sticky="ew", pady=4)

    def _form_combo(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, values: list[str]) -> ttk.Combobox:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
        widget = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", width=45)
        widget.grid(row=row, column=1, sticky="ew", pady=4)
        return widget

    def refresh_all(self) -> None:
        self.refresh_receipts()
        self.refresh_failed_records()
        self.refresh_reports()
        self._update_stats()
        self.status_var.set("Data refreshed")

    def _update_stats(self) -> None:
        """PRD 8.1: update dashboard counts."""
        store = load_app_state(self.paths)[0]
        total = len(store.receipts)
        success = sum(1 for r in store.receipts if r.ocr_status == OcrStatus.SUCCESS)
        failed = len(store.failed_ocr_records)
        pending = sum(1 for r in store.receipts if r.ocr_status in (OcrStatus.PENDING, OcrStatus.NEEDS_REVIEW))
        self.stats_vars["total"].set(str(total))
        self.stats_vars["success"].set(str(success))
        self.stats_vars["failed"].set(str(failed))
        self.stats_vars["pending"].set(str(pending))

    def trigger_ingestion_dialog(self) -> None:
        """PRD 8.1: pick an image file and run the ingestion pipeline."""
        from tkinter import filedialog

        file_path = filedialog.askopenfilename(
            title="Select receipt image to process",
            filetypes=[
                ("Image files", "*.jpg *.jpeg *.png *.webp"),
                ("All files", "*.*"),
            ],
            parent=self,
        )
        if not file_path:
            return
        self.log_message(f"OCR Prompt ({len(OCR_PROMPT)} chars):\n{OCR_PROMPT}")
        self.log_message(f"Processing: {file_path}")
        use_llm_parser = self.use_llm_parser_var.get()
        use_preprocess = self.use_preprocess_var.get()
        if use_llm_parser:
            self.log_message("Engine: DeepSeek V4-Flash (external parser, thinking disabled)")
        if use_preprocess:
            self.log_message("Image preprocessing enabled: 黑底抠图 + 透视矫正")
        try:
            receipt_id, raw_text, preprocess_info = trigger_ingestion(
                self.paths,
                file_path,
                use_llm_parser=use_llm_parser,
                use_preprocess=use_preprocess,
            )
            if preprocess_info:
                self.log_message(
                    f"Preprocess: method={preprocess_info.get('method')} "
                    f"found_receipt={preprocess_info.get('found_receipt')} "
                    f"info={preprocess_info.get('info')}"
                )
            self.log_message(f"SUCCESS: receipt_id={receipt_id}")
            self.log_message(f"OCR Raw Output ({len(raw_text)} chars):\n{raw_text}")
            messagebox.showinfo("Ingestion Complete", f"Receipt {receipt_id} processed successfully.", parent=self)
        except Exception as exc:
            raw = getattr(exc, "content", None)
            print(f"[DEBUG] exc type={type(exc).__name__} exc={exc!r}")
            print(f"[DEBUG] raw type={type(raw).__name__} raw={raw!r}")
            import traceback
            traceback.print_exc()
            if raw:
                self.log_message(f"OCR Raw Output (failed, {len(raw)} chars):\n{raw}")
            self.log_message(f"FAILED: {exc}")
            messagebox.showerror("Ingestion Failed", str(exc), parent=self)
            return
        self.refresh_all()

    def refresh_receipts(self) -> None:
        store, owners = load_app_state(self.paths)
        self.owner_ids = [owner.id for owner in owners.owners]
        self.owner_names = {owner.id: owner.name for owner in owners.owners}
        self.receipt_index = {receipt.id: receipt for receipt in store.receipts}

        # 记录用户已展开的月份节点，刷新后恢复（首次打开时默认只展开当前月份）
        open_months = {
            item
            for item in self.receipt_tree.get_children()
            if self.receipt_tree.item(item, "open")
        }
        for tree_item in self.receipt_tree.get_children():
            self.receipt_tree.delete(tree_item)

        # 按月份分组：月份节点为父行，该月小票为子行
        month_items: dict[str, list[ReceiptRecord]] = {}
        for receipt in store.receipts:
            month_items.setdefault(receipt.purchase_date.strftime("%Y-%m"), []).append(receipt)

        current_month = date.today().strftime("%Y-%m")
        for month in sorted(month_items, reverse=True):
            receipts = month_items[month]
            parent = f"month:{month}"
            month_label = f"{month} ({len(receipts)})"
            self.receipt_tree.insert(
                "",
                "end",
                iid=parent,
                text=month_label,
                values=("", "", ""),
                open=(month in open_months or month == current_month),
            )
            for receipt in sorted(receipts, key=lambda item: item.purchase_date, reverse=True):
                self.receipt_tree.insert(
                    parent,
                    "end",
                    iid=receipt.id,
                    text=receipt.id,
                    values=(
                        receipt.purchase_date.isoformat(),
                        f"{receipt.total_amount:.2f}",
                        receipt.ocr_status.value,
                    ),
                )

        self.default_owner_combo.configure(values=self.owner_ids)
        self._refresh_owner_slots()

        if self.current_receipt_payload and self.current_receipt_payload.get("id") in self.receipt_index:
            self.load_receipt_into_form(self.receipt_index[self.current_receipt_payload["id"]])
        elif self.receipt_index:
            first_receipt = next(iter(sorted(self.receipt_index.values(), key=lambda item: item.purchase_date, reverse=True)))
            self.load_receipt_into_form(first_receipt)
        else:
            self.new_receipt()

    def _reveal_receipt(self, receipt_id: str) -> None:
        """在树中展开该小票所在月份并选中它（滚动到可见位置）。"""
        receipt = self.receipt_index.get(receipt_id)
        if receipt is None:
            return
        self.receipt_tree.item(f"month:{receipt.purchase_date.strftime('%Y-%m')}", open=True)
        self.receipt_tree.selection_set(receipt_id)
        self.receipt_tree.focus(receipt_id)
        self.receipt_tree.see(receipt_id)

    def refresh_failed_records(self) -> None:
        store = load_app_state(self.paths)[0]
        self.failed_records = list(store.failed_ocr_records)
        for item in self.failed_tree.get_children():
            self.failed_tree.delete(item)
        for index, record in enumerate(self.failed_records):
            self.failed_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    record.image_path,
                    record.attempts,
                    record.failure_reason,
                    record.created_at.isoformat(),
                ),
            )

    def refresh_reports(self) -> None:
        self.report_entries = list_reports(self.paths.reports_dir)
        for item in self.reports_tree.get_children():
            self.reports_tree.delete(item)
        for entry in self.report_entries:
            self.reports_tree.insert(
                "",
                "end",
                iid=entry.report_month,
                values=(entry.report_month, entry.generated_at or "-", str(entry.html_path)),
            )

    def new_receipt(self) -> None:
        self._draft_item_counter = 0
        self.current_receipt_payload = build_new_receipt_draft(self.paths)
        self._apply_current_payload()
        self.status_var.set("New receipt draft ready")

    def _on_receipt_selected(self, _event) -> None:
        selection = self.receipt_tree.selection()
        if not selection:
            return
        receipt_id = selection[0]
        receipt = self.receipt_index.get(receipt_id)
        if receipt is not None:
            self.load_receipt_into_form(receipt)

    def load_receipt_into_form(self, receipt: ReceiptRecord) -> None:
        self.current_receipt_payload = receipt_to_edit_payload(receipt)
        self._apply_current_payload()

    def _apply_current_payload(self) -> None:
        payload = self.current_receipt_payload or build_new_receipt_draft(self.paths)
        for key, variable in self.receipt_vars.items():
            variable.set(str(payload.get(key, "")))
        self.is_verified_var.set(bool(payload.get("is_verified", False)))
        self.review_notes_text.delete("1.0", "end")
        self.review_notes_text.insert("1.0", payload.get("review_notes", ""))
        self.ocr_raw_text.delete("1.0", "end")
        self.ocr_raw_text.insert("1.0", payload.get("ocr_raw_text", ""))
        self._load_receipt_image(payload.get("image_path", ""))
        self._refresh_items_tree()
        self._refresh_removed_tree()

    def _load_receipt_image(self, image_path: str) -> None:
        """Load and display the receipt image in the details form.

        The path may be absolute or relative to the project root; a missing or
        unreadable file simply shows a placeholder text. The image starts at
        the default zoom and the mouse wheel adjusts the zoom factor.
        """
        self._image_rotation = 0
        if not image_path:
            self._image_original = None
            self._image_zoom = DEFAULT_IMAGE_ZOOM
            self.receipt_image_label.configure(text="No image loaded")
            self._receipt_photo = None
            return

        path = Path(image_path)
        if not path.is_absolute():
            path = self.paths.project_root / path

        try:
            self._image_original = Image.open(path)
        except Exception:
            self._image_original = None
            self._image_zoom = DEFAULT_IMAGE_ZOOM
            self.receipt_image_label.configure(text="Image not found")
            self._receipt_photo = None
            return

        self._image_zoom = DEFAULT_IMAGE_ZOOM
        self._render_receipt_image()

    def _rotate_receipt_image(self, degrees: int) -> None:
        """按 90° 步进旋转预览图（仅显示层，不改动磁盘原图）。"""
        if self._image_original is None:
            return
        self._image_rotation = (self._image_rotation + degrees) % 360
        self._render_receipt_image()

    def _render_receipt_image(self) -> None:
        """Render the current receipt image at the current zoom factor."""
        if self._image_original is None:
            return

        img = self._image_original
        if self._image_rotation:
            img = img.rotate(self._image_rotation, expand=True, resample=Image.BICUBIC)

        width = max(1, int(img.width * self._image_zoom))
        height = max(1, int(img.height * self._image_zoom))
        # Cap the rendered size to keep memory reasonable while still allowing
        # close inspection of small receipt text.
        max_side = 2400
        if max(width, height) > max_side:
            scale = max_side / max(width, height)
            width = max(1, int(width * scale))
            height = max(1, int(height * scale))

        img = img.resize((width, height), Image.LANCZOS)
        self._receipt_photo = ImageTk.PhotoImage(img)
        self.receipt_image_label.configure(
            image=self._receipt_photo,
            text="",
            compound="center",
        )

    def _on_image_wheel(self, event) -> None:
        """Zoom the receipt image with the mouse wheel (up = zoom in)."""
        if self._image_original is None:
            return
        factor = 1.15 if event.delta > 0 else 1 / 1.15
        self._image_zoom = min(12.0, max(0.25, self._image_zoom * factor))
        self._render_receipt_image()

    def _format_item_owners(self, item: dict) -> str:
        """Formal Items 的 Owner 列：若 item 有 split，显示所有参与 split 的归属人。"""
        splits = item.get("splits") or []
        if splits:
            return " + ".join(
                self.owner_names.get(s["owner_id"], s["owner_id"]) for s in splits
            )
        return self.owner_names.get(item["owner_id"], item["owner_id"])

    def _refresh_items_tree(self) -> None:
        for item in self.items_tree.get_children():
            self.items_tree.delete(item)
        payload = self.current_receipt_payload or {"items": []}
        # 默认显示所有 item：树高随 item 数量自适应（至少 5 行）
        self.items_tree.configure(height=max(len(payload.get("items", [])), 5))
        for index, item in enumerate(payload.get("items", [])):
            self.items_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    item["name"],
                    item["category"],
                    item["quantity"],
                    item["unit_price"],
                    item["total_price"],
                    self._format_item_owners(item),
                ),
            )
        self._refresh_split_combo()
        self._refresh_owner_slots()

    def _compute_owner_totals(self, items: list[dict]) -> dict[str, Decimal]:
        """按 owner 聚合当前小票的金额（split-aware：有 splits 时按份数分摊，总和守恒）。"""
        totals: dict[str, Decimal] = {}
        for item in items:
            splits = item.get("splits") or []
            if not splits:
                owner_id = str(item["owner_id"])
                totals[owner_id] = totals.get(owner_id, Decimal("0")) + Decimal(str(item["total_price"]))
                continue
            total_shares = sum(int(split["shares"]) for split in splits)
            total_price = Decimal(str(item["total_price"]))
            allocated = Decimal("0")
            for index, split in enumerate(splits):
                if index == len(splits) - 1:
                    amount = total_price - allocated
                else:
                    amount = (total_price * int(split["shares"]) / total_shares).quantize(Decimal("0.01"))
                    allocated += amount
                owner_id = str(split["owner_id"])
                totals[owner_id] = totals.get(owner_id, Decimal("0")) + amount
        return totals

    def _refresh_owner_slots(self) -> None:
        """按当前 owners 配置重建"Assign Owner"槽（拖拽目标），并显示当前小票各归属人总价。"""
        for child in self.owner_slots_frame.winfo_children():
            child.destroy()
        self.owner_slots.clear()
        items = (self.current_receipt_payload or {}).get("items", [])
        totals = self._compute_owner_totals(items)
        for owner_id in self.owner_ids:
            name = self.owner_names.get(owner_id, owner_id)
            total = totals.get(owner_id, Decimal("0"))
            slot = tk.Label(
                self.owner_slots_frame,
                text=f"{name}\n({owner_id})\n€{total:.2f}",
                justify="center",
                anchor="center",
                relief="groove",
                bd=1,
                padx=6,
                pady=12,
                bg="#f2f5f4",
            )
            slot.pack(fill="x", pady=(4, 0))
            slot.bind("<Enter>", lambda event, s=slot: s.configure(bg="#cfe3f7"))
            slot.bind("<Leave>", lambda event, s=slot: s.configure(bg="#f2f5f4"))
            self.owner_slots[owner_id] = slot

    def _on_item_press(self, event: tk.Event) -> None:
        """按下 items 行：记录起点，准备拖拽反馈。"""
        self._drag_ghost = None
        self._drag_start = (event.x_root, event.y_root)
        self._drag_item_name = None
        selection = self.items_tree.selection()
        payload = self.current_receipt_payload
        if selection and payload and selection[0].isdigit():
            index = int(selection[0])
            if 0 <= index < len(payload["items"]):
                self._drag_item_name = payload["items"][index]["name"]

    def _on_item_motion(self, event: tk.Event) -> None:
        """拖动中：让 item name 跟随鼠标显示（小悬浮标签）。"""
        if self._drag_item_name is None:
            return
        dx = event.x_root - self._drag_start[0]
        dy = event.y_root - self._drag_start[1]
        if self._drag_ghost is None:
            if abs(dx) < 5 and abs(dy) < 5:
                return
            ghost = tk.Toplevel(self)
            ghost.overrideredirect(True)
            ghost.attributes("-topmost", True)
            tk.Label(
                ghost,
                text=self._drag_item_name,
                bg="#ffffcc",
                relief="solid",
                bd=1,
                padx=6,
                pady=2,
            ).pack()
            self._drag_ghost = ghost
        self._drag_ghost.geometry(f"+{event.x_root + 12}+{event.y_root + 8}")

    def _on_item_release(self, event: tk.Event) -> None:
        """松开：销毁跟随标签，执行归属分配。"""
        if self._drag_ghost is not None:
            self._drag_ghost.destroy()
            self._drag_ghost = None
        self._drag_item_name = None
        self._assign_owner_by_drag()

    def _assign_owner_by_drag(self) -> None:
        """把 Formal Items 中选中的行拖到 Owner 槽上松开时，批量分配 owner。"""
        selection = self.items_tree.selection()
        payload = self.current_receipt_payload
        if not selection or not payload or not self.owner_slots:
            return
        x = self.winfo_pointerx()
        y = self.winfo_pointery()
        target_owner = None
        for owner_id, widget in self.owner_slots.items():
            rx, ry = widget.winfo_rootx(), widget.winfo_rooty()
            rw, rh = widget.winfo_width(), widget.winfo_height()
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                target_owner = owner_id
                break
        if target_owner is None:
            return
        indices = sorted(int(iid) for iid in selection if iid.isdigit())
        for index in indices:
            payload["items"][index]["owner_id"] = target_owner
        self._refresh_items_tree()
        self.status_var.set(
            f"Assigned {len(indices)} item(s) to {self.owner_names.get(target_owner, target_owner)}"
        )

    def _refresh_removed_tree(self) -> None:
        for item in self.removed_tree.get_children():
            self.removed_tree.delete(item)
        payload = self.current_receipt_payload or {"removed_items": []}
        for index, item in enumerate(payload.get("removed_items", [])):
            self.removed_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(item["name"], item["reason"], item["total_price"]),
            )

    def _gather_receipt_payload_from_form(self) -> dict:
        payload = dict(self.current_receipt_payload or build_new_receipt_draft(self.paths))
        for key, variable in self.receipt_vars.items():
            payload[key] = variable.get().strip()
        payload["is_verified"] = self.is_verified_var.get()
        payload["review_notes"] = self.review_notes_text.get("1.0", "end").strip()
        payload["ocr_raw_text"] = self.ocr_raw_text.get("1.0", "end").strip()
        return payload

    def save_current_receipt(self) -> None:
        payload = self._gather_receipt_payload_from_form()
        image_path = str(payload.get("image_path") or "")
        # 勾选 verified 保存时会尝试把图片从 已处理/ 移到 已校对/。PIL 的
        # Image.open 会一直持有文件句柄，若不先关闭，Windows 移动同一文件会
        # 抛 WinError 32（文件被占用）。手动记录（无图）无需处理。
        if payload.get("is_verified") and image_path and not image_path.startswith("manual://"):
            self._close_receipt_image()
        try:
            record = save_receipt_edit(
                self.paths,
                payload,
                payload.get("items", []),
                payload.get("removed_items", []),
            )
        except Exception as exc:
            messagebox.showerror("Save Receipt", str(exc), parent=self)
            # 保存失败时图片通常仍在原处：恢复预览（句柄已被上面释放）
            self._load_receipt_image(image_path)
            return
        self.status_var.set(f"Saved {record.id}")
        # refresh_receipts 会用 store 中更新后的 image_path（若图片已移入
        # 已校对则为新位置）重载预览。
        self.refresh_receipts()
        self._reveal_receipt(record.id)

    def _close_receipt_image(self) -> None:
        """关闭 PIL 对当前预览图的文件句柄。

        勾选 verified 保存前必须先释放句柄，否则移动/重命名这张图时
        Windows 会因文件被本进程占用而抛 WinError 32。
        """
        if self._image_original is not None:
            try:
                self._image_original.close()
            except Exception:
                pass
            self._image_original = None
            self._image_rotation = 0

    def delete_current_receipt(self) -> None:
        payload = self.current_receipt_payload
        if not payload or not payload.get("id"):
            self.new_receipt()
            return
        if not messagebox.askyesno("Delete Receipt", f"Delete receipt {payload['id']}?", parent=self):
            return
        try:
            delete_receipt(self.paths, payload["id"])
        except Exception as exc:
            messagebox.showerror("Delete Receipt", str(exc), parent=self)
            return
        self.status_var.set(f"Deleted {payload['id']}")
        self.current_receipt_payload = None
        self.refresh_receipts()

    def _selected_item_index(self) -> int | None:
        selection = self.items_tree.selection()
        if not selection:
            return None
        return int(selection[0])

    def add_item(self) -> None:
        if self.current_receipt_payload is None:
            self.new_receipt()
        dialog = ReceiptItemDialog(self, self.owner_ids, owner_names=self.owner_names)
        self.wait_window(dialog)
        if not dialog.result:
            return
        self._draft_item_counter += 1
        payload = dialog.result.payload
        payload["id"] = payload.get("id") or f"draft-item-{self._draft_item_counter}"
        self.current_receipt_payload["items"].append(payload)
        self._refresh_items_tree()

    def edit_selected_item(self) -> None:
        index = self._selected_item_index()
        if index is None or self.current_receipt_payload is None:
            return
        dialog = ReceiptItemDialog(self, self.owner_ids, self.current_receipt_payload["items"][index], owner_names=self.owner_names)
        self.wait_window(dialog)
        if not dialog.result:
            return
        self.current_receipt_payload["items"][index] = dialog.result.payload
        self._refresh_items_tree()

    def delete_selected_item(self) -> None:
        index = self._selected_item_index()
        if index is None or self.current_receipt_payload is None:
            return
        del self.current_receipt_payload["items"][index]
        self._refresh_items_tree()

    def step_down_selected_items(self) -> None:
        """Step Down：把选中 item 的价格整体下移一位（修复金额错位）。

        选中项可多选；在最后一个选中项之后插入新 item "new"（价格为最后
        一个选中项的价格），选中项价格依次后移，第一个选中项价格置 0。
        """
        if self.current_receipt_payload is None:
            self.new_receipt()
        selection = [int(iid) for iid in self.items_tree.selection()]
        if not selection:
            return
        items = self.current_receipt_payload["items"]
        step_down_items(items, selection)
        # 给新插入的 item 分配 draft id（新 item 位于最后一个选中项之后）
        self._draft_item_counter += 1
        last_index = max(selection)
        items[last_index + 1]["id"] = f"draft-item-{self._draft_item_counter}"
        self._refresh_items_tree()

    def _refresh_split_combo(self) -> None:
        """重建 Split 下拉：列出当前小票中 quantity>1 整数的 item。"""
        options = []
        items = (self.current_receipt_payload or {}).get("items", [])
        for index, item in enumerate(items):
            if is_splittable_item(item):
                options.append(f"#{index}: {item['quantity']} × {item['name']}")
        self.split_combo.configure(values=options)
        self.split_combo.set("")

    def _on_split_selected(self, _event) -> None:
        """把 Split 下拉选中的 item 拆成多个 quantity=1 的 item，可分别设置 owner。"""
        if self.current_receipt_payload is None:
            return
        selection = self.split_combo.get()
        if not selection:
            return
        index = int(selection.lstrip("#").split(":", 1)[0])
        items = self.current_receipt_payload["items"]
        count = int(Decimal(str(items[index]["quantity"])))
        split_quantity_items(items, [index])
        # 给拆分出的 item 分配 draft id（保存时若未改会自动转正式 id）
        self._draft_item_counter += 1
        start = self._draft_item_counter
        for offset in range(count):
            items[index + offset]["id"] = f"draft-item-{start + offset}"
        self._draft_item_counter = start + count - 1
        self.status_var.set(f"Split item into {count} × qty 1 (set owner per item if needed)")
        self._refresh_items_tree()

    def open_current_receipt_image(self) -> None:
        payload = self.current_receipt_payload
        if not payload or not payload.get("image_path"):
            messagebox.showinfo("Open Image", "No image path is set for this receipt.", parent=self)
            return
        try:
            open_path(payload["image_path"])
        except Exception as exc:
            messagebox.showerror("Open Image", str(exc), parent=self)

    def open_failed_archived_image(self) -> None:
        selection = self.failed_tree.selection()
        if not selection:
            return
        record = self.failed_records[int(selection[0])]
        try:
            open_path(record.archived_image_path)
        except Exception as exc:
            messagebox.showerror("Open Archived Image", str(exc), parent=self)

    def open_failed_original_path(self) -> None:
        selection = self.failed_tree.selection()
        if not selection:
            return
        record = self.failed_records[int(selection[0])]
        try:
            open_path(record.image_path)
        except Exception as exc:
            messagebox.showerror("Open Original Image", str(exc), parent=self)

    def reopen_failed_dialog(self) -> None:
        """PRD 8.2: move archived image back to input dir and refresh."""
        selection = self.failed_tree.selection()
        if not selection:
            return
        index = int(selection[0])
        record = self.failed_records[index]
        if not messagebox.askyesno(
            "Review & Resubmit",
            f"Reopen failed receipt?\n\n"
            f"Image: {record.image_path}\n"
            f"Failure: {record.failure_reason}\n"
            f"Attempts: {record.attempts}\n\n"
            f"This will move the archived image back to the input directory.\n"
            f"You can then trigger ingestion or fix the image manually.",
            parent=self,
        ):
            return
        try:
            dest = reopen_failed_receipt(self.paths, index)
            messagebox.showinfo("Review & Resubmit", f"Image moved to:\n{dest}\n\nYou can now re-process it via 'Trigger Ingestion' or the CLI.", parent=self)
        except Exception as exc:
            messagebox.showerror("Review & Resubmit", str(exc), parent=self)
            return
        self.refresh_all()

    def generate_report_from_form(self) -> None:
        try:
            year_text, month_text = self.report_month_var.get().strip().split("-", 1)
            year = int(year_text)
            month = int(month_text)
            written = generate_report(self.paths, year, month, write_schema=self.write_schema_var.get())
        except Exception as exc:
            messagebox.showerror("Generate Report", str(exc), parent=self)
            return
        self.status_var.set(f"Generated report {written.report.meta.report_month}")
        self.refresh_reports()
        self.reports_tree.selection_set(written.report.meta.report_month)
        self.reports_tree.focus(written.report.meta.report_month)

    def open_selected_report_html(self) -> None:
        selection = self.reports_tree.selection()
        if not selection:
            return
        entry = next((item for item in self.report_entries if item.report_month == selection[0]), None)
        if entry is None:
            return
        try:
            open_html_report(entry.html_path)
        except Exception as exc:
            messagebox.showerror("Open HTML Report", str(exc), parent=self)

    def open_selected_report_json(self) -> None:
        selection = self.reports_tree.selection()
        if not selection:
            return
        entry = next((item for item in self.report_entries if item.report_month == selection[0]), None)
        if entry is None:
            return
        try:
            open_path(entry.json_path)
        except Exception as exc:
            messagebox.showerror("Open JSON Report", str(exc), parent=self)


def run_app(paths: AppPaths | None = None) -> None:
    paths = paths or default_app_paths()
    ensure_receipt_flow_dirs(paths.project_root)
    # 全新环境/便携目录：无 owners.json 时首启生成默认单归属人模板
    ensure_owners_config(paths.owners_path)
    # 换机/移动目录后：把库里指向 receipt_input/ 的绝对路径改写为相对路径，
    # 避免旧机器的 C:\... 盘符在新电脑上失效（图片显示/移动依赖该路径）。
    normalize_store_image_paths(paths)
    app = ExpenseTrackerGui(paths=paths)
    app.mainloop()


def main() -> int:
    run_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
