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
    QPlainTextEdit, QTextBrowser, QLineEdit, QMessageBox, QSpinBox,
)

import datetime

from ..ai.ollama_client import OllamaClient, OllamaError
from ..ai.knowledge import KnowledgeStore
from ..ai.eva import Eva
from ..ai.fetch import fetch_url
from ..ai.vector_index import KnowledgeIndex

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
        self._workers = []
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

        # ── Memory (retrieval) row ──
        mrow = QHBoxLayout()
        mrow.addWidget(QLabel("Embed model:"))
        self.embed_edit = QLineEdit(
            self.settings.value("eva_embed_model", "nomic-embed-text"))
        self.embed_edit.setMaximumWidth(160)
        mrow.addWidget(self.embed_edit)
        mrow.addWidget(QLabel("Top-K:"))
        self.topk_spin = QSpinBox()
        self.topk_spin.setRange(1, 50)
        self.topk_spin.setValue(int(self.settings.value("eva_topk", 6)))
        mrow.addWidget(self.topk_spin)
        mrow.addWidget(QLabel("Context:"))
        self.ctx_spin = QSpinBox()
        self.ctx_spin.setRange(512, 131072)
        self.ctx_spin.setSingleStep(1024)
        self.ctx_spin.setValue(int(self.settings.value("eva_num_ctx", 8192)))
        self.ctx_spin.setSuffix(" tok")
        self.ctx_spin.setToolTip("Model context window — bigger = more memory per "
                                 "reply, but slower and more RAM.")
        mrow.addWidget(self.ctx_spin)
        self.rebuild_idx_btn = QPushButton("Rebuild memory index")
        self.rebuild_idx_btn.clicked.connect(self._rebuild_index)
        mrow.addWidget(self.rebuild_idx_btn)
        mrow.addStretch(1)
        outer.addLayout(mrow)

        # ── Knowledge row ──
        krow = QHBoxLayout()
        self.know_label = QLabel()
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

        # Retrieval index (persisted alongside the knowledge folder).
        self.index = KnowledgeIndex(self.knowledge, self._client(),
                                    self._embed_model())
        self._update_know_label()

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _client(self):
        url = self.url_edit.text().strip() or _DEFAULT_URL
        self.settings.setValue("eva_url", url)
        return OllamaClient(base_url=url)

    def _embed_model(self):
        m = self.embed_edit.text().strip() or "nomic-embed-text"
        self.settings.setValue("eva_embed_model", m)
        return m

    def _sync_index(self):
        self.index.client = self._client()
        self.index.embed_model = self._embed_model()

    def _eva(self):
        model = self.model_box.currentText().strip()
        self.settings.setValue("eva_model", model)
        self.settings.setValue("eva_topk", self.topk_spin.value())
        self.settings.setValue("eva_num_ctx", self.ctx_spin.value())
        self._sync_index()
        return Eva(self._client(), self.knowledge, model, index=self.index,
                   num_ctx=self.ctx_spin.value(), top_k=self.topk_spin.value())

    def _run(self, fn, on_done, on_fail):
        w = _Worker(fn)
        self._workers.append(w)
        w.done.connect(on_done)
        w.failed.connect(on_fail)
        w.finished.connect(lambda: self._workers.remove(w)
                           if w in self._workers else None)
        w.start()

    def _busy(self, busy):
        for w in (self.send_btn, self.refresh_btn, self.input,
                  getattr(self, "learn_btn", None), getattr(self, "url_learn", None),
                  getattr(self, "rebuild_idx_btn", None)):
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
        self._run(client.list_models, self._models_loaded, self._ollama_failed)

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
        self._run(lambda: eva.ask(msg, section_map, history=self.history[:-1]),
                  self._reply, self._ollama_failed)

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
        idx = getattr(self, "index", None)
        if not docs:
            self.know_label.setText("Knowledge: none yet (drop files in the inbox).")
            return
        if idx is None:
            self.know_label.setText(f"Knowledge: {len(docs)} doc(s).")
            return
        ndocs, nchunks = idx.stats()
        stale = " — stale, Rebuild memory index" if idx.is_stale() else ""
        self.know_label.setText(
            f"Knowledge: {len(docs)} doc(s), index {nchunks} chunk(s){stale}.")

    def _rebuild_index(self):
        self._sync_index()
        self._busy(True)
        self.status.setText("Building memory index (embedding docs — may take a "
                            "while)…")
        self._run(self.index.rebuild, self._index_built, self._index_failed)

    def _index_built(self, n_new):
        self._busy(False)
        ndocs, nchunks = self.index.stats()
        self._update_know_label()
        self.status.setText(
            f"Memory index ready: {ndocs} doc(s), {nchunks} chunk(s) "
            f"({n_new} newly embedded). Eva now retrieves the most relevant bits.")

    def _index_failed(self, msg):
        self._busy(False)
        self.status.setText(
            f"Could not build the index: {msg}\nPull an embedding model first, "
            "e.g.  ollama pull nomic-embed-text")

    def _learn_url(self):
        url = self.url_learn.text().strip()
        if not url:
            return
        self._busy(True)
        self.status.setText(f"Fetching {url} …")
        self._run(lambda: fetch_url(url),
                  lambda r: self._learned(url, r), self._learn_failed)

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
