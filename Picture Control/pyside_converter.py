from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from np3_serializer import repair_np3_file
from photo_preset_to_nikon import (
    PICCON_RE,
    apply_preview_adjustment,
    convert_file,
    convert_np3_file_to_np2,
    export_nikon_profile_to_xmp,
    find_default_custompc_path,
    find_next_piccon_path,
    find_recipe_root,
    get_piccon_filename,
    get_default_output_format,
    get_template_path,
    map_photoshop_to_nikon,
    normalize_output_path,
    parse_np3_preview_options,
    parse_xmp_file,
    resolve_camera_folder,
    run_folder,
    sample_base_color,
    scan_recipe_files,
)

try:
    from PIL import Image
except ImportError:
    Image = None


APP_TITLE = "Nikon NP3/NCP 工具"
PREVIEW_SIZE = (520, 260)


def make_button(text: str, slot, *, primary: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.clicked.connect(slot)
    button.setProperty("primary", primary)
    button.setMinimumHeight(34)
    return button


def preview_details(options: dict, root: Path | None = None) -> str:
    source = options.get("source")
    source_text = ""
    if isinstance(source, Path):
        try:
            source_text = str(source.relative_to(root)) if root else str(source)
        except ValueError:
            source_text = str(source)

    rows = [
        ("名称", options.get("name", "")),
        ("文件", source_text),
        ("大小", f"{options.get('size', '生成预览')} bytes"),
        ("对比度", options.get("contrast", 0)),
        ("高光", options.get("highlights", 0)),
        ("阴影", options.get("shadows", 0)),
        ("白色色阶", options.get("whiteLevel", 0)),
        ("黑色色阶", options.get("blackLevel", 0)),
        ("饱和度", options.get("saturation", 0)),
        ("清晰度", options.get("clarity", 0)),
    ]
    return "\n".join(f"{label}: {value}" for label, value in rows) + "\n\n预览是本地近似模拟效果，并非 Nikon 机内渲染。"


def qimage_from_pillow(image) -> QImage:
    rgb = image.convert("RGB")
    width, height = rgb.size
    data = rgb.tobytes("raw", "RGB")
    return QImage(data, width, height, width * 3, QImage.Format.Format_RGB888).copy()


def render_preview_pixmap(
    options: dict,
    sample_path: Path | None = None,
    rotation: int = 0,
    width: int = PREVIEW_SIZE[0],
    height: int = PREVIEW_SIZE[1],
) -> QPixmap:
    if sample_path and Image is not None:
        try:
            source = Image.open(sample_path).convert("RGB")
            rotation = rotation % 360
            if rotation:
                source = source.rotate(-rotation, expand=True)
            source.thumbnail((width, height), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (width, height), (28, 28, 28))
            left = (width - source.width) // 2
            top = (height - source.height) // 2
            canvas.paste(source, (left, top))

            pixels = canvas.load()
            for y in range(height):
                for x in range(width):
                    pixels[x, y] = apply_preview_adjustment(pixels[x, y], options, x, y, width, height)
            return QPixmap.fromImage(qimage_from_pillow(canvas))
        except Exception:
            pass

    image = QImage(width, height, QImage.Format.Format_RGB32)
    for y in range(height):
        for x in range(width):
            color = apply_preview_adjustment(sample_base_color(x, y, width, height), options, x, y, width, height)
            image.setPixelColor(x, y, QColor(color[0], color[1], color[2]))
    return QPixmap.fromImage(image)


class PreviewPanel(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("previewPanel")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self.image_label = QLabel()
        self.image_label.setObjectName("previewImage")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(PREVIEW_SIZE[0], PREVIEW_SIZE[1])
        self.image_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.details_label = QLabel("请选择文件或预设进行预览。")
        self.details_label.setObjectName("detailsLabel")
        self.details_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        title = QLabel("预览")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        layout.addWidget(self.image_label)
        layout.addWidget(self.details_label, 1)

    def show_options(self, options: dict, sample_path: Path | None, rotation: int, root: Path | None = None):
        self.image_label.setPixmap(render_preview_pixmap(options, sample_path, rotation))
        self.details_label.setText(preview_details(options, root))


class PreviewDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        title: str,
        options: dict,
        sample_path: Path | None,
        rotation: int,
        root: Path | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(590, 520)

        panel = PreviewPanel()
        panel.show_options(options, sample_path, rotation, root)

        close_button = make_button("关闭", self.accept)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(panel, 1)
        layout.addLayout(button_row)


class PresetBrowserDialog(QDialog):
    def __init__(self, parent: "NikonPySideWindow", root: Path, recipe_files: list[Path]):
        super().__init__(parent)
        self.parent_window = parent
        self.root = root
        self.recipe_files = recipe_files
        self.selected_path: Path | None = recipe_files[0] if recipe_files else None

        self.setWindowTitle("预制预设")
        self.resize(980, 620)
        self.setMinimumSize(820, 520)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("presetList")
        for path in recipe_files:
            item = QListWidgetItem(str(path.relative_to(root)))
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.list_widget.addItem(item)
        self.list_widget.currentItemChanged.connect(self.update_preview)

        self.preview_panel = PreviewPanel()

        save_button = make_button("保存到 SD 卡", self.save_selected_to_card, primary=True)
        export_button = make_button("修复/导出...", self.export_selected)
        sample_button = make_button("更换预览图片", self.change_preview_image)
        rotate_left = make_button("向左旋转", lambda: self.rotate_preview(-90))
        rotate_right = make_button("向右旋转", lambda: self.rotate_preview(90))

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        found_label = QLabel(f"在 {root} 中找到 {len(recipe_files)} 个预设")
        found_label.setWordWrap(True)
        left_layout.addWidget(found_label)
        left_layout.addWidget(self.list_widget, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.preview_panel, 1)
        action_grid = QGridLayout()
        action_grid.addWidget(save_button, 0, 0)
        action_grid.addWidget(export_button, 0, 1)
        action_grid.addWidget(sample_button, 1, 0, 1, 2)
        action_grid.addWidget(rotate_left, 2, 0)
        action_grid.addWidget(rotate_right, 2, 1)
        right_layout.addLayout(action_grid)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter, 1)

        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    def update_preview(self, current: QListWidgetItem | None):
        if current is None:
            return
        self.selected_path = current.data(Qt.ItemDataRole.UserRole)
        options = parse_np3_preview_options(self.selected_path)
        self.preview_panel.show_options(
            options,
            self.parent_window.sample_image_path,
            self.parent_window.sample_rotation,
            self.root,
        )

    def save_selected_to_card(self):
        if self.selected_path:
            self.parent_window.save_np3_source_to_camera(self.selected_path)

    def export_selected(self):
        if self.selected_path:
            self.parent_window.export_repaired_np3(self.selected_path)

    def change_preview_image(self):
        self.parent_window.select_sample_image()
        self.refresh_current()

    def rotate_preview(self, degrees: int):
        self.parent_window.rotate_sample_image(degrees)
        self.refresh_current()

    def refresh_current(self):
        self.update_preview(self.list_widget.currentItem())


class NikonPySideWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.input_file: Path | None = None
        self.output_file: Path | None = None
        self.input_folder: Path | None = None
        self.output_folder: Path | None = None
        self.sample_image_path: Path | None = None
        self.sample_rotation = 0
        self.current_preview_options: dict | None = None
        self.current_preview_root: Path | None = None

        self.setWindowTitle(APP_TITLE)
        self.resize(1120, 720)
        self.setMinimumSize(960, 620)

        self.status = QStatusBar()
        self.setStatusBar(self.status)

        self.preview_panel = PreviewPanel()
        self.setup_ui()
        self.apply_style()
        self.update_status("就绪")

    def setup_ui(self):
        self.source_label = QLabel("未选择")
        self.source_label.setObjectName("pathLabel")
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.sample_label = QLabel("使用生成的示例场景")
        self.sample_label.setObjectName("pathLabel")
        self.sample_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.rotation_label = QLabel("旋转：0 度")
        self.output_name = QLineEdit(get_piccon_filename(1, get_default_output_format()))

        self.format_combo = QComboBox()
        self.format_combo.addItems(["NP3", "NCP"])
        self.format_combo.setCurrentText(get_default_output_format().upper())
        self.format_combo.currentTextChanged.connect(self.refresh_piccon_name)

        source_group = QGroupBox("单个 XMP")
        source_layout = QGridLayout(source_group)
        source_layout.addWidget(QLabel("输入"), 0, 0)
        source_layout.addWidget(self.source_label, 0, 1, 1, 2)
        source_layout.addWidget(make_button("选择 XMP", self.select_input_file), 1, 0)
        source_layout.addWidget(make_button("预览 XMP", self.preview_xmp), 1, 1)
        source_layout.addWidget(make_button("导出 XMP 文件...", self.convert_single_file, primary=True), 1, 2)
        source_layout.addWidget(make_button("保存 XMP 到 SD 卡", self.save_as_camera_file), 2, 0, 1, 3)

        camera_group = QGroupBox("相机输出")
        camera_layout = QGridLayout(camera_group)
        camera_layout.addWidget(QLabel("格式"), 0, 0)
        camera_layout.addWidget(self.format_combo, 0, 1)
        camera_layout.addWidget(QLabel("下一个文件"), 1, 0)
        camera_layout.addWidget(self.output_name, 1, 1)

        preview_source_group = QGroupBox("预览图片")
        preview_source_layout = QGridLayout(preview_source_group)
        preview_source_layout.addWidget(self.sample_label, 0, 0, 1, 3)
        preview_source_layout.addWidget(self.rotation_label, 1, 0, 1, 3)
        preview_source_layout.addWidget(make_button("选择 JPG/PNG", self.select_sample_image), 2, 0)
        preview_source_layout.addWidget(make_button("使用生成图", self.clear_sample_image), 2, 1)
        preview_source_layout.addWidget(make_button("向左旋转", lambda: self.rotate_sample_image(-90)), 3, 0)
        preview_source_layout.addWidget(make_button("向右旋转", lambda: self.rotate_sample_image(90)), 3, 1)

        tools_group = QGroupBox("工具")
        tools_layout = QGridLayout(tools_group)
        tools_layout.addWidget(make_button("预制预设", self.open_preset_browser, primary=True), 0, 0)
        tools_layout.addWidget(make_button("修复/导出", self.repair_profile_file), 0, 1)
        tools_layout.addWidget(make_button("修复到 SD 卡", self.repair_profile_to_camera), 1, 0)
        tools_layout.addWidget(make_button("NP3/NCP 转 XMP", self.export_profile_to_xmp), 1, 1)
        tools_layout.addWidget(make_button("NP3 转 NP2", self.convert_np3_to_np2), 2, 0, 1, 2)

        folder_group = QGroupBox("文件夹批量转换")
        self.input_folder_label = QLabel("输入文件夹：未选择")
        self.input_folder_label.setObjectName("pathLabel")
        self.output_folder_label = QLabel("输出文件夹：未选择")
        self.output_folder_label.setObjectName("pathLabel")
        folder_layout = QGridLayout(folder_group)
        folder_layout.addWidget(self.input_folder_label, 0, 0, 1, 3)
        folder_layout.addWidget(self.output_folder_label, 1, 0, 1, 3)
        folder_layout.addWidget(make_button("选择输入文件夹", self.select_input_folder), 2, 0)
        folder_layout.addWidget(make_button("选择输出文件夹", self.select_output_folder), 2, 1)
        folder_layout.addWidget(make_button("转换文件夹", self.convert_folder, primary=True), 2, 2)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(14)
        header = QLabel("Nikon Picture Control 转换工具")
        header.setObjectName("appHeader")
        subhead = QLabel("转换 XMP 预设，修复 NP3/NCP 文件，并生成相机可导入的 PICCON 文件。")
        subhead.setObjectName("subHeader")
        subhead.setWordWrap(True)
        controls_layout.addWidget(header)
        controls_layout.addWidget(subhead)
        controls_layout.addWidget(source_group)
        controls_layout.addWidget(camera_group)
        controls_layout.addWidget(preview_source_group)
        controls_layout.addWidget(tools_group)
        controls_layout.addWidget(folder_group)
        controls_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(controls)

        splitter = QSplitter()
        splitter.addWidget(scroll)
        splitter.addWidget(self.preview_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(container)

    def apply_style(self):
        self.setStyleSheet(
            """
            QMainWindow, QDialog {
                background: #f4f6f7;
                color: #1d252c;
                font-family: Segoe UI, Arial, sans-serif;
                font-size: 10pt;
            }
            QGroupBox {
                background: #ffffff;
                border: 1px solid #d8dee4;
                border-radius: 8px;
                margin-top: 16px;
                padding: 12px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                color: #26313b;
            }
            QPushButton, QToolButton {
                background: #eef2f5;
                border: 1px solid #cbd4dd;
                border-radius: 6px;
                padding: 7px 12px;
                color: #1d252c;
            }
            QPushButton:hover, QToolButton:hover {
                background: #e2e9ef;
            }
            QPushButton[primary="true"] {
                background: #176b63;
                border-color: #176b63;
                color: #ffffff;
                font-weight: 600;
            }
            QPushButton[primary="true"]:hover {
                background: #125a54;
            }
            QLineEdit, QComboBox, QListWidget {
                background: #ffffff;
                border: 1px solid #cbd4dd;
                border-radius: 6px;
                padding: 6px;
            }
            QListWidget::item {
                padding: 7px;
            }
            QListWidget::item:selected {
                background: #dcefeb;
                color: #102a28;
            }
            #appHeader {
                font-size: 20pt;
                font-weight: 700;
                color: #18232d;
            }
            #subHeader {
                color: #52616f;
                font-size: 10pt;
            }
            #pathLabel {
                color: #52616f;
                padding: 4px 0;
            }
            #previewPanel {
                background: #ffffff;
                border: 1px solid #d8dee4;
                border-radius: 8px;
            }
            #sectionTitle {
                font-size: 15pt;
                font-weight: 700;
                color: #18232d;
            }
            #previewImage {
                background: #1d252c;
                border-radius: 6px;
                border: 1px solid #151b20;
            }
            #detailsLabel {
                background: #f8fafb;
                border: 1px solid #e2e7ec;
                border-radius: 6px;
                padding: 10px;
                color: #2f3d49;
            }
            QStatusBar {
                background: #ffffff;
                border-top: 1px solid #d8dee4;
            }
            """
        )

    def output_format(self) -> str:
        return self.format_combo.currentText().lower()

    def update_status(self, message: str):
        self.status.showMessage(message)

    def refresh_piccon_name(self):
        current_name = self.output_name.text()
        match = PICCON_RE.match(current_name)
        number = int(match.group(1)) if match else 1
        self.output_name.setText(get_piccon_filename(number, self.output_format()))

    def refresh_preview_panel(self):
        if self.current_preview_options is not None:
            self.preview_panel.show_options(
                self.current_preview_options,
                self.sample_image_path,
                self.sample_rotation,
                self.current_preview_root,
            )

    def select_input_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 Photoshop XMP 文件", "", "XMP 文件 (*.xmp)")
        if path:
            self.input_file = Path(path)
            self.source_label.setText(str(self.input_file))
            self.update_status(f"已选择输入文件：{self.input_file.name}")
            self.preview_xmp(show_dialog=False)

    def select_sample_image(self):
        if Image is None:
            QMessageBox.critical(self, "缺少依赖", "JPG/PNG 预览需要 Pillow。请运行：python -m pip install Pillow")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择预览图片",
            "",
            "图片文件 (*.jpg *.jpeg *.png *.bmp *.tif *.tiff);;所有文件 (*.*)",
        )
        if path:
            self.sample_image_path = Path(path)
            self.sample_rotation = 0
            self.sample_label.setText(f"预览图片：{self.sample_image_path}")
            self.rotation_label.setText("旋转：0 度")
            self.refresh_preview_panel()
            self.update_status(f"正在使用预览图片：{self.sample_image_path.name}")

    def clear_sample_image(self):
        self.sample_image_path = None
        self.sample_rotation = 0
        self.sample_label.setText("使用生成的示例场景")
        self.rotation_label.setText("旋转：0 度")
        self.refresh_preview_panel()
        self.update_status("正在使用生成的预览场景")

    def rotate_sample_image(self, degrees: int):
        self.sample_rotation = (self.sample_rotation + degrees) % 360
        self.rotation_label.setText(f"旋转：{self.sample_rotation} 度")
        self.refresh_preview_panel()
        self.update_status(f"预览旋转已设为 {self.sample_rotation} 度")

    def choose_camera_folder(self) -> Path | None:
        default_custompc = find_default_custompc_path()
        if default_custompc is not None:
            self.update_status(f"正在使用相机文件夹：{default_custompc}")
            return default_custompc
        folder = QFileDialog.getExistingDirectory(self, "选择 SD 卡根目录或 NIKON/CUSTOMPC 文件夹")
        if not folder:
            return None
        return resolve_camera_folder(Path(folder))

    def select_output_file(self):
        default_name = self.output_name.text() or f"PICCON01.{self.output_format()}"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "选择输出文件",
            default_name,
            f"Nikon 配置文件 (*.{self.output_format()});;所有文件 (*.*)",
        )
        if path:
            self.output_file = Path(path)
            self.output_name.setText(self.output_file.name)
            self.update_status(f"已选择输出文件：{self.output_file.name}")

    def convert_single_file(self):
        if not self.input_file:
            self.select_input_file()
            if not self.input_file:
                return
        if not self.output_file:
            self.select_output_file()
            if not self.output_file:
                return

        try:
            self.output_file = normalize_output_path(self.output_file, self.output_format())
            convert_file(self.input_file, self.output_file)
            self.output_name.setText(self.output_file.name)
            QMessageBox.information(self, "完成", f"文件转换成功：\n{self.output_file}")
            self.update_status(f"已将 {self.input_file.name} 转换为 {self.output_file.name}")
        except Exception as exc:
            QMessageBox.critical(self, "错误", f"转换失败：{exc}")
            self.update_status("转换失败")

    def preview_xmp(self, show_dialog: bool = True):
        if not self.input_file:
            self.select_input_file()
            if not self.input_file:
                return

        try:
            settings = parse_xmp_file(self.input_file)
            options = map_photoshop_to_nikon(settings, self.input_file)
            options["source"] = self.input_file
            options["size"] = "生成预览"
            self.current_preview_options = options
            self.current_preview_root = None
            self.refresh_preview_panel()
            if show_dialog:
                PreviewDialog(self, "转换预览", options, self.sample_image_path, self.sample_rotation).exec()
            self.update_status(f"正在预览 {self.input_file.name}")
        except Exception as exc:
            QMessageBox.critical(self, "错误", f"预览生成失败：{exc}")
            self.update_status("预览生成失败")

    def save_as_camera_file(self):
        if not self.input_file:
            self.select_input_file()
            if not self.input_file:
                return
        camera_folder = self.choose_camera_folder()
        if camera_folder is None:
            return

        try:
            camera_folder.mkdir(parents=True, exist_ok=True)
            camera_file = find_next_piccon_path(camera_folder, self.output_format())
            self.output_name.setText(camera_file.name)
            convert_file(self.input_file, camera_file)
            QMessageBox.information(self, "完成", f"已保存相机导入文件：\n{camera_file}")
            self.update_status(f"已将 {camera_file.name} 保存到 {camera_folder}")
        except Exception as exc:
            QMessageBox.critical(self, "错误", f"保存失败：{exc}")
            self.update_status("保存失败")

    def save_np3_source_to_camera(self, source_path: Path):
        camera_folder = self.choose_camera_folder()
        if camera_folder is None:
            return

        try:
            camera_folder.mkdir(parents=True, exist_ok=True)
            camera_file = find_next_piccon_path(camera_folder, self.output_format())
            repair_np3_file(source_path, camera_file, template_path=get_template_path(self.output_format()))
            self.output_name.setText(camera_file.name)
            QMessageBox.information(self, "完成", f"已保存修复后的预设：\n{camera_file}")
            self.update_status(f"已将 {source_path.name} 保存为 {camera_file.name}")
        except Exception as exc:
            QMessageBox.critical(self, "错误", f"预设保存失败：{exc}")
            self.update_status("预设保存失败")

    def export_repaired_np3(self, source_path: Path):
        default_name = f"repaired_{source_path.stem}.{self.output_format()}"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出修复后的预设",
            default_name,
            f"Nikon 配置文件 (*.{self.output_format()});;所有文件 (*.*)",
        )
        if not path:
            return

        try:
            output_path = normalize_output_path(Path(path), self.output_format())
            repair_np3_file(source_path, output_path, template_path=get_template_path(self.output_format()))
            QMessageBox.information(self, "完成", f"已导出修复后的预设：\n{output_path}")
            self.update_status(f"已导出修复后的预设：{output_path.name}")
        except Exception as exc:
            QMessageBox.critical(self, "错误", f"预设导出失败：{exc}")
            self.update_status("预设导出失败")

    def repair_profile_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择要修复的 NP3/NCP 文件", "", "Nikon 配置文件 (*.np3 *.ncp)")
        if not path:
            return
        input_file = Path(path)

        try:
            options = parse_np3_preview_options(input_file)
            self.current_preview_options = options
            self.current_preview_root = None
            self.refresh_preview_panel()
        except Exception:
            pass

        default_name = f"repaired_{input_file.name}"
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存修复后的配置文件",
            default_name,
            f"Nikon 配置文件 (*{input_file.suffix});;所有文件 (*.*)",
        )
        if not save_path:
            return

        try:
            repair_np3_file(input_file, Path(save_path), template_path=get_template_path(input_file.suffix.lstrip(".")))
            QMessageBox.information(self, "完成", f"已保存修复后的配置文件：\n{save_path}")
            self.update_status(f"已修复 {input_file.name} -> {Path(save_path).name}")
        except Exception as exc:
            QMessageBox.critical(self, "错误", f"修复失败：{exc}")
            self.update_status("修复失败")

    def repair_profile_to_camera(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择要修复到 SD 卡的 NP3/NCP 文件", "", "Nikon 配置文件 (*.np3 *.ncp)")
        if not path:
            return
        input_file = Path(path)

        try:
            options = parse_np3_preview_options(input_file)
            self.current_preview_options = options
            self.current_preview_root = None
            self.refresh_preview_panel()
        except Exception:
            pass

        self.save_np3_source_to_camera(input_file)

    def export_profile_to_xmp(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择要导出为 XMP 的 NP3/NCP 文件", "", "Nikon 配置文件 (*.np3 *.ncp)")
        if not path:
            return
        input_file = Path(path)

        try:
            options = parse_np3_preview_options(input_file)
            self.current_preview_options = options
            self.current_preview_root = None
            self.refresh_preview_panel()
        except Exception:
            pass

        default_name = f"{input_file.stem}_approx.xmp"
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存近似 XMP 预设",
            default_name,
            "XMP 文件 (*.xmp);;所有文件 (*.*)",
        )
        if not save_path:
            return

        try:
            output_path = export_nikon_profile_to_xmp(input_file, Path(save_path))
            QMessageBox.information(self, "完成", f"已导出近似 XMP 预设：\n{output_path}")
            self.update_status(f"已将 {input_file.name} 导出为近似 XMP")
        except Exception as exc:
            QMessageBox.critical(self, "错误", f"导出近似 XMP 失败：{exc}")
            self.update_status("导出近似 XMP 失败")

    def convert_np3_to_np2(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择要转换为 NP2 的 NP3 文件", "", "Nikon NP3 文件 (*.np3)")
        if not path:
            return
        input_file = Path(path)

        default_name = f"{input_file.stem}.NP2"
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存 NP2 文件",
            default_name,
            "Nikon NP2 文件 (*.np2);;所有文件 (*.*)",
        )
        if not save_path:
            return

        try:
            output_path = convert_np3_file_to_np2(input_file, Path(save_path))
            QMessageBox.information(self, "完成", f"已转换为 NP2：\n{output_path}")
            self.update_status(f"已将 {input_file.name} 转换为 NP2")
        except Exception as exc:
            QMessageBox.critical(self, "错误", f"NP3 转 NP2 失败：{exc}")
            self.update_status("NP3 转 NP2 失败")

    def open_preset_browser(self):
        root = find_recipe_root()
        if root is None:
            selected = QFileDialog.getExistingDirectory(self, "选择 Nikon Recipes 文件夹")
            if not selected:
                return
            root = Path(selected)

        recipe_files = scan_recipe_files(root)
        if not recipe_files:
            QMessageBox.warning(self, "未找到预设", "这个文件夹里没有找到 .NP3 文件。")
            return

        dialog = PresetBrowserDialog(self, root, recipe_files)
        dialog.exec()

    def select_input_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择包含 XMP 文件的文件夹")
        if path:
            self.input_folder = Path(path)
            self.input_folder_label.setText(f"输入文件夹：{self.input_folder}")
            self.update_status(f"已选择输入文件夹：{self.input_folder}")

    def select_output_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
        if path:
            self.output_folder = Path(path)
            self.output_folder_label.setText(f"输出文件夹：{self.output_folder}")
            self.update_status(f"已选择输出文件夹：{self.output_folder}")

    def convert_folder(self):
        if not self.input_folder or not self.output_folder:
            QMessageBox.warning(self, "缺少选择", "请同时选择输入文件夹和输出文件夹。")
            return

        try:
            run_folder(self.input_folder, self.output_folder, f".{self.output_format()}")
            QMessageBox.information(self, "完成", f"文件夹转换成功。\n输出文件夹：{self.output_folder}")
            self.update_status(f"已将文件夹 {self.input_folder} 转换到 {self.output_folder}")
        except Exception as exc:
            QMessageBox.critical(self, "错误", f"文件夹转换失败：{exc}")
            self.update_status("文件夹转换失败")


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    window = NikonPySideWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
