import argparse
import colorsys
import json
import os
import re
import sys
import uuid
import xml.etree.ElementTree as ET
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from np3_serializer import serialize_np3, repair_np3_file

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

PROJECT_DIR = Path(__file__).resolve().parent
RECIPE_ROOT_ENV = 'NIKON_RECIPE_ROOT'
CUSTOMPC_ENV = 'NIKON_CUSTOMPC_PATH'
LOCAL_RECIPE_DIRS = (
    PROJECT_DIR / 'recipes',
    PROJECT_DIR / 'Nikon-Recipes-main',
    PROJECT_DIR.parent / 'Nikon-Recipes-main',
)
TEMPLATE_PATHS = {
    'np3': PROJECT_DIR / 'PICCON01.NP3',
    'ncp': PROJECT_DIR / 'PICCON01.NCP',
}
PICCON_RE = re.compile(r'^PICCON([0-9]{2})\.(?:NP3|NCP)$', re.IGNORECASE)
MAX_PICCON_NUMBER = 99
PREVIEW_WIDTH = 360
PREVIEW_HEIGHT = 180
NP3_EXTRA_RECORD_START = 206
NP3_EXTRA_RECORD_LENGTH = 40
NP3_TO_NP2_VERSION = b'0210'

XMP_TO_KEY = {
    'Exposure2012': 'exposure',
    'Contrast2012': 'contrast',
    'Highlights2012': 'highlights',
    'Shadows2012': 'shadows',
    'Whites2012': 'whites',
    'Blacks2012': 'blacks',
    'Clarity2012': 'clarity',
    'Vibrance': 'vibrance',
    'Saturation': 'saturation',
    'Temperature': 'temperature',
    'Tint': 'tint',
    'SharpenRadius': 'sharpen_radius',
    'SharpenDetail': 'sharpen_detail',
    'SharpenProtect': 'sharpen_protect',
}


def parse_xmp_file(path: Path) -> dict:
    tree = ET.parse(path)
    root = tree.getroot()
    values = {}

    for description in root.findall('.//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description'):
        for attr_name, attr_value in description.attrib.items():
            if '}' in attr_name:
                local_name = attr_name.split('}', 1)[1]
            else:
                local_name = attr_name

            if local_name in XMP_TO_KEY:
                values[XMP_TO_KEY[local_name]] = try_parse_number(attr_value)
            else:
                values[local_name] = attr_value

    return values


def try_parse_number(value):
    try:
        if '.' in value:
            return float(value)
        return int(value)
    except (ValueError, TypeError):
        return value


def map_photoshop_to_nikon(settings: dict, source_path: Path | None = None) -> dict:
    name = settings.get('crs:PresetName') or settings.get('PresetName') or (source_path.stem if source_path else 'Converted')
    options = {
        'name': str(name)[:19],
        'comment': '',
        'sharpning': 3.0,
        'midRangeSharpning': 2.0,
        'clarity': 1.0,
        'contrast': 75,
        'highlights': None,
        'shadows': None,
        'whiteLevel': None,
        'blackLevel': None,
        'saturation': None,
        'colorBlender': None,
        'colorGrading': None,
    }

    def clamp_value(value, scale=1, minimum=-100, maximum=100):
        try:
            return max(minimum, min(maximum, int(round(float(value) * scale))))
        except (ValueError, TypeError):
            return 0

    def clamp_level(value, scale=0.2, minimum=-20, maximum=20):
        return clamp_value(value, scale=scale, minimum=minimum, maximum=maximum)

    exposure_offset = 0
    if 'exposure' in settings:
        exposure_offset = clamp_level(settings['exposure'], scale=4)

    if 'contrast' in settings:
        options['contrast'] = clamp_value(settings['contrast'], scale=0.6) + 75
    if 'highlights' in settings:
        options['highlights'] = clamp_value(settings['highlights'], scale=0.5)
    if 'shadows' in settings:
        options['shadows'] = clamp_value(settings['shadows'], scale=0.5)

    white_level = None
    black_level = None
    if 'whites' in settings:
        white_level = clamp_level(settings['whites'], scale=0.25)
    if 'blacks' in settings:
        black_level = clamp_level(settings['blacks'], scale=0.25)

    if exposure_offset:
        white_level = clamp_level((white_level or 0) + exposure_offset, scale=1)
        black_level = clamp_level((black_level or 0) + exposure_offset, scale=1)

    if white_level is not None:
        options['whiteLevel'] = white_level
    if black_level is not None:
        options['blackLevel'] = black_level

    if 'saturation' in settings:
        options['saturation'] = clamp_value(settings['saturation'], scale=0.5)
    elif 'vibrance' in settings:
        options['saturation'] = clamp_value(settings['vibrance'], scale=0.5)

    if 'clarity' in settings:
        options['clarity'] = 1.0 + max(-5, min(5, float(settings['clarity']) / 20))
    if 'temperature' in settings or 'tint' in settings:
        temp = settings.get('temperature')
        tint = settings.get('tint')
        comment_parts = []
        if temp is not None:
            comment_parts.append(f'Temperature={temp}')
        if tint is not None:
            comment_parts.append(f'Tint={tint}')
        if comment_parts:
            options['comment'] += ' | ' + ', '.join(comment_parts)

    return options


def normalize_output_path(output_path: Path, format: str) -> Path:
    suffix = f'.{format.upper()}'
    if output_path.suffix.lower() not in ('.np3', '.ncp'):
        return output_path.with_suffix(suffix)
    return output_path.with_suffix(suffix)


def get_template_path(format: str | None = None) -> Path | None:
    if format:
        template_path = TEMPLATE_PATHS.get(format.lower())
        if template_path and template_path.exists():
            return template_path

    for template_path in TEMPLATE_PATHS.values():
        if template_path.exists():
            return template_path

    return None


def get_default_output_format() -> str:
    if TEMPLATE_PATHS['np3'].exists():
        return 'np3'
    if TEMPLATE_PATHS['ncp'].exists():
        return 'ncp'
    return 'np3'


def get_piccon_filename(number: int, format: str) -> str:
    return f'PICCON{number:02d}.{format.upper()}'


def find_next_piccon_number(camera_folder: Path) -> int:
    used_numbers = set()
    if camera_folder.exists():
        for file_path in camera_folder.iterdir():
            match = PICCON_RE.match(file_path.name)
            if match:
                used_numbers.add(int(match.group(1)))

    for number in range(1, MAX_PICCON_NUMBER + 1):
        if number not in used_numbers:
            return number

    raise RuntimeError('No PICCON slots are available. The camera folder already has PICCON01 through PICCON99.')


def find_next_piccon_path(camera_folder: Path, format: str) -> Path:
    return camera_folder / get_piccon_filename(find_next_piccon_number(camera_folder), format)


def resolve_camera_folder(selected_path: Path) -> Path:
    if selected_path.name.upper() == 'CUSTOMPC':
        return selected_path

    nikon_custompc = selected_path / 'NIKON' / 'CUSTOMPC'
    if (selected_path / 'NIKON').exists() or nikon_custompc.exists():
        return nikon_custompc

    return selected_path / 'NIKON' / 'CUSTOMPC'


def find_default_custompc_path() -> Path | None:
    configured = os.environ.get(CUSTOMPC_ENV)
    if configured:
        path = Path(configured)
        if path.exists():
            return path

    for letter in 'DEFGHIJKLMNOPQRSTUVWXYZ':
        path = Path(f'{letter}:/NIKON/CUSTOMPC')
        if path.exists():
            return path

    return None


def find_recipe_root() -> Path | None:
    candidates = []
    configured = os.environ.get(RECIPE_ROOT_ENV)
    if configured:
        candidates.append(Path(configured))
    candidates.extend(LOCAL_RECIPE_DIRS)

    for candidate in candidates:
        if candidate.exists() and any(candidate.rglob('*.NP3')):
            return candidate
    return None


def read_ascii_field(data: bytes, offset: int, length: int) -> str:
    if offset >= len(data):
        return ''
    raw = data[offset:offset + length].split(b'\x00', 1)[0]
    return raw.decode('ascii', errors='ignore').strip()


def decode_centered_byte(data: bytes, offset: int, default: int = 0) -> int:
    if offset >= len(data):
        return default
    value = data[offset]
    if value in (0x00, 0xff):
        return default
    return max(-100, min(100, value - 0x80))


def parse_np3_preview_options(path: Path) -> dict:
    data = path.read_bytes()
    name = read_ascii_field(data, 24, 19) or path.stem
    options = {
        'name': name,
        'source': path,
        'size': len(data),
        'sharpning': max(-3, min(9, decode_centered_byte(data, 82) / 4)),
        'midRangeSharpning': max(-5, min(5, decode_centered_byte(data, 242) / 4)),
        'clarity': max(-5, min(5, decode_centered_byte(data, 92) / 4)),
        'contrast': decode_centered_byte(data, 272),
        'highlights': decode_centered_byte(data, 282),
        'shadows': decode_centered_byte(data, 292),
        'whiteLevel': decode_centered_byte(data, 302),
        'blackLevel': decode_centered_byte(data, 312),
        'saturation': decode_centered_byte(data, 322),
    }
    return options


def scan_recipe_files(root: Path) -> list[Path]:
    return sorted(root.rglob('*.NP3'), key=lambda path: (str(path.parent).lower(), path.name.lower()))


def apply_preview_adjustment(rgb: tuple[int, int, int], options: dict, x: int, y: int, width: int, height: int) -> tuple[int, int, int]:
    r, g, b = [channel / 255.0 for channel in rgb]
    h, l, s = colorsys.rgb_to_hls(r, g, b)

    contrast = (options.get('contrast') or 0) / 100.0
    highlights = (options.get('highlights') or 0) / 100.0
    shadows = (options.get('shadows') or 0) / 100.0
    white_level = (options.get('whiteLevel') or 0) / 100.0
    black_level = (options.get('blackLevel') or 0) / 100.0
    saturation = (options.get('saturation') or 0) / 100.0
    clarity = (options.get('clarity') or 0) / 5.0

    l = l * (1.0 + white_level * 0.18) + black_level * 0.10
    l = ((l - 0.5) * (1.0 + contrast * 0.75)) + 0.5
    if l > 0.55:
        l += (1.0 - l) * highlights * 0.22
    else:
        l -= l * shadows * 0.22

    texture = (((x * 17 + y * 31) % 19) - 9) / 255.0
    l += texture * max(0.0, clarity) * 1.4
    l = max(0.0, min(1.0, l))

    s = max(0.0, min(1.0, s * (1.0 + saturation * 0.85)))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return (int(r * 255), int(g * 255), int(b * 255))


def sample_base_color(x: int, y: int, width: int, height: int) -> tuple[int, int, int]:
    nx = x / max(1, width - 1)
    ny = y / max(1, height - 1)
    if y < height * 0.42:
        return (int(70 + nx * 90), int(110 + ny * 90), int(155 + ny * 70))
    if x < width * 0.35:
        shade = int(40 + ny * 80)
        return (shade, int(shade * 1.08), int(shade * 0.95))
    if x > width * 0.68 and y > height * 0.50:
        return (int(155 + nx * 45), int(95 + ny * 55), int(70 + ny * 35))
    if y > height * 0.66:
        return (int(55 + nx * 70), int(95 + ny * 65), int(55 + nx * 25))
    return (int(125 + nx * 65), int(118 + ny * 55), int(105 + nx * 35))


def create_preview_image(
    options: dict,
    width: int = PREVIEW_WIDTH,
    height: int = PREVIEW_HEIGHT,
    sample_path: Path | None = None,
    rotation: int = 0,
):
    if sample_path is not None and Image is not None and ImageTk is not None:
        try:
            return create_preview_from_file(sample_path, options, width, height, rotation)
        except Exception:
            pass

    image = tk.PhotoImage(width=width, height=height)
    for y in range(height):
        row = []
        for x in range(width):
            color = apply_preview_adjustment(sample_base_color(x, y, width, height), options, x, y, width, height)
            row.append(f'#{color[0]:02x}{color[1]:02x}{color[2]:02x}')
        image.put('{' + ' '.join(row) + '}', to=(0, y))
    return image


def create_preview_from_file(sample_path: Path, options: dict, width: int, height: int, rotation: int = 0):
    source = Image.open(sample_path).convert('RGB')
    rotation = rotation % 360
    if rotation:
        source = source.rotate(-rotation, expand=True)
    source.thumbnail((width, height), Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', (width, height), (28, 28, 28))
    left = (width - source.width) // 2
    top = (height - source.height) // 2
    canvas.paste(source, (left, top))

    pixels = canvas.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = apply_preview_adjustment(pixels[x, y], options, x, y, width, height)

    return ImageTk.PhotoImage(canvas)


def write_nikon_profile(profile: dict, output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() in ('.np3', '.ncp'):
        output_format = output_path.suffix.lower().lstrip('.')
        data = serialize_np3(profile, template_path=get_template_path(output_format))
        with output_path.open('wb') as f:
            f.write(data)
        print(f'Wrote Nikon {output_format.upper()} binary profile: {output_path}')
    else:
        with output_path.open('w', encoding='utf-8') as f:
            json.dump(profile, f, indent=2)
        print(f'Wrote Nikon placeholder profile: {output_path}')


def convert_file(input_path: Path, output_path: Path):
    print(f'Converting {input_path} -> {output_path}')
    settings = parse_xmp_file(input_path)
    nikon_profile = map_photoshop_to_nikon(settings, input_path)
    write_nikon_profile(nikon_profile, output_path)


def run_folder(input_folder: Path, output_folder: Path, extension: str):
    for source_file in sorted(input_folder.rglob('*.xmp')):
        dest_file = output_folder / source_file.with_suffix(extension).name
        convert_file(source_file, dest_file)


def normalize_np2_output_path(output_path: Path) -> Path:
    return output_path if output_path.suffix.lower() == '.np2' else output_path.with_suffix('.NP2')


def get_picture_control_version(data: bytes) -> str:
    if not data.startswith(b'NCP\x00'):
        return ''
    if len(data) >= 16 and data[11] == 0x04:
        return data[12:16].decode('ascii', errors='ignore')
    if len(data) >= 16 and data[11] == 0x24:
        return data[12:16].decode('ascii', errors='ignore')
    return ''


def convert_np3_bytes_to_np2(data: bytes) -> bytes:
    version = get_picture_control_version(data)
    if not version.startswith('03'):
        raise ValueError(f'Input is not an NP3-format Picture Control file. Detected version: {version or "unknown"}')

    marker_offset = data.find(b'BI0')
    if marker_offset < NP3_EXTRA_RECORD_START + NP3_EXTRA_RECORD_LENGTH:
        raise ValueError('Input NP3 file does not have the expected flexible color record layout.')

    output = bytearray(data[:NP3_EXTRA_RECORD_START] + data[NP3_EXTRA_RECORD_START + NP3_EXTRA_RECORD_LENGTH:])
    output[12:16] = NP3_TO_NP2_VERSION
    output[NP3_EXTRA_RECORD_START - 1] = 0x01
    return bytes(output)


def convert_np3_file_to_np2(input_path: Path, output_path: Path) -> Path:
    output_path = normalize_np2_output_path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(convert_np3_bytes_to_np2(input_path.read_bytes()))
    print(f'Wrote Nikon NP2 profile: {output_path}')
    return output_path


def normalize_xmp_output_path(output_path: Path) -> Path:
    return output_path if output_path.suffix.lower() == '.xmp' else output_path.with_suffix('.xmp')


def clamp_xmp_value(value: float, minimum: int = -100, maximum: int = 100) -> int:
    return max(minimum, min(maximum, int(round(value))))


def map_nikon_to_photoshop(options: dict, source_path: Path | None = None) -> dict:
    name = str(options.get('name') or (source_path.stem if source_path else 'Converted'))[:64]
    contrast = clamp_xmp_value(((options.get('contrast') or 75) - 75) / 0.6)
    highlights = clamp_xmp_value((options.get('highlights') or 0) / 0.5)
    shadows = clamp_xmp_value((options.get('shadows') or 0) / 0.5)
    whites = clamp_xmp_value((options.get('whiteLevel') or 0) / 0.25)
    blacks = clamp_xmp_value((options.get('blackLevel') or 0) / 0.25)
    saturation = clamp_xmp_value((options.get('saturation') or 0) / 0.5)
    clarity = clamp_xmp_value((float(options.get('clarity') or 1.0) - 1.0) * 20)

    return {
        'PresetName': name or 'Converted',
        'Contrast2012': contrast,
        'Highlights2012': highlights,
        'Shadows2012': shadows,
        'Whites2012': whites,
        'Blacks2012': blacks,
        'Saturation': saturation,
        'Clarity2012': clarity,
    }


def write_xmp_preset(settings: dict, output_path: Path) -> Path:
    output_path = normalize_xmp_output_path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    x_ns = 'adobe:ns:meta/'
    rdf_ns = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
    crs_ns = 'http://ns.adobe.com/camera-raw-settings/1.0/'
    ET.register_namespace('x', x_ns)
    ET.register_namespace('rdf', rdf_ns)
    ET.register_namespace('crs', crs_ns)

    root = ET.Element(f'{{{x_ns}}}xmpmeta')
    rdf = ET.SubElement(root, f'{{{rdf_ns}}}RDF')
    description = ET.SubElement(rdf, f'{{{rdf_ns}}}Description')
    description.set(f'{{{rdf_ns}}}about', '')
    description.set(f'{{{crs_ns}}}PresetType', 'Normal')
    description.set(f'{{{crs_ns}}}UUID', str(uuid.uuid4()).upper())
    description.set(f'{{{crs_ns}}}SupportsAmount', 'False')
    description.set(f'{{{crs_ns}}}SupportsColor', 'True')
    description.set(f'{{{crs_ns}}}SupportsMonochrome', 'True')
    description.set(f'{{{crs_ns}}}SupportsHighDynamicRange', 'True')
    description.set(f'{{{crs_ns}}}SupportsNormalDynamicRange', 'True')
    description.set(f'{{{crs_ns}}}ProcessVersion', '15.4')
    description.set(f'{{{crs_ns}}}ConvertToGrayscale', 'False')

    for key, value in settings.items():
        description.set(f'{{{crs_ns}}}{key}', str(value))

    tree = ET.ElementTree(root)
    ET.indent(tree, space='  ')
    tree.write(output_path, encoding='utf-8', xml_declaration=True)
    print(f'Wrote approximate XMP preset: {output_path}')
    return output_path


def export_nikon_profile_to_xmp(input_path: Path, output_path: Path) -> Path:
    options = parse_np3_preview_options(input_path)
    settings = map_nikon_to_photoshop(options, input_path)
    return write_xmp_preset(settings, output_path)


class NikonConverterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title('Photoshop to Nikon Converter')
        self.root.geometry('760x560')
        self.root.minsize(700, 520)

        self.input_file = None
        self.output_file = None
        self.input_folder = None
        self.output_folder = None
        self.sample_image_path = None
        self.sample_rotation = 0
        self.output_format = tk.StringVar(value=get_default_output_format())
        self.output_format.trace_add('write', self.refresh_piccon_name)
        self.output_name_var = tk.StringVar(value=get_piccon_filename(1, self.output_format.get()))
        self.sample_image_var = tk.StringVar(value='Generated sample scene')
        self.sample_rotation_var = tk.StringVar(value='Rotation: 0 deg')

        self.create_widgets()

    def create_widgets(self):
        main_frame = tk.Frame(self.root, padx=14, pady=12)
        main_frame.pack(fill='both', expand=True)
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_columnconfigure(1, weight=1)

        script_path = Path(__file__).resolve()
        startup_label = 'Running packaged executable version' if getattr(sys, 'frozen', False) else 'Running current source version'
        tk.Label(
            main_frame,
            text=f'{startup_label}: {script_path}',
            fg='blue',
            font=('Segoe UI', 9, 'bold'),
            anchor='w',
            wraplength=700,
            justify='left',
        ).grid(row=0, column=0, columnspan=2, sticky='we', pady=(0, 10))

        sample_frame = tk.LabelFrame(main_frame, text='Preview Image', padx=10, pady=8)
        sample_frame.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(0, 10))
        sample_frame.grid_columnconfigure(0, weight=1)
        tk.Label(sample_frame, textvariable=self.sample_image_var, anchor='w').grid(row=0, column=0, sticky='ew', padx=(0, 8))
        tk.Button(sample_frame, text='Choose JPG/PNG', command=self.select_sample_image).grid(row=0, column=1, sticky='e')
        tk.Button(sample_frame, text='Use Generated', command=self.clear_sample_image).grid(row=0, column=2, sticky='e', padx=(8, 0))
        tk.Label(sample_frame, textvariable=self.sample_rotation_var, anchor='w').grid(row=1, column=0, sticky='w', pady=(8, 0))
        tk.Button(sample_frame, text='Rotate Left', command=lambda: self.rotate_sample_image(-90)).grid(row=1, column=1, sticky='e', pady=(8, 0))
        tk.Button(sample_frame, text='Rotate Right', command=lambda: self.rotate_sample_image(90)).grid(row=1, column=2, sticky='e', padx=(8, 0), pady=(8, 0))

        single_frame = tk.LabelFrame(main_frame, text='Single File', padx=10, pady=10)
        single_frame.grid(row=2, column=0, sticky='nsew', padx=(0, 8), pady=(0, 10))
        single_frame.grid_columnconfigure(0, weight=1)
        single_frame.grid_columnconfigure(1, weight=1)
        tk.Button(single_frame, text='Select XMP', command=self.select_input_file).grid(row=0, column=0, sticky='ew', padx=(0, 6), pady=3)
        tk.Button(single_frame, text='Preview XMP', command=self.preview_np3).grid(row=0, column=1, sticky='ew', padx=(6, 0), pady=3)
        tk.Button(single_frame, text='Save XMP to SD Card', command=self.save_as_camera_file).grid(row=1, column=0, sticky='ew', padx=(0, 6), pady=(8, 3))
        tk.Button(single_frame, text='Export XMP File...', command=self.convert_single_file).grid(row=1, column=1, sticky='ew', padx=(6, 0), pady=(8, 3))

        camera_frame = tk.LabelFrame(main_frame, text='Camera Card', padx=10, pady=10)
        camera_frame.grid(row=2, column=1, sticky='nsew', padx=(8, 0), pady=(0, 10))
        camera_frame.grid_columnconfigure(1, weight=1)
        tk.Label(camera_frame, text='Next file:').grid(row=0, column=0, sticky='w', padx=(0, 8), pady=3)
        tk.Entry(camera_frame, textvariable=self.output_name_var).grid(row=0, column=1, sticky='ew', pady=3)
        tk.Label(camera_frame, text='Auto-detects the next open PICCON slot.', anchor='w', fg='gray25').grid(row=1, column=0, columnspan=2, sticky='ew', pady=(8, 3))

        utilities_frame = tk.LabelFrame(main_frame, text='Utilities', padx=10, pady=10)
        utilities_frame.grid(row=3, column=0, sticky='nsew', padx=(0, 8), pady=(0, 10))
        utilities_frame.grid_columnconfigure(0, weight=1)
        utilities_frame.grid_columnconfigure(1, weight=1)
        utilities_frame.grid_columnconfigure(2, weight=1)
        utilities_frame.grid_columnconfigure(3, weight=1)
        tk.Button(utilities_frame, text='Preview Conversion', command=self.preview_np3).grid(row=0, column=0, sticky='ew', padx=(0, 6), pady=3)
        tk.Button(utilities_frame, text='Premade Presets', command=self.open_preset_browser).grid(row=0, column=1, sticky='ew', padx=6, pady=3)
        tk.Button(utilities_frame, text='Repair/Export', command=self.repair_profile_file).grid(row=0, column=2, sticky='ew', padx=6, pady=3)
        tk.Button(utilities_frame, text='Repair to SD', command=self.repair_profile_to_camera).grid(row=0, column=3, sticky='ew', padx=(6, 0), pady=3)
        tk.Button(utilities_frame, text='NP3/NCP 转 XMP', command=self.export_profile_to_xmp).grid(row=1, column=0, columnspan=4, sticky='ew', pady=(8, 3))
        tk.Button(utilities_frame, text='NP3 转 NP2', command=self.convert_np3_to_np2).grid(row=2, column=0, columnspan=4, sticky='ew', pady=(8, 3))

        format_frame = tk.LabelFrame(main_frame, text='Output Format', padx=10, pady=10)
        format_frame.grid(row=3, column=1, sticky='nsew', padx=(8, 0), pady=(0, 10))
        tk.Radiobutton(format_frame, text='NP3', variable=self.output_format, value='np3').pack(side='left', padx=(0, 16))
        tk.Radiobutton(format_frame, text='NCP', variable=self.output_format, value='ncp').pack(side='left')

        folder_frame = tk.LabelFrame(main_frame, text='Folder Conversion', padx=10, pady=10)
        folder_frame.grid(row=4, column=0, columnspan=2, sticky='ew', pady=(0, 10))
        folder_frame.grid_columnconfigure(0, weight=1)
        folder_frame.grid_columnconfigure(1, weight=1)
        folder_frame.grid_columnconfigure(2, weight=1)
        tk.Button(folder_frame, text='Select Input Folder', command=self.select_input_folder).grid(row=0, column=0, sticky='ew', padx=(0, 6), pady=3)
        tk.Button(folder_frame, text='Select Output Folder', command=self.select_output_folder).grid(row=0, column=1, sticky='ew', padx=6, pady=3)
        tk.Button(folder_frame, text='Convert Folder', command=self.convert_folder).grid(row=0, column=2, sticky='ew', padx=(6, 0), pady=3)

        self.status_label = tk.Label(self.root, text='Ready', anchor='w', padx=14, pady=10, wraplength=720, justify='left')
        self.status_label.pack(fill='x')

    def select_input_file(self):
        path = filedialog.askopenfilename(title='Select Photoshop XMP File', filetypes=[('XMP files', '*.xmp')])
        if path:
            self.input_file = Path(path)
            self.update_status(f'Selected input file: {self.input_file.name}')

    def select_sample_image(self):
        if Image is None or ImageTk is None:
            messagebox.showerror('Missing dependency', 'JPG previews require Pillow. Run: python -m pip install Pillow')
            return

        path = filedialog.askopenfilename(
            title='Select preview image',
            filetypes=[('Image files', '*.jpg;*.jpeg;*.png;*.bmp;*.tif;*.tiff'), ('All files', '*.*')],
        )
        if path:
            self.sample_image_path = Path(path)
            self.sample_rotation = 0
            self.update_sample_rotation_label()
            self.sample_image_var.set(f'Preview image: {self.sample_image_path.name}')
            self.update_status(f'Using preview image: {self.sample_image_path}')

    def clear_sample_image(self):
        self.sample_image_path = None
        self.sample_rotation = 0
        self.update_sample_rotation_label()
        self.sample_image_var.set('Generated sample scene')
        self.update_status('Using generated preview scene')

    def rotate_sample_image(self, degrees: int):
        self.sample_rotation = (self.sample_rotation + degrees) % 360
        self.update_sample_rotation_label()
        if self.sample_image_path is None:
            self.update_status('Preview rotation will apply after choosing an image')
        else:
            self.update_status(f'Preview rotation set to {self.sample_rotation} deg')

    def update_sample_rotation_label(self):
        self.sample_rotation_var.set(f'Rotation: {self.sample_rotation} deg')

    def refresh_piccon_name(self, *_args):
        try:
            current_name = self.output_name_var.get()
        except AttributeError:
            return

        match = PICCON_RE.match(current_name)
        number = int(match.group(1)) if match else 1
        self.output_name_var.set(get_piccon_filename(number, self.output_format.get()))

    def choose_camera_folder(self) -> Path | None:
        default_custompc = find_default_custompc_path()
        if default_custompc is not None:
            self.update_status(f'Using camera folder: {default_custompc}')
            return default_custompc

        folder = filedialog.askdirectory(title='Select SD card root or NIKON/CUSTOMPC folder')
        if not folder:
            return None
        return resolve_camera_folder(Path(folder))

    def open_preset_browser(self):
        root = find_recipe_root()
        if root is None:
            selected = filedialog.askdirectory(title='Select Nikon Recipes folder')
            if not selected:
                return
            root = Path(selected)

        recipe_files = scan_recipe_files(root)
        if not recipe_files:
            messagebox.showwarning('No presets found', 'No .NP3 files were found in that folder.')
            return

        browser = tk.Toplevel(self.root)
        browser.title('Premade Presets')
        browser.geometry('880x560')
        browser.minsize(780, 500)
        browser.grid_columnconfigure(0, weight=1)
        browser.grid_columnconfigure(1, weight=2)
        browser.grid_rowconfigure(1, weight=1)

        tk.Label(browser, text=f'{len(recipe_files)} presets found in {root}', anchor='w', wraplength=820, justify='left').grid(
            row=0, column=0, columnspan=2, sticky='we', padx=12, pady=(10, 6)
        )

        list_frame = tk.Frame(browser)
        list_frame.grid(row=1, column=0, sticky='nsew', padx=(12, 8), pady=6)
        list_frame.grid_columnconfigure(0, weight=1)
        list_frame.grid_rowconfigure(0, weight=1)
        preset_list = tk.Listbox(list_frame, exportselection=False)
        preset_list.grid(row=0, column=0, sticky='nsew')
        scrollbar = tk.Scrollbar(list_frame, orient='vertical', command=preset_list.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        preset_list.configure(yscrollcommand=scrollbar.set)

        for path in recipe_files:
            preset_list.insert(tk.END, str(path.relative_to(root)))

        preview_frame = tk.Frame(browser, padx=10, pady=10)
        preview_frame.grid(row=1, column=1, sticky='nsew', padx=(8, 12), pady=6)
        preview_frame.grid_columnconfigure(0, weight=1)

        preview_label = tk.Label(preview_frame, bd=1, relief='solid')
        preview_label.grid(row=0, column=0, sticky='n', pady=(0, 10))
        details = tk.Label(preview_frame, text='', anchor='nw', justify='left', wraplength=430)
        details.grid(row=1, column=0, sticky='new')

        button_frame = tk.Frame(preview_frame)
        button_frame.grid(row=2, column=0, sticky='ew', pady=(14, 0))
        button_frame.grid_columnconfigure(0, weight=1)
        button_frame.grid_columnconfigure(1, weight=1)

        selected_path = {'path': recipe_files[0]}

        def update_preview(_event=None):
            selection = preset_list.curselection()
            if not selection:
                return
            path = recipe_files[selection[0]]
            selected_path['path'] = path
            options = parse_np3_preview_options(path)
            image = create_preview_image(options, sample_path=self.sample_image_path, rotation=self.sample_rotation)
            preview_label.configure(image=image)
            preview_label.image = image
            details.configure(text=self.format_preview_details(options, root))

        def save_selected_to_card():
            self.save_np3_source_to_camera(selected_path['path'])

        def export_selected():
            self.export_repaired_np3(selected_path['path'])

        def choose_browser_sample():
            self.select_sample_image()
            update_preview()

        def rotate_browser_sample(degrees: int):
            self.rotate_sample_image(degrees)
            update_preview()

        tk.Button(button_frame, text='Save to SD Card', command=save_selected_to_card).grid(row=0, column=0, sticky='ew', padx=(0, 6))
        tk.Button(button_frame, text='Repair/Export...', command=export_selected).grid(row=0, column=1, sticky='ew', padx=(6, 0))
        tk.Button(button_frame, text='Change Preview Image', command=choose_browser_sample).grid(row=1, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        tk.Button(button_frame, text='Rotate Left', command=lambda: rotate_browser_sample(-90)).grid(row=2, column=0, sticky='ew', padx=(0, 6), pady=(8, 0))
        tk.Button(button_frame, text='Rotate Right', command=lambda: rotate_browser_sample(90)).grid(row=2, column=1, sticky='ew', padx=(6, 0), pady=(8, 0))

        preset_list.bind('<<ListboxSelect>>', update_preview)
        preset_list.selection_set(0)
        update_preview()

    def format_preview_details(self, options: dict, root: Path | None = None) -> str:
        source = options.get('source')
        source_text = ''
        if isinstance(source, Path):
            try:
                source_text = str(source.relative_to(root)) if root else str(source)
            except ValueError:
                source_text = str(source)

        return (
            f"Name: {options.get('name', '')}\n"
            f"File: {source_text}\n"
            f"Size: {options.get('size', 'generated')} bytes\n\n"
            f"Contrast: {options.get('contrast', 0)}\n"
            f"Highlights: {options.get('highlights', 0)}\n"
            f"Shadows: {options.get('shadows', 0)}\n"
            f"White Level: {options.get('whiteLevel', 0)}\n"
            f"Black Level: {options.get('blackLevel', 0)}\n"
            f"Saturation: {options.get('saturation', 0)}\n"
            f"Clarity: {options.get('clarity', 0)}\n\n"
            "Preview is an approximate local simulation."
        )

    def save_np3_source_to_camera(self, source_path: Path):
        camera_folder = self.choose_camera_folder()
        if camera_folder is None:
            return

        try:
            camera_folder.mkdir(parents=True, exist_ok=True)
            camera_file = find_next_piccon_path(camera_folder, self.output_format.get())
            repair_np3_file(source_path, camera_file, template_path=get_template_path(self.output_format.get()))
            self.output_name_var.set(camera_file.name)
            messagebox.showinfo('Success', f'Saved repaired preset:\n{camera_file}')
            self.update_status(f'Saved {source_path.name} as {camera_file.name}')
        except Exception as exc:
            messagebox.showerror('Error', f'Preset save failed: {exc}')
            self.update_status('Preset save failed')

    def export_repaired_np3(self, source_path: Path):
        default_name = f'repaired_{source_path.stem}.{self.output_format.get()}'
        save_path = filedialog.asksaveasfilename(
            title='Export repaired preset',
            defaultextension='.' + self.output_format.get(),
            filetypes=[('Nikon profile', f'*.{self.output_format.get()}')],
            initialfile=default_name,
        )
        if not save_path:
            return

        try:
            output_path = normalize_output_path(Path(save_path), self.output_format.get())
            repair_np3_file(source_path, output_path, template_path=get_template_path(self.output_format.get()))
            messagebox.showinfo('Success', f'Exported repaired preset:\n{output_path}')
            self.update_status(f'Exported repaired preset: {output_path.name}')
        except Exception as exc:
            messagebox.showerror('Error', f'Preset export failed: {exc}')
            self.update_status('Preset export failed')

    def show_render_preview(self, options: dict, title: str, root: Path | None = None):
        preview_window = tk.Toplevel(self.root)
        preview_window.title(title)
        preview_window.resizable(False, False)

        image_label = tk.Label(preview_window, bd=1, relief='solid')
        image_label.pack(padx=12, pady=(12, 8))

        tk.Label(
            preview_window,
            text=self.format_preview_details(options, root),
            anchor='nw',
            justify='left',
            wraplength=PREVIEW_WIDTH,
        ).pack(fill='both', padx=12, pady=(0, 12))

        def refresh_image():
            image = create_preview_image(options, sample_path=self.sample_image_path, rotation=self.sample_rotation)
            image_label.configure(image=image)
            image_label.image = image

        def choose_preview_image():
            self.select_sample_image()
            refresh_image()

        def rotate_preview_image(degrees: int):
            self.rotate_sample_image(degrees)
            refresh_image()

        controls = tk.Frame(preview_window)
        controls.pack(fill='x', padx=12, pady=(0, 12))
        tk.Button(controls, text='Change Preview Image', command=choose_preview_image).pack(side='left')
        tk.Button(controls, text='Use Generated', command=lambda: (self.clear_sample_image(), refresh_image())).pack(side='left', padx=(8, 0))
        tk.Button(controls, text='Rotate Left', command=lambda: rotate_preview_image(-90)).pack(side='left', padx=(8, 0))
        tk.Button(controls, text='Rotate Right', command=lambda: rotate_preview_image(90)).pack(side='left', padx=(8, 0))
        refresh_image()

    def select_output_file(self):
        default_name = self.output_name_var.get() or f'PICCON01.{self.output_format.get()}'
        path = filedialog.asksaveasfilename(title='Select Output File', defaultextension='.' + self.output_format.get(), filetypes=[('Nikon profile', f'*.{self.output_format.get()}')], initialfile=default_name)
        if path:
            self.output_file = Path(path)
            self.output_name_var.set(self.output_file.name)
            self.update_status(f'Selected output file: {self.output_file.name}')

    def select_input_folder(self):
        path = filedialog.askdirectory(title='Select Folder with XMP Files')
        if path:
            self.input_folder = Path(path)
            self.update_status(f'Selected input folder: {self.input_folder}')

    def select_output_folder(self):
        path = filedialog.askdirectory(title='Select Output Folder')
        if path:
            self.output_folder = Path(path)
            self.update_status(f'Selected output folder: {self.output_folder}')

    def convert_single_file(self):
        if not self.input_file:
            self.select_input_file()
            if not self.input_file:
                return

        if not self.output_file:
            self.select_output_file()
            if not self.output_file:
                return

        if not self.input_file or not self.output_file:
            return

        self.output_file = normalize_output_path(self.output_file, self.output_format.get())

        try:
            convert_file(self.input_file, self.output_file)
            messagebox.showinfo('Success', f'File converted successfully:\n{self.output_file}')
            self.update_status(f'Converted {self.input_file.name} to {self.output_file.name}')
        except Exception as exc:
            messagebox.showerror('Error', f'Conversion failed: {exc}')
            self.update_status('Conversion failed')

    def preview_np3(self):
        if not self.input_file:
            self.select_input_file()
            if not self.input_file:
                return

        try:
            settings = parse_xmp_file(self.input_file)
            options = map_photoshop_to_nikon(settings, self.input_file)
            options['source'] = self.input_file
            options['size'] = 'generated'
            self.show_render_preview(options, 'Conversion Preview')
        except Exception as exc:
            messagebox.showerror('Error', f'Preview generation failed: {exc}')
            self.update_status('Preview generation failed')

    def repair_profile_file(self):
        path = filedialog.askopenfilename(
            title='Select NP3/NCP file to repair',
            filetypes=[('Nikon profile', '*.np3;*.ncp')]
        )
        if not path:
            return

        input_file = Path(path)
        try:
            self.show_render_preview(parse_np3_preview_options(input_file), 'Repair Preview')
        except Exception:
            pass

        default_name = f'repaired_{input_file.name}'
        save_path = filedialog.asksaveasfilename(
            title='Save repaired profile',
            defaultextension=input_file.suffix,
            filetypes=[('Nikon profile', f'*{input_file.suffix}')],
            initialfile=default_name
        )
        if not save_path:
            return

        try:
            repair_np3_file(input_file, Path(save_path), template_path=get_template_path(input_file.suffix.lstrip('.')))
            messagebox.showinfo('Success', f'Repaired profile saved:\n{save_path}')
            self.update_status(f'Repaired {input_file.name} -> {Path(save_path).name}')
        except Exception as exc:
            messagebox.showerror('Error', f'Repair failed: {exc}')
            self.update_status('Repair failed')

    def repair_profile_to_camera(self):
        path = filedialog.askopenfilename(
            title='Select NP3/NCP file to repair to SD card',
            filetypes=[('Nikon profile', '*.np3;*.ncp')]
        )
        if not path:
            return

        input_file = Path(path)
        try:
            self.show_render_preview(parse_np3_preview_options(input_file), 'Repair Preview')
        except Exception:
            pass

        self.save_np3_source_to_camera(input_file)

    def export_profile_to_xmp(self):
        path = filedialog.askopenfilename(
            title='Select NP3/NCP file to export as XMP',
            filetypes=[('Nikon profile', '*.np3;*.ncp')]
        )
        if not path:
            return

        input_file = Path(path)
        try:
            self.show_render_preview(parse_np3_preview_options(input_file), 'NP3/NCP to XMP Preview')
        except Exception:
            pass

        save_path = filedialog.asksaveasfilename(
            title='Save approximate XMP preset',
            defaultextension='.xmp',
            filetypes=[('XMP preset', '*.xmp')],
            initialfile=f'{input_file.stem}_approx.xmp',
        )
        if not save_path:
            return

        try:
            output_path = export_nikon_profile_to_xmp(input_file, Path(save_path))
            messagebox.showinfo('Success', f'Exported approximate XMP preset:\n{output_path}')
            self.update_status(f'Exported {input_file.name} as approximate XMP')
        except Exception as exc:
            messagebox.showerror('Error', f'XMP export failed: {exc}')
            self.update_status('XMP export failed')

    def convert_np3_to_np2(self):
        path = filedialog.askopenfilename(
            title='Select NP3 file to convert to NP2',
            filetypes=[('Nikon NP3 profile', '*.np3')]
        )
        if not path:
            return

        input_file = Path(path)
        save_path = filedialog.asksaveasfilename(
            title='Save NP2 file',
            defaultextension='.np2',
            filetypes=[('Nikon NP2 profile', '*.np2')],
            initialfile=f'{input_file.stem}.NP2',
        )
        if not save_path:
            return

        try:
            output_path = convert_np3_file_to_np2(input_file, Path(save_path))
            messagebox.showinfo('Success', f'Converted to NP2:\n{output_path}')
            self.update_status(f'Converted {input_file.name} to NP2')
        except Exception as exc:
            messagebox.showerror('Error', f'NP3 to NP2 conversion failed: {exc}')
            self.update_status('NP3 to NP2 conversion failed')

    def show_np3_preview(self, options: dict):
        preview_window = tk.Toplevel(self.root)
        preview_window.title('NP3 Preview')
        preview_window.resizable(False, False)

        header = tk.Label(preview_window, text='NP3 Preview (approximate)', font=('Segoe UI', 11, 'bold'))
        header.pack(padx=12, pady=(12, 8), anchor='w')

        preview_frame = tk.Frame(preview_window)
        preview_frame.pack(padx=12, pady=(0, 12))

        image_canvas = tk.Canvas(preview_frame, width=380, height=120, bg='white', bd=1, relief='solid')
        image_canvas.pack(pady=(0, 8))
        self.draw_preview_simulation(image_canvas, options)

        curve_canvas = tk.Canvas(preview_frame, width=380, height=120, bg='white', bd=1, relief='solid')
        curve_canvas.pack()
        self.draw_preview_curve(curve_canvas, options)

        info_frame = tk.Frame(preview_window, padx=12, pady=8)
        info_frame.pack(fill='both', expand=True)

        fields = [
            ('Preset Name', options.get('name', 'Converted')),
            ('Contrast', options.get('contrast', 0)),
            ('Highlights', options.get('highlights', 0)),
            ('Shadows', options.get('shadows', 0)),
            ('White Level', options.get('whiteLevel', 0)),
            ('Black Level', options.get('blackLevel', 0)),
            ('Saturation', options.get('saturation', 0)),
            ('Clarity', options.get('clarity', 0.5)),
            ('Comment', options.get('comment', '')),
        ]

        for row, (label_text, value) in enumerate(fields):
            tk.Label(info_frame, text=f'{label_text}:', anchor='e', width=12).grid(row=row, column=0, sticky='e', padx=(0, 8), pady=2)
            tk.Label(info_frame, text=str(value), anchor='w', width=34, wraplength=260, justify='left').grid(row=row, column=1, sticky='w', pady=2)

        note = tk.Label(preview_window, text='Note: This preview is an approximate representation of the Nikon profile effect.', fg='gray40', wraplength=380, justify='left')
        note.pack(padx=12, pady=(0, 12), anchor='w')

    def draw_preview_curve(self, canvas: tk.Canvas, options: dict):
        width = int(canvas['width'])
        height = int(canvas['height'])
        curve_points = []

        def clamp01(value):
            return max(0.0, min(1.0, value))

        contrast = options.get('contrast', 0) / 100.0
        highlights = options.get('highlights', 0) / 100.0
        shadows = options.get('shadows', 0) / 100.0
        white_level = options.get('whiteLevel', 0) / 100.0
        black_level = options.get('blackLevel', 0) / 100.0

        for x in range(width + 1):
            t = x / width
            y = t
            y = y * (1.0 + white_level * 0.15) + black_level * 0.15
            y = ((y - 0.5) * (1.0 + contrast * 0.8)) + 0.5
            if highlights != 0:
                if y > 0.5:
                    y += (1.0 - y) * highlights * 0.25
                else:
                    y += y * highlights * 0.06
            if shadows != 0:
                if y < 0.5:
                    y -= y * shadows * 0.25
                else:
                    y -= (1.0 - y) * shadows * 0.06
            y = clamp01(y)
            curve_points.append((x, height - int(y * height)))

        for i in range(width):
            canvas.create_rectangle(i, 0, i + 1, height, fill=f'#{int(i / width * 255):02x}{int(i / width * 255):02x}{int(i / width * 255):02x}', outline='')

        coords = []
        for x, y in curve_points:
            coords.extend([x, y])
        canvas.create_line(coords, fill='red', width=2)
        canvas.create_text(10, 10, text='Input', anchor='nw', fill='black', font=('Segoe UI', 8))
        canvas.create_text(width - 10, height - 10, text='Output', anchor='se', fill='black', font=('Segoe UI', 8))

    def draw_preview_simulation(self, canvas: tk.Canvas, options: dict):
        width = int(canvas['width'])
        height = int(canvas['height'])
        input_pixels = []
        output_pixels = []

        def clamp01(value):
            return max(0.0, min(1.0, value))

        contrast = options.get('contrast', 0) / 100.0
        highlights = options.get('highlights', 0) / 100.0
        shadows = options.get('shadows', 0) / 100.0
        white_level = options.get('whiteLevel', 0) / 100.0
        black_level = options.get('blackLevel', 0) / 100.0

        for x in range(width + 1):
            t = x / width
            input_pixels.append(t)
            y = t
            y = y * (1.0 + white_level * 0.15) + black_level * 0.15
            y = ((y - 0.5) * (1.0 + contrast * 0.8)) + 0.5
            if highlights != 0:
                if y > 0.5:
                    y += (1.0 - y) * highlights * 0.25
                else:
                    y += y * highlights * 0.06
            if shadows != 0:
                if y < 0.5:
                    y -= y * shadows * 0.25
                else:
                    y -= (1.0 - y) * shadows * 0.06
            output_pixels.append(clamp01(y))

        for x in range(width):
            base = int(input_pixels[x] * 255)
            canvas.create_rectangle(x, 0, x + 1, height // 2, fill=f'#{base:02x}{base:02x}{base:02x}', outline='')
            out = int(output_pixels[x] * 255)
            canvas.create_rectangle(x, height // 2, x + 1, height, fill=f'#{out:02x}{out:02x}{out:02x}', outline='')

        canvas.create_text(6, 6, text='Input gradient', anchor='nw', fill='black', font=('Segoe UI', 8, 'bold'))
        canvas.create_text(6, height // 2 + 6, text='Simulated output', anchor='nw', fill='black', font=('Segoe UI', 8, 'bold'))
        canvas.create_line(0, height // 2, width, height // 2, fill='gray70')

    def save_as_camera_file(self):
        if not self.input_file:
            self.select_input_file()
            if not self.input_file:
                return

        camera_folder = self.choose_camera_folder()
        if camera_folder is None:
            return

        camera_folder.mkdir(parents=True, exist_ok=True)

        try:
            camera_file = find_next_piccon_path(camera_folder, self.output_format.get())
            self.output_name_var.set(camera_file.name)
            convert_file(self.input_file, camera_file)
            messagebox.showinfo('Success', f'Saved camera import file:\n{camera_file}')
            self.update_status(f'Saved {camera_file.name} to {camera_folder}')
        except Exception as exc:
            messagebox.showerror('Error', f'Save failed: {exc}')
            self.update_status('Save failed')

    def convert_folder(self):
        if not self.input_folder or not self.output_folder:
            messagebox.showwarning('Missing selection', 'Please select both an input folder and an output folder.')
            return

        try:
            run_folder(self.input_folder, self.output_folder, f'.{self.output_format.get()}')
            messagebox.showinfo('Success', f'Folder converted successfully.\nOutput folder: {self.output_folder}')
            self.update_status(f'Converted folder {self.input_folder} to {self.output_folder}')
        except Exception as exc:
            messagebox.showerror('Error', f'Folder conversion failed: {exc}')
            self.update_status('Folder conversion failed')

    def update_status(self, message: str):
        self.status_label.config(text=message)


def main():
    parser = argparse.ArgumentParser(description='Convert Photoshop XMP presets into Nikon Picture Control placeholder profiles.')
    parser.add_argument('--input', type=Path, help='Input XMP preset file')
    parser.add_argument('--output', type=Path, help='Output file path (.np3 or .ncp)')
    parser.add_argument('--input-folder', type=Path, help='Input folder containing .xmp files')
    parser.add_argument('--output-folder', type=Path, help='Output folder for Nikon profile files')
    parser.add_argument('--format', choices=['np3', 'ncp'], default=get_default_output_format(), help='Output extension to create')
    parser.add_argument('--export-xmp', action='store_true', help='Export an NP3/NCP file to an approximate XMP preset')
    parser.add_argument('--np3-to-np2', action='store_true', help='Convert an NP3 file to NP2 by removing NP3-only records')
    args = parser.parse_args()

    if args.np3_to_np2 and args.input and args.output:
        convert_np3_file_to_np2(args.input, args.output)
    elif args.export_xmp and args.input and args.output:
        export_nikon_profile_to_xmp(args.input, args.output)
    elif args.input and args.output:
        output_path = normalize_output_path(args.output, args.format)
        convert_file(args.input, output_path)
    elif args.input_folder and args.output_folder:
        run_folder(args.input_folder, args.output_folder, f'.{args.format}')
    elif len(sys.argv) == 1:
        if getattr(sys, 'frozen', False):
            temp_root = tk.Tk()
            temp_root.withdraw()
            messagebox.showwarning(
                'Packaged executable detected',
                'This application is running as a packaged executable.\n'
                'The workspace source code may be newer than this build.\n'
                'Use launch_converter.bat or run python photo_preset_to_nikon.py to launch the latest version.'
            )
            temp_root.destroy()
        root = tk.Tk()
        NikonConverterGUI(root)
        root.mainloop()
    else:
        parser.error('Please specify either --input and --output, or --input-folder and --output-folder.')


if __name__ == '__main__':
    main()
