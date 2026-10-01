# -*- mode: python ; coding: utf-8 -*-
"""
Spec de PyInstaller para empaquetar la interfaz en un único .exe de Windows.

Compilar con:
    py -m PyInstaller gui_puente.spec --noconfirm

El resultado queda en dist\Envolventes de Carga Viva.exe (un solo archivo,
sin consola, con icono propio). No requiere Python instalado en la máquina
que lo ejecute.
"""

from PyInstaller.utils.hooks import collect_submodules

hidden = (
    collect_submodules("pandas")
    + [
        # Sólo lo que realmente usa la app: PIL.Image y el plugin GIF, para
        # el escritor "pillow" de la animación. NO se usa collect_submodules
        # de PIL completo: el hook de Pillow que trae PyInstaller igual
        # empaqueta todos sus plugins binarios (.pyd), incluidos formatos
        # exóticos como AVIF/WEBP/HEIF que esta app nunca importa, y esos
        # binarios grandes son justo los que el antivirus corrompe al
        # escanearlos durante la extracción del .exe (ver más abajo).
        "PIL.Image", "PIL.GifImagePlugin", "PIL.PngImagePlugin",
        "openpyxl",
        "openpyxl.cell._writer",
        "matplotlib.backends.backend_tkagg",
        "matplotlib.backends.backend_agg",
    ]
)

a = Analysis(
    ["gui_puente.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # pyarrow/numba/llvmlite: dependencias opcionales de pandas (backend de
    # dtypes con Arrow / aceleración JIT) que esta app nunca usa -- el Excel
    # se escribe con openpyxl (ver hiddenimports) -- pero que PyInstaller
    # arrastra igual si están instaladas en el entorno de compilación,
    # agregando más de 80 MB al .exe sin ninguna función real.
    excludes=["PyQt5", "PyQt6", "PySide2", "PySide6", "scipy", "IPython", "notebook",
             "pyarrow", "numba", "llvmlite"],
    noarchive=False,
    optimize=0,
)

# El hook de Pillow que trae PyInstaller empaqueta TODOS sus plugins de
# formato binarios, muchos de los cuales esta app no usa (sólo necesita
# PNG/GIF). Esos .pyd extra, además de inflar el .exe, son la causa de un
# fallo intermitente real: "Failed to extract PIL\_avif...pyd: decompression
# resulted in return code -1" cuando el antivirus escanea el archivo justo
# mientras el bootloader onefile lo está descomprimiendo al arrancar. Se
# excluyen los plugins de formatos que no se usan.
_PIL_INNECESARIOS = ("_avif", "_webp", "_imagingcms", "avif", "webp", "heif", "raqm")
a.binaries = [b for b in a.binaries
             if not (("PIL" in b[0] or "Pillow" in b[0])
                     and any(n in b[0] for n in _PIL_INNECESARIOS))]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Envolventes de Carga Viva",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # aplicación de escritorio: sin ventana de consola
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icono_puente.ico",
)
