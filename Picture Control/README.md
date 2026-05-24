# Nikon NP3 Repair and Preview Tool

A small Windows/Python utility for working with Nikon Picture Control `.NP3` and `.NCP` files, with a focus on repairing internet-downloaded NP3 recipes that do not load or render correctly on Nikon Z-series cameras.

This project was tested with a Nikon Z5 workflow.

## Features

- Repair NP3/NCP files by rebuilding them from a known-good camera-exported template.
- Save repaired files directly to the next open `PICCON##` slot on an SD card.
- Convert Photoshop/Lightroom XMP preset metadata into Nikon-style NP3/NCP output.
- Browse a local folder of premade Nikon recipe NP3 files.
- Preview XMP conversions, NP3/NCP repairs, and premade recipes against a generated sample scene or your own JPG/PNG.
- Rotate preview images without modifying the original image file.

## Important Format Note

Nikon `.NP3` and `.NCP` files are proprietary binary formats. This tool is based on observed layouts and repair-by-template behavior, not official Nikon documentation.

The preview image is an approximation for comparing looks quickly. It is not Nikon's exact in-camera rendering engine.

## Acknowledgements

This project builds on NP3/Flexible Color Picture Control research and data from:

- [ssssota/nikon-flexible-color-picture-control](https://github.com/ssssota/nikon-flexible-color-picture-control)
- [krafta-visio/nikon-flexible-color-creator](https://github.com/krafta-visio/nikon-flexible-color-creator)

Those projects provided valuable reference material for understanding Nikon Flexible Color Picture Control binary structure.

Premade recipe files used during development came from:

- [shouryan01/Nikon-Recipes](https://github.com/shouryan01/Nikon-Recipes/tree/main)

## Template File

Repair depends on a known-good camera-exported NP3 template named `PICCON01.NP3` in the project folder.

The included template is used as the safe binary shell for repaired files. For best compatibility with your own camera, export a working Picture Control from your camera, name it `PICCON01.NP3`, and replace the template in this folder.

## Install

Install Python 3.10+ for Windows, then install dependencies:

```powershell
python -m pip install -r requirements.txt
```

## Launch

Use either:

```powershell
python photo_preset_to_nikon.py
```

or double-click:

```text
launch_converter.bat
```

You can recreate the local Windows shortcut with:

```text
reset_shortcut.bat
```

The `.lnk` shortcut itself is machine-specific and is intentionally ignored by git.

## SD Card Saving

The app looks for an inserted Nikon SD card folder such as:

```text
E:\NIKON\CUSTOMPC
```

If it cannot find one automatically, it asks you to choose either the SD card root or the `NIKON/CUSTOMPC` folder.

When saving to the camera card, it scans existing `PICCON##.NP3` and `PICCON##.NCP` files and uses the first open slot from `PICCON01` through `PICCON99`.

You can override the automatic SD-card path with:

```powershell
$env:NIKON_CUSTOMPC_PATH = "E:\NIKON\CUSTOMPC"
python photo_preset_to_nikon.py
```

## Premade Recipe Browser

The app looks for premade recipe NP3 files in these places:

- `recipes/`
- `Nikon-Recipes-main/`
- `../Nikon-Recipes-main/`

You can also set:

```powershell
$env:NIKON_RECIPE_ROOT = "C:\path\to\Nikon-Recipes-main"
python photo_preset_to_nikon.py
```

If no recipe folder is found automatically, the app asks you to choose one.

## Command Line

Convert one XMP preset:

```powershell
python photo_preset_to_nikon.py --input path\to\preset.xmp --output path\to\output.np3
```

Convert a folder of XMP presets:

```powershell
python photo_preset_to_nikon.py --input-folder path\to\presets --output-folder path\to\nikon-profiles
```

## GitHub Publishing Notes

Recommended repo contents:

- `photo_preset_to_nikon.py`
- `np3_serializer.py`
- `np3_data.py`
- `PICCON01.NP3`
- `requirements.txt`
- `launch_converter.bat`
- `reset_shortcut.bat`
- `recreate_shortcut.py`
- `README.md`
- `.gitignore`

Generated build artifacts, logs, shortcuts, screenshots, and caches should stay out of git.
