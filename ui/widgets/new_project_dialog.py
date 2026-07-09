"""New-project dialog: name + baseline_id + starting source (template/clone/import)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from baseline.errors import BaselineError
from baseline.newproject import prepare_new_baseline
from baseline.store import today_str
from ui import baseline_service


class NewProjectDialog(QDialog):
    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建项目")
        self.setMinimumWidth(520)
        self._import_path: Optional[Path] = None
        self._doc_path: Optional[Path] = None
        self.created_id: Optional[str] = None
        # 当选择"从文档生成"时，创建改为异步（需 LLM），由调用方处理该请求
        self.generate_request: Optional[Dict[str, Any]] = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QGridLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：少儿编程创作课程")
        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("小写字母/数字/-/_，3-81 字符，如 kids_coding_course")
        form.addWidget(QLabel("项目名称"), 0, 0)
        form.addWidget(self.name_edit, 0, 1)
        form.addWidget(QLabel("项目标识"), 1, 0)
        form.addWidget(self.id_edit, 1, 1)
        form.setColumnStretch(1, 1)
        layout.addLayout(form)

        source_group = QGroupBox("起始基线")
        source_layout = QVBoxLayout(source_group)
        self._source_group = QButtonGroup(self)
        self.template_radio = QRadioButton("从内置模板新建（复制默认基线结构，再自行编辑/合并）")
        self.clone_radio = QRadioButton("复制现有项目")
        self.import_radio = QRadioButton("导入 JSON 文件")
        self.generate_radio = QRadioButton("从文档生成（上传项目介绍，自动分析生成新基线，需 API）")
        self.template_radio.setChecked(True)
        for rb in (self.template_radio, self.clone_radio, self.import_radio, self.generate_radio):
            self._source_group.addButton(rb)
            source_layout.addWidget(rb)

        clone_row = QHBoxLayout()
        clone_row.addWidget(QLabel("来源项目"))
        self.clone_combo = QComboBox()
        for info in baseline_service.projects():
            self.clone_combo.addItem(f"{info.name}（{info.baseline_id}）", info.baseline_id)
        self.clone_combo.setEnabled(False)
        clone_row.addWidget(self.clone_combo, 1)
        source_layout.addLayout(clone_row)

        import_row = QHBoxLayout()
        self.import_button = QPushButton("选择 JSON…")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self._choose_import)
        self.import_label = QLabel("未选择")
        self.import_label.setObjectName("Subtitle")
        import_row.addWidget(self.import_button)
        import_row.addWidget(self.import_label, 1)
        source_layout.addLayout(import_row)

        doc_row = QHBoxLayout()
        self.doc_button = QPushButton("选择文档…")
        self.doc_button.setEnabled(False)
        self.doc_button.clicked.connect(self._choose_doc)
        self.doc_label = QLabel("未选择（支持 PDF/DOCX/TXT）")
        self.doc_label.setObjectName("Subtitle")
        doc_row.addWidget(self.doc_button)
        doc_row.addWidget(self.doc_label, 1)
        source_layout.addLayout(doc_row)
        layout.addWidget(source_group)

        self.clone_radio.toggled.connect(self.clone_combo.setEnabled)
        self.import_radio.toggled.connect(self.import_button.setEnabled)
        self.generate_radio.toggled.connect(self.doc_button.setEnabled)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("创建")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_create)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择基线 JSON", "", "JSON (*.json)")
        if path:
            self._import_path = Path(path)
            self.import_label.setText(self._import_path.name)

    def _choose_doc(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择项目介绍/背景文档", "", "文档 (*.pdf *.docx *.txt *.md)"
        )
        if path:
            self._doc_path = Path(path)
            self.doc_label.setText(self._doc_path.name)

    def _source_baseline(self) -> Dict[str, Any]:
        if self.clone_radio.isChecked():
            baseline_id = self.clone_combo.currentData()
            if not baseline_id:
                raise ValueError("请选择要复制的来源项目")
            return baseline_service.load_project_baseline(baseline_id)
        if self.import_radio.isChecked():
            if not self._import_path:
                raise ValueError("请选择要导入的 JSON 文件")
            return json.loads(self._import_path.read_text(encoding="utf-8"))
        return baseline_service.bundled_baseline()

    def _on_create(self) -> None:
        name = self.name_edit.text().strip()
        baseline_id = self.id_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "缺少项目名称", "请填写项目名称。")
            return
        if not baseline_id:
            QMessageBox.warning(self, "缺少项目标识", "请填写项目标识（baseline_id）。")
            return
        if self.generate_radio.isChecked():
            # 从文档生成需要调 LLM，交给调用方异步处理（此处仅收集请求）
            if not self._doc_path:
                QMessageBox.warning(self, "缺少文档", "请选择用于生成基线的文档。")
                return
            self.generate_request = {"name": name, "baseline_id": baseline_id, "path": self._doc_path}
            self.accept()
            return
        try:
            source = self._source_baseline()
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "起始基线无效", str(exc))
            return
        baseline = prepare_new_baseline(source, baseline_id, name, today_str())
        try:
            info = baseline_service.create_project(baseline)
        except BaselineError as exc:
            QMessageBox.warning(self, "创建失败", str(exc))
            return
        self.created_id = info.baseline_id
        self.accept()
