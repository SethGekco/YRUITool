"""
EvaTab — the chat panel for the local-LLM assistant.

Eva talks to a local Ollama server, produces a modifier script, and the user
sends it to the Custom Script tab or previews it. Ollama calls run in a worker
thread so the UI stays responsive. Requires Ollama installed + a model pulled;
if it's unreachable, a friendly message explains what to do.
"""

import os

from PySide6.QtCore import Qt, QThread, Signal, QSettings, QUrl
from PySide6.QtGui import QFont, QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QPlainTextEdit, QTextBrowser, QLineEdit, QMessageBox,
)

import datetime

from ..ai.ollama_client import OllamaClient, OllamaError
from ..ai.knowledge import KnowledgeStore
from ..ai.eva import Eva
from ..ai.fetch import fetch_url

_DEFAULT_URL = "http://localhost:11434"


class _Worker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as e:  # OllamaError and anything else
            self.failed.emit(f"{type(e).__name__}: {e}")


class EvaTab(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.mw = main_window
        self.settings = QSettings("YRBalanceTool", "YRBalanceTool")
        self.knowledge = KnowledgeStore()
        self.history = []
        self._worker = None
        self._last_script = ""

        outer = QVBoxLayout(self)

        # ── Connection / model row ──
        top = QHBoxLayout()
        top.addWidget(QLabel("Ollama:"))
        self.url_edit = QLineEdit(self.settings.value("eva_url", _DEFAULT_URL))
        self.url_edit.setMaximumWidth(220)
        top.addWidget(self.url_edit)
        top.addWidget(QLabel("Model:"))
        self.model_box = QComboBox()
        self.model_box.setMinimumWidth(180)
        last_model = self.settings.value("eva_model", "")
        if last_model:
            self.model_box.addItem(last_model)
        top.addWidget(self.model_box, 1)
        self.refresh_btn = QPushButton("Refresh models")
        self.refresh_btn.clicked.connect(self.refresh_models)
        top.addWidget(self.refresh_btn)
        outer.addLayout(top)

        # ── Transcript ──
        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(False)
        self.transcript.setPlaceholderText(
            "Ask Eva to rebalance something, e.g. "
            "\"halve the cost of all tier-1 infantry except the attack dog\". "
            "She writes a modifier script you can preview and apply.")
        outer.addWidget(self.transcript, 2)

        # ── Input row ──
        inrow = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Tell Eva what to change…")
        self.input.returnPressed.connect(self.send)
        inrow.addWidget(self.input, 1)
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send)
        inrow.addWidget(self.send_btn)
        outer.addLayout(inrow)

        # ── Generated script ──
        outer.addWidget(QLabel("Eva's script:"))
        self.script_view = QPlainTextEdit()
        self.script_view.setFont(QFont("monospace", 10))
        self.script_view.setMaximumHeight(160)
        outer.addWidget(self.script_view, 1)
        srow = QHBoxLayout()
        self.apply_btn = QPushButton("Apply to rules")
        self.apply_btn.clicked.connect(self._apply)
        self.preview_btn = QPushButton("Preview")
        self.preview_btn.clicked.connect(self._preview)
        self.send_script_btn = QPushButton("Send to Custom Script")
        self.send_script_btn.clicked.connect(self._send_to_custom)
        srow.addWidget(self.apply_btn)
        srow.addWidget(self.preview_btn)
        srow.addWidget(self.send_script_btn)
        srow.addStretch(1)
        outer.addLayout(srow)

        # ── Learn-from-URL row ──
        urow = QHBoxLayout()
        urow.addWidget(QLabel("Learn from URL:"))
        self.url_learn = QLineEdit()
        self.url_learn.setPlaceholderText(
            "paste a tutorial / wiki page (e.g. a ModENC page) and press Enter")
        self.url_learn.returnPressed.connect(self._learn_url)
        urow.addWidget(self.url_learn, 1)
        self.learn_btn = QPushButton("Fetch and learn")
        self.learn_btn.clicked.connect(self._learn_url)
        urow.addWidget(self.learn_btn)
        outer.addLayout(urow)

        # ── Knowledge row ──
        krow = QHBoxLayout()
        self.know_label = QLabel()
        self._update_know_label()
        krow.addWidget(self.know_label, 1)
        remember_btn = QPushButton("Remember this chat")
        remember_btn.clicked.connect(self._remember_chat)
        scan_btn = QPushButton("Scan inbox")
        scan_btn.clicked.connect(self._scan_inbox)
        open_btn = QPushButton("Open folder")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(self.knowledge.base_dir)))
        krow.addWidget(remember_btn)
        krow.addWidget(scan_btn)
        krow.addWidget(open_btn)
        outer.addLayout(krow)

        self.status = QLabel("Install Ollama and pull a model, then Refresh models.")
        self.status.setStyleSheet("color: gray;")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _client(self):
        url = self.url_edit.text().strip() or _DEFAULT_URL
        self.settings.setValue("eva_url", url)
        return OllamaClient(base_url=url)

    def _eva(self):
        model = self.model_box.currentText().strip()
        self.settings.setValue("eva_model", model)
        return Eva(self._client(), self.knowledge, model)

    def _busy(self, busy):
        for w in (self.send_btn, self.refresh_btn, self.input,
                  getattr(self, "learn_btn", None), getattr(self, "url_learn", None)):
            if w is not None:
                w.setEnabled(not busy)

    def _append(self, who, text):
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.transcript.append(f"<b>{who}:</b> {safe}<br>")

    # ── Models ───────────────────────────────────────────────────────────────
    def refresh_models(self):
        client = self._client()
        self._busy(True)
        self.status.setText("Contacting Ollama…")
        self._worker = _Worker(client.list_models)
        self._worker.done.connect(self._models_loaded)
        self._worker.failed.connect(self._ollama_failed)
        self._worker.start()

    def _models_loaded(self, models):
        self._busy(False)
        current = self.model_box.currentText()
        self.model_box.clear()
        self.model_box.addItems(models)
        if current and current in models:
            self.model_box.setCurrentText(current)
        if models:
            self.status.setText(f"{len(models)} model(s) available. Ask Eva anything.")
        else:
            self.status.setText("Ollama is running but has no models. "
                                "Pull one, e.g.  ollama pull llama3.1")

    def _ollama_failed(self, msg):
        self._busy(False)
        self.status.setText(
            f"{msg}\nInstall Ollama (ollama.com), start it, and pull a model "
            "(e.g.  ollama pull llama3.1), then Refresh models.")

    # ── Chat ─────────────────────────────────────────────────────────────────
    def send(self):
        msg = self.input.text().strip()
        if not msg:
            return
        if not self.model_box.currentText().strip():
            QMessageBox.information(self, "Eva", "Pick a model first (Refresh models).")
            return
        self.input.clear()
        self._append("You", msg)
        self.history.append({"role": "user", "content": msg})
        eva = self._eva()
        section_map = self.mw.section_map
        self._busy(True)
        self.status.setText("Eva is thinking… (local models can take a while)")
        self._worker = _Worker(
            lambda: eva.ask(msg, section_map, history=self.history[:-1]))
        self._worker.done.connect(self._reply)
        self._worker.failed.connect(self._ollama_failed)
        self._worker.start()

    def _reply(self, reply):
        self._busy(False)
        self.history.append({"role": "assistant", "content": reply.text})
        self._append("Eva", reply.text)
        if reply.script:
            self._last_script = reply.script
            self.script_view.setPlainText(reply.script)
            if reply.valid:
                self.status.setText("Script looks valid. Preview or send it to "
                                    "the Custom Script tab.")
            else:
                self.status.setText(f"Script has an issue: {reply.error} "
                                    "— you can edit it below.")
        else:
            self.status.setText("Eva replied without a script.")

    # ── Script actions ───────────────────────────────────────────────────────
    def _current_script(self):
        return self.script_view.toPlainText().strip()

    def _send_to_custom(self):
        s = self._current_script()
        if not s:
            return
        self.mw.script_panel.editor.setPlainText(s)
        self.mw.tabs.setCurrentWidget(self.mw.script_tab)

    def _preview(self):
        s = self._current_script()
        if s:
            self.mw.preview_script_text(s)

    def _apply(self):
        s = self._current_script()
        if s:
            self.mw.apply_script_to_rules(s)

    # ── Knowledge ────────────────────────────────────────────────────────────
    def _scan_inbox(self):
        ingested = self.knowledge.scan_inbox()
        self._update_know_label()
        if ingested:
            names = ", ".join(n for n, _ in ingested)
            self.status.setText(f"Ingested {len(ingested)} doc(s): {names}")
        else:
            self.status.setText(
                f"No new files in {self.knowledge.inbox}. Drop .txt/.md files there.")

    def _update_know_label(self):
        docs = self.knowledge.list_docs()
        if docs:
            self.know_label.setText(f"Knowledge: {len(docs)} doc(s) ingested.")
        else:
            self.know_label.setText("Knowledge: none yet (drop files in the inbox).")

    def _learn_url(self):
        url = self.url_learn.text().strip()
        if not url:
            return
        self._busy(True)
        self.status.setText(f"Fetching {url} …")
        self._worker = _Worker(lambda: fetch_url(url))
        self._worker.done.connect(lambda r: self._learned(url, r))
        self._worker.failed.connect(self._learn_failed)
        self._worker.start()

    def _learned(self, url, result):
        self._busy(False)
        title, text = result
        if not text.strip():
            self.status.setText("Fetched, but found no readable text "
                                "(a script-heavy page?).")
            return
        path = self.knowledge.add_doc(title, f"Source: {url}\n\n{text}")
        self.url_learn.clear()
        self._update_know_label()
        self.status.setText(f"Learned “{os.path.basename(path)}” "
                            f"({len(text)} chars). Eva will use it as reference.")

    def _learn_failed(self, msg):
        self._busy(False)
        self.status.setText(f"Could not fetch that URL: {msg}")

    def _transcript_text(self):
        lines = []
        for m in self.history:
            who = "You" if m.get("role") == "user" else "Eva"
            lines.append(f"{who}: {m.get('content', '')}")
        return "\n\n".join(lines)

    def _remember_chat(self):
        if not self.history:
            self.status.setText("No conversation to remember yet.")
            return
        name = "Eva chat " + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".md"
        path = self.knowledge.add_doc(name, self._transcript_text())
        self._update_know_label()
        self.status.setText(
            f"Saved this chat to knowledge ({os.path.basename(path)}). "
            "Eva will remember it next time.")
