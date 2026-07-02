"""Tkinter desktop GUI for receipt review and report generation."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tkinter import messagebox, ttk

from expense_tracker.gui.services import (
    AppPaths,
    build_new_receipt_draft,
    default_app_paths,
    delete_receipt,
    generate_report,
    list_reports,
    load_app_state,
    open_html_report,
    open_path,
    receipt_to_edit_payload,
    reopen_failed_receipt,
    save_receipt_edit,
    trigger_ingestion,
)
from expense_tracker.schemas.domain import FailedOcrRecord, ReceiptRecord
from expense_tracker.schemas.enums import ItemCategory, OcrStatus, OwnerMode


def _previous_month_string(today: date | None = None) -> str:
    today = today or date.today()
    if today.month == 1:
        return f"{today.year - 1:04d}-12"
    return f"{today.year:04d}-{today.month - 1:02d}"


@dataclass
class ReceiptItemDialogResult:
    payload: dict


class ReceiptItemDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, owner_ids: list[str], item_payload: dict | None = None):
        super().__init__(parent)
        self.title("Receipt Item")
        self.resizable(False, False)
        self.transient(parent)
        self.result: ReceiptItemDialogResult | None = None
        self.owner_ids = owner_ids
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
        buttons.grid(row=len(labels), column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="OK", command=self._submit).pack(side="right")

        self.bind("<Return>", lambda event: self._submit())
        self.bind("<Escape>", lambda event: self.destroy())
        self.grab_set()
        self.wait_visibility()
        self.focus()

    def _submit(self) -> None:
        payload = {"id": self.item_id}
        for key, variable in self.vars.items():
            payload[key] = variable.get().strip()
        self.result = ReceiptItemDialogResult(payload=payload)
        self.destroy()


class ExpenseTrackerGui(tk.Tk):
    def __init__(self, paths: AppPaths | None = None):
        super().__init__()
        self.paths = paths or default_app_paths()
        self.title("Expense Tracker GUI")
        self.geometry("1480x900")
        self.minsize(1220, 780)

        self.owner_ids: list[str] = []
        self.owner_names: dict[str, str] = {}
        self.current_receipt_payload: dict | None = None
        self.receipt_index: dict[str, ReceiptRecord] = {}
        self.failed_records: list[FailedOcrRecord] = []
        self.report_entries = []
        self._draft_item_counter = 0

        self._build_style()
        self._build_shell()
        self.refresh_all()

    def _build_style(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Header.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Subtle.TLabel", foreground="#5f6b66")

    def _build_shell(self) -> None:
        top = ttk.Frame(self, padding=(14, 12))
        top.pack(fill="x")
        ttk.Label(top, text="Expense Tracker Desktop", style="Header.TLabel").pack(anchor="w")
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
        notebook.add(self.receipts_tab, text="Receipts")
        notebook.add(self.failed_tab, text="Failed OCR")
        notebook.add(self.reports_tab, text="Reports")

        self._build_receipts_tab()
        self._build_failed_tab()
        self._build_reports_tab()

    def _build_receipts_tab(self) -> None:
        paned = ttk.Panedwindow(self.receipts_tab, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=1)
        paned.add(right, weight=3)

        ttk.Label(left, text="Scanned Receipts").pack(anchor="w", pady=(0, 6))
        self.receipt_tree = ttk.Treeview(
            left,
            columns=("merchant", "date", "total", "status"),
            show="headings",
            selectmode="browse",
        )
        for column, heading, width in (
            ("merchant", "Merchant", 180),
            ("date", "Date", 90),
            ("total", "Total", 90),
            ("status", "Status", 100),
        ):
            self.receipt_tree.heading(column, text=heading)
            self.receipt_tree.column(column, width=width, anchor="w")
        self.receipt_tree.pack(fill="both", expand=True)
        self.receipt_tree.bind("<<TreeviewSelect>>", self._on_receipt_selected)

        canvas = tk.Canvas(right, highlightthickness=0)
        scroll = ttk.Scrollbar(right, orient="vertical", command=canvas.yview)
        form_host = ttk.Frame(canvas)
        form_host.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=form_host, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        form = ttk.LabelFrame(form_host, text="Receipt Details", padding=12)
        form.pack(fill="x", expand=True)
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
        self.is_verified_var = tk.BooleanVar(value=False)

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
        ttk.Label(form, text="Verified").grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
        ttk.Checkbutton(form, variable=self.is_verified_var).grid(row=row, column=1, sticky="w", pady=4)
        row += 1

        ttk.Label(form, text="Review Notes").grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=(6, 4))
        self.review_notes_text = tk.Text(form, height=4, width=45)
        self.review_notes_text.grid(row=row, column=1, sticky="ew", pady=(6, 4))
        row += 1
        ttk.Label(form, text="OCR Raw Text").grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=(6, 4))
        self.ocr_raw_text = tk.Text(form, height=6, width=45)
        self.ocr_raw_text.grid(row=row, column=1, sticky="ew", pady=(6, 4))
        form.columnconfigure(1, weight=1)

        items_frame = ttk.LabelFrame(form_host, text="Formal Items", padding=12)
        items_frame.pack(fill="both", expand=True, pady=(10, 0))
        item_toolbar = ttk.Frame(items_frame)
        item_toolbar.pack(fill="x", pady=(0, 6))
        ttk.Button(item_toolbar, text="Add Item", command=self.add_item).pack(side="left")
        ttk.Button(item_toolbar, text="Edit Item", command=self.edit_selected_item).pack(side="left", padx=(8, 0))
        ttk.Button(item_toolbar, text="Delete Item", command=self.delete_selected_item).pack(side="left", padx=(8, 0))

        self.items_tree = ttk.Treeview(
            items_frame,
            columns=("name", "category", "quantity", "unit_price", "total_price", "owner"),
            show="headings",
            height=8,
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
        try:
            receipt_id = trigger_ingestion(self.paths, file_path)
            messagebox.showinfo("Ingestion Complete", f"Receipt {receipt_id} processed successfully.", parent=self)
        except Exception as exc:
            messagebox.showerror("Ingestion Failed", str(exc), parent=self)
            return
        self.refresh_all()

    def refresh_receipts(self) -> None:
        store, owners = load_app_state(self.paths)
        self.owner_ids = [owner.id for owner in owners.owners]
        self.owner_names = {owner.id: owner.name for owner in owners.owners}
        self.receipt_index = {receipt.id: receipt for receipt in store.receipts}
        for tree_item in self.receipt_tree.get_children():
            self.receipt_tree.delete(tree_item)
        for receipt in sorted(store.receipts, key=lambda item: item.purchase_date, reverse=True):
            self.receipt_tree.insert(
                "",
                "end",
                iid=receipt.id,
                values=(
                    receipt.merchant,
                    receipt.purchase_date.isoformat(),
                    f"{receipt.total_amount:.2f}",
                    receipt.ocr_status.value,
                ),
            )

        self.default_owner_combo.configure(values=self.owner_ids)

        if self.current_receipt_payload and self.current_receipt_payload.get("id") in self.receipt_index:
            self.load_receipt_into_form(self.receipt_index[self.current_receipt_payload["id"]])
        elif self.receipt_index:
            first_receipt = next(iter(sorted(self.receipt_index.values(), key=lambda item: item.purchase_date, reverse=True)))
            self.load_receipt_into_form(first_receipt)
        else:
            self.new_receipt()

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
        self._refresh_items_tree()
        self._refresh_removed_tree()

    def _refresh_items_tree(self) -> None:
        for item in self.items_tree.get_children():
            self.items_tree.delete(item)
        payload = self.current_receipt_payload or {"items": []}
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
                    self.owner_names.get(item["owner_id"], item["owner_id"]),
                ),
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
        try:
            payload = self._gather_receipt_payload_from_form()
            record = save_receipt_edit(
                self.paths,
                payload,
                payload.get("items", []),
                payload.get("removed_items", []),
            )
        except Exception as exc:
            messagebox.showerror("Save Receipt", str(exc), parent=self)
            return
        self.status_var.set(f"Saved {record.id}")
        self.refresh_receipts()
        self.receipt_tree.selection_set(record.id)
        self.receipt_tree.focus(record.id)

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
        dialog = ReceiptItemDialog(self, self.owner_ids)
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
        dialog = ReceiptItemDialog(self, self.owner_ids, self.current_receipt_payload["items"][index])
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
    app = ExpenseTrackerGui(paths=paths)
    app.mainloop()


def main() -> int:
    run_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
