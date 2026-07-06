#!/usr/bin/env python3
"""Desktop GUI for repeatable DashDesign print workflows."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox


PROJECT_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
PYTHON = str(VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable))


class PrintWorkflowApp(tk.Tk):
    """Classic Tk-only app.

    The macOS system Python on this machine ships with Tk 8.5.9. Its ttk
    widgets can open a window without painting child controls, so the GUI uses
    only classic Tk widgets.
    """

    def __init__(self) -> None:
        super().__init__()
        self.title("DashDesign 印刷图片工作流")
        self.geometry("1060x760")
        self.minsize(920, 650)
        self.configure(bg="#f4f4f4")

        self.process: subprocess.Popen[str] | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.last_output_dir: Path | None = None
        self.current_tab = tk.StringVar(value="batch")
        self.tab_buttons: dict[str, tk.Button] = {}

        self._build_vars()
        self._build_ui()
        self.after(50, self._bring_to_front)
        self.after(120, self._drain_log_queue)

    def _bring_to_front(self) -> None:
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
            if sys.platform == "darwin":
                self.attributes("-topmost", True)
                self.after(250, self._clear_topmost)
        except tk.TclError:
            pass

    def _clear_topmost(self) -> None:
        try:
            self.attributes("-topmost", False)
        except tk.TclError:
            pass

    def _build_vars(self) -> None:
        self.input_dir = tk.StringVar(value=str(PROJECT_ROOT))
        self.batch_output_dir = tk.StringVar(value=str(PROJECT_ROOT / "print_ready_desktop"))
        self.batch_dpi = tk.StringVar(value="200")
        self.batch_only = tk.StringVar(value="")
        self.batch_workflow = tk.StringVar(value="style_preserved")
        self.batch_force = tk.BooleanVar(value=True)
        self.batch_keep_masters = tk.BooleanVar(value=False)

        self.gpt_source = tk.StringVar(value="")
        self.gpt_output_dir = tk.StringVar(value=str(PROJECT_ROOT / "workflow_samples" / "desktop_gpt_image_rebuild"))
        self.gpt_dpi = tk.StringVar(value="200")
        self.gpt_mode = tk.StringVar(value="edit")
        self.gpt_execute = tk.BooleanVar(value=False)
        self.gpt_base_url = tk.StringVar(value="")
        self.gpt_api_key = tk.StringVar(value="")
        self.gpt_description = tk.StringVar(value="")

        self.qr_input = tk.StringVar(value="")
        self.qr_output_dir = tk.StringVar(value=str(PROJECT_ROOT / "single_no_qr_desktop"))
        self.qr_box = tk.StringVar(value="")
        self.qr_reference_size = tk.StringVar(value="")
        self.qr_margin = tk.StringVar(value="0.55")
        self.qr_radius = tk.StringVar(value="21")

    def _build_ui(self) -> None:
        self.main = tk.Frame(self, bg="#f4f4f4", padx=14, pady=12)
        self.main.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(4, weight=1)

        header = tk.Frame(self.main, bg="#f4f4f4")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        tk.Label(
            header,
            text="DashDesign 印刷图片工作流",
            font=("Helvetica", 18, "bold"),
            bg="#f4f4f4",
            fg="#222222",
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Button(
            header,
            text="打开项目目录",
            command=lambda: self.open_path(PROJECT_ROOT),
            padx=12,
            pady=4,
        ).grid(row=0, column=1, sticky="e")

        tabs = tk.Frame(self.main, bg="#f4f4f4")
        tabs.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        for index, (key, label) in enumerate(
            [
                ("batch", "批量印刷"),
                ("gpt", "GPT 重建"),
                ("qr", "去二维码留空"),
            ]
        ):
            button = tk.Button(
                tabs,
                text=label,
                width=16,
                command=lambda tab_key=key: self.show_tab(tab_key),
                padx=8,
                pady=5,
            )
            button.grid(row=0, column=index, padx=(0, 8), sticky="w")
            self.tab_buttons[key] = button

        self.content = tk.Frame(self.main, bg="#ffffff", bd=1, relief=tk.SOLID, padx=12, pady=12)
        self.content.grid(row=2, column=0, sticky="ew")
        self.content.grid_columnconfigure(0, weight=1)

        self.forms: dict[str, tk.Frame] = {
            "batch": self._make_batch_form(),
            "gpt": self._make_gpt_form(),
            "qr": self._make_qr_form(),
        }
        self.show_tab("batch")

        controls = tk.Frame(self.main, bg="#f4f4f4")
        controls.grid(row=3, column=0, sticky="ew", pady=(12, 8))
        controls.grid_columnconfigure(4, weight=1)
        tk.Button(
            controls,
            text="运行当前工作流",
            command=self.run_current,
            width=16,
            padx=8,
            pady=5,
        ).grid(row=0, column=0, sticky="w")
        tk.Button(controls, text="停止", command=self.stop_process, width=10, pady=5).grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )
        tk.Button(
            controls,
            text="打开最近输出",
            command=self.open_last_output,
            width=14,
            pady=5,
        ).grid(row=0, column=2, sticky="w", padx=(8, 0))
        tk.Button(
            controls,
            text="清空日志",
            command=lambda: self.log_text.delete("1.0", tk.END),
            width=12,
            pady=5,
        ).grid(row=0, column=5, sticky="e")

        log_box = tk.Frame(self.main, bg="#f4f4f4")
        log_box.grid(row=4, column=0, sticky="nsew")
        log_box.grid_columnconfigure(0, weight=1)
        log_box.grid_rowconfigure(1, weight=1)
        tk.Label(log_box, text="运行日志", bg="#f4f4f4", fg="#333333", anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        text_frame = tk.Frame(log_box, bg="#d6d6d6")
        text_frame.grid(row=1, column=0, sticky="nsew")
        text_frame.grid_columnconfigure(0, weight=1)
        text_frame.grid_rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            text_frame,
            wrap=tk.WORD,
            height=16,
            bg="#111111",
            fg="#eeeeee",
            insertbackground="#eeeeee",
            relief=tk.FLAT,
            padx=8,
            pady=8,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = tk.Scrollbar(text_frame, command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)
        self.append_log("工具已启动。请选择工作流并运行。")

    def _row(
        self,
        parent: tk.Frame,
        label: str,
        variable: tk.StringVar,
        choose: str | None = None,
        show: str | None = None,
    ) -> None:
        row = tk.Frame(parent, bg="#ffffff")
        row.grid_columnconfigure(1, weight=1)
        row.pack(fill=tk.X, pady=4)
        tk.Label(row, text=label, width=13, anchor="w", bg="#ffffff", fg="#222222").grid(
            row=0, column=0, sticky="w"
        )
        tk.Entry(row, textvariable=variable, show=show).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        if choose:
            tk.Button(row, text="选择", command=lambda: self.choose_path(variable, choose), width=8).grid(
                row=0, column=2, sticky="e"
            )

    def _make_batch_form(self) -> tk.Frame:
        frame = tk.Frame(self.content, bg="#ffffff")
        self._row(frame, "输入目录", self.input_dir, "dir")
        self._row(frame, "输出目录", self.batch_output_dir, "dir")

        workflow = tk.Frame(frame, bg="#ffffff")
        workflow.pack(fill=tk.X, pady=6)
        tk.Label(workflow, text="工作流", width=13, anchor="w", bg="#ffffff", fg="#222222").pack(side=tk.LEFT)
        tk.Radiobutton(
            workflow,
            text="保留原字效高清化（推荐）",
            variable=self.batch_workflow,
            value="style_preserved",
            bg="#ffffff",
            anchor="w",
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            workflow,
            text="基础 200dpi",
            variable=self.batch_workflow,
            value="pil",
            bg="#ffffff",
            anchor="w",
        ).pack(side=tk.LEFT, padx=(12, 0))

        options = tk.Frame(frame, bg="#ffffff")
        options.pack(fill=tk.X, pady=4)
        options.grid_columnconfigure(3, weight=1)
        tk.Label(options, text="DPI", width=13, anchor="w", bg="#ffffff", fg="#222222").grid(
            row=0, column=0, sticky="w"
        )
        tk.Entry(options, textvariable=self.batch_dpi, width=8).grid(row=0, column=1, sticky="w")
        tk.Label(options, text="只处理文件", bg="#ffffff", fg="#222222").grid(
            row=0, column=2, sticky="w", padx=(16, 4)
        )
        tk.Entry(options, textvariable=self.batch_only).grid(row=0, column=3, sticky="ew")

        checks = tk.Frame(frame, bg="#ffffff")
        checks.pack(fill=tk.X, pady=4)
        tk.Checkbutton(
            checks,
            text="覆盖/强制重新生成",
            variable=self.batch_force,
            bg="#ffffff",
        ).pack(side=tk.LEFT)
        tk.Checkbutton(
            checks,
            text="保留 Real-ESRGAN 中间 master",
            variable=self.batch_keep_masters,
            bg="#ffffff",
        ).pack(side=tk.LEFT, padx=(16, 0))

        tk.Label(
            frame,
            text="说明：不重建文字、不重建二维码；文件名需包含类似 120乘以80 的厘米尺寸。",
            bg="#ffffff",
            fg="#555555",
            anchor="w",
        ).pack(fill=tk.X, pady=(8, 0))
        return frame

    def _make_gpt_form(self) -> tk.Frame:
        frame = tk.Frame(self.content, bg="#ffffff")
        self._row(frame, "源图片", self.gpt_source, "file")
        self._row(frame, "输出目录", self.gpt_output_dir, "dir")

        row = tk.Frame(frame, bg="#ffffff")
        row.pack(fill=tk.X, pady=4)
        tk.Label(row, text="模式/DPI", width=13, anchor="w", bg="#ffffff", fg="#222222").pack(side=tk.LEFT)
        tk.OptionMenu(row, self.gpt_mode, "edit", "generate").pack(side=tk.LEFT)
        tk.Entry(row, textvariable=self.gpt_dpi, width=8).pack(side=tk.LEFT, padx=(12, 0))
        tk.Checkbutton(
            row,
            text="立即调用 API",
            variable=self.gpt_execute,
            bg="#ffffff",
        ).pack(side=tk.LEFT, padx=(16, 0))

        self._row(frame, "Base URL", self.gpt_base_url)
        self._row(frame, "API Key", self.gpt_api_key, show="*")
        self._row(frame, "描述补充", self.gpt_description)
        tk.Label(
            frame,
            text="说明：不填写 API Key 时只生成请求包。API Key 仅作为本次进程环境变量传入，不写入文件。",
            bg="#ffffff",
            fg="#555555",
            anchor="w",
        ).pack(fill=tk.X, pady=(8, 0))
        return frame

    def _make_qr_form(self) -> tk.Frame:
        frame = tk.Frame(self.content, bg="#ffffff")
        self._row(frame, "输入图片", self.qr_input, "file")
        self._row(frame, "输出目录", self.qr_output_dir, "dir")
        self._row(frame, "清除区域", self.qr_box)
        self._row(frame, "参考尺寸", self.qr_reference_size)

        row = tk.Frame(frame, bg="#ffffff")
        row.pack(fill=tk.X, pady=4)
        tk.Label(row, text="边界/半径", width=13, anchor="w", bg="#ffffff", fg="#222222").pack(side=tk.LEFT)
        tk.Entry(row, textvariable=self.qr_margin, width=8).pack(side=tk.LEFT)
        tk.Entry(row, textvariable=self.qr_radius, width=8).pack(side=tk.LEFT, padx=(12, 0))
        tk.Label(
            frame,
            text="说明：只清除指定区域，不识别、不生成二维码。区域格式：x1,y1,x2,y2。",
            bg="#ffffff",
            fg="#555555",
            anchor="w",
        ).pack(fill=tk.X, pady=(8, 0))
        return frame

    def show_tab(self, key: str) -> None:
        self.current_tab.set(key)
        for form_key, form in self.forms.items():
            if form_key == key:
                form.grid(row=0, column=0, sticky="ew")
            else:
                form.grid_remove()
        for button_key, button in self.tab_buttons.items():
            if button_key == key:
                button.configure(bg="#222222", fg="#ffffff", activebackground="#333333", activeforeground="#ffffff")
            else:
                button.configure(bg="#e8e8e8", fg="#222222", activebackground="#dddddd", activeforeground="#222222")

    def choose_path(self, variable: tk.StringVar, mode: str) -> None:
        if mode == "dir":
            selected = filedialog.askdirectory(initialdir=variable.get() or str(PROJECT_ROOT))
        else:
            selected = filedialog.askopenfilename(initialdir=str(PROJECT_ROOT))
        if selected:
            variable.set(selected)

    def run_current(self) -> None:
        if self.process is not None:
            messagebox.showwarning("正在运行", "已有工作流正在运行，请先停止或等待完成。")
            return
        try:
            command, output_dir, env = self.build_current_command()
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        self.last_output_dir = output_dir
        self.append_log("$ " + " ".join(command))
        self.process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._read_process_output, daemon=True).start()

    def build_current_command(self) -> tuple[list[str], Path, dict[str, str]]:
        tab = self.current_tab.get()
        if tab == "batch":
            return self.build_batch_command()
        if tab == "gpt":
            return self.build_gpt_command()
        return self.build_qr_command()

    def build_batch_command(self) -> tuple[list[str], Path, dict[str, str]]:
        input_dir = Path(self.input_dir.get()).expanduser()
        output_dir = Path(self.batch_output_dir.get()).expanduser()
        if not input_dir.exists():
            raise ValueError("输入目录不存在")
        dpi = self.batch_dpi.get().strip()
        command = [PYTHON]
        if self.batch_workflow.get() == "style_preserved":
            command += [
                "scripts/batch_style_preserved_print.py",
                "--input-dir",
                str(input_dir),
                "--output-dir",
                str(output_dir),
                "--dpi",
                dpi,
            ]
            if self.batch_force.get():
                command.append("--force")
            if self.batch_keep_masters.get():
                command.append("--keep-masters")
        else:
            command += [
                "scripts/prepare_print_assets.py",
                "--input-dir",
                str(input_dir),
                "--output-dir",
                str(output_dir),
                "--dpi",
                dpi,
            ]
        only = self.batch_only.get().strip()
        if only:
            command += ["--only", only]
        return command, output_dir, os.environ.copy()

    def build_gpt_command(self) -> tuple[list[str], Path, dict[str, str]]:
        source = Path(self.gpt_source.get()).expanduser()
        output_dir = Path(self.gpt_output_dir.get()).expanduser()
        if not source.exists():
            raise ValueError("源图片不存在")
        command = [
            PYTHON,
            "scripts/gpt_image_rebuild.py",
            str(source),
            "--output-dir",
            str(output_dir),
            "--print-dpi",
            self.gpt_dpi.get().strip(),
            "--api-mode",
            self.gpt_mode.get(),
        ]
        description = self.gpt_description.get().strip()
        if description:
            command += ["--description", description]
        if self.gpt_execute.get():
            command.append("--execute")
        env = os.environ.copy()
        if self.gpt_base_url.get().strip():
            env["OPENAI_BASE_URL"] = self.gpt_base_url.get().strip()
        if self.gpt_api_key.get().strip():
            env["OPENAI_API_KEY"] = self.gpt_api_key.get().strip()
        return command, output_dir, env

    def build_qr_command(self) -> tuple[list[str], Path, dict[str, str]]:
        source = Path(self.qr_input.get()).expanduser()
        output_dir = Path(self.qr_output_dir.get()).expanduser()
        if not source.exists():
            raise ValueError("输入图片不存在")
        box = self.qr_box.get().strip()
        if not box:
            raise ValueError("请填写清除区域 x1,y1,x2,y2")
        command = [
            PYTHON,
            "scripts/remove_qr_area.py",
            str(source),
            "--output-dir",
            str(output_dir),
            "--box",
            box,
            "--margin-ratio",
            self.qr_margin.get().strip(),
            "--inpaint-radius",
            self.qr_radius.get().strip(),
        ]
        reference_size = self.qr_reference_size.get().strip()
        if reference_size:
            command += ["--reference-size", reference_size]
        return command, output_dir, os.environ.copy()

    def _read_process_output(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            self.log_queue.put(line)
        return_code = process.wait()
        self.log_queue.put(f"\n[完成] exit={return_code}\n")
        self.process = None

    def _drain_log_queue(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.append_log(line.rstrip("\n"))
        try:
            self.after(120, self._drain_log_queue)
        except tk.TclError:
            pass

    def append_log(self, text: str) -> None:
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)

    def stop_process(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        self.append_log("[停止] 已发送 terminate")

    def open_last_output(self) -> None:
        if self.last_output_dir is None:
            messagebox.showinfo("无输出", "还没有运行过工作流。")
            return
        self.open_path(self.last_output_dir)

    def open_path(self, path: Path) -> None:
        path = path.expanduser()
        if not path.exists():
            messagebox.showwarning("路径不存在", str(path))
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])


def main() -> None:
    app = PrintWorkflowApp()
    app.mainloop()


if __name__ == "__main__":
    main()
