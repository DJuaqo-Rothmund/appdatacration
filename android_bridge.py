"""
android_bridge.py
=================
Acceso a hardware y almacenamiento nativo de Android con Pyjnius, con
alternativas de escritorio para desarrollo en PC.

* Cámara: ``ACTION_IMAGE_CAPTURE`` escribiendo en un URI de MediaStore
  (Pictures/FenoRubus). No necesita FileProvider y deja un respaldo en la
  galería del teléfono; luego se copia al almacenamiento privado de la app.
* Galería: Photo Picker del sistema (Android 13+) o ``ACTION_GET_CONTENT``.
* Documentos (PDF/TXT) para el módulo de calibración: ``ACTION_OPEN_DOCUMENT``.
* Compartir informes: se copian a Descargas/FenoRubus vía MediaStore y se
  lanza ``ACTION_SEND`` (WhatsApp, correo, Drive...) o ``ACTION_VIEW``.
"""
from __future__ import annotations

import datetime as _dt
import os
import shutil
import threading
import webbrowser
from typing import Callable

from PIL import Image, ImageOps

from platform_utils import APP_NAME, IS_ANDROID, android_api_level

RC_CAMERA = 0x4631
RC_GALLERY = 0x4632
RC_DOCUMENT = 0x4633

PhotoCallback = Callable[[str | None, str], None]   # (ruta_temporal | None, origen)


# ===========================================================================
# Utilidades comunes
# ===========================================================================
def store_photo(src: str, dest_dir: str, basename: str, max_side: int = 1600) -> str:
    """Normaliza (EXIF, tamaño, JPEG q88) y guarda la foto en el almacenamiento de la app."""
    os.makedirs(dest_dir, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(dest_dir, f"{basename}_{stamp}.jpg")
    img = Image.open(src)
    # Decodifica el JPEG ya reducido (escalado DCT 1/2, 1/4...): mucho más rápido y
    # con menos memoria que abrir la foto de 12 MP a tamaño completo.
    w, h = img.size
    scale = max_side / max(w, h)
    if scale < 1:
        img.draft("RGB", (int(w * scale), int(h * scale)))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    img.save(dest, "JPEG", quality=88)
    return dest


def make_thumbnail(src: str, dest: str, size: int) -> str:
    img = Image.open(src)
    img.draft("RGB", (size, size))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((size, size))
    img.save(dest, "JPEG", quality=80)
    return dest


def request_runtime_permissions(callback: Callable[[bool], None] | None = None) -> None:
    if not IS_ANDROID:
        if callback:
            callback(True)
        return
    from android.permissions import request_permissions  # type: ignore
    api = android_api_level()
    perms = ["android.permission.CAMERA"]
    if api >= 33:
        perms += ["android.permission.READ_MEDIA_IMAGES",
                  "android.permission.POST_NOTIFICATIONS"]
    else:
        perms += ["android.permission.READ_EXTERNAL_STORAGE"]
        if api < 29:
            perms += ["android.permission.WRITE_EXTERNAL_STORAGE"]

    def _done(permissions, grants):
        if callback:
            callback(all(grants))

    request_permissions(perms, _done)


# ===========================================================================
# Android
# ===========================================================================
class AndroidMedia:
    def __init__(self, tmp_dir: str):
        from android import activity  # type: ignore
        from jnius import autoclass  # type: ignore
        self.tmp_dir = tmp_dir
        os.makedirs(tmp_dir, exist_ok=True)
        self.PythonActivity = autoclass("org.kivy.android.PythonActivity")
        self.Intent = autoclass("android.content.Intent")
        self.MediaStore = autoclass("android.provider.MediaStore")
        # Pyjnius no expone clases anidadas como atributos: se cargan por nombre binario.
        self.MediaColumns = autoclass("android.provider.MediaStore$MediaColumns")
        self.ImagesMedia = autoclass("android.provider.MediaStore$Images$Media")
        self.ContentValues = autoclass("android.content.ContentValues")
        self.Activity = autoclass("android.app.Activity")
        self.api = android_api_level()
        self._pending: dict[int, tuple] = {}
        activity.bind(on_activity_result=self._on_activity_result)

    # ----------------------------------------------------------- helpers
    @property
    def activity(self):
        return self.PythonActivity.mActivity

    @property
    def resolver(self):
        return self.activity.getContentResolver()

    def _copy_uri_to_file(self, uri, dest: str) -> str:
        from jnius import autoclass  # type: ignore
        FileOutputStream = autoclass("java.io.FileOutputStream")
        ins = self.resolver.openInputStream(uri)
        try:
            if self.api >= 29:
                out = FileOutputStream(dest)
                try:
                    autoclass("android.os.FileUtils").copy(ins, out)
                finally:
                    out.close()
            else:
                if os.path.exists(dest):
                    os.remove(dest)
                File = autoclass("java.io.File")
                autoclass("java.nio.file.Files").copy(ins, File(dest).toPath())
        finally:
            ins.close()
        return dest

    def _display_name(self, uri) -> str:
        from jnius import autoclass  # type: ignore
        OpenableColumns = autoclass("android.provider.OpenableColumns")
        cursor = self.resolver.query(uri, None, None, None, None)
        name = "documento"
        if cursor is not None:
            try:
                if cursor.moveToFirst():
                    idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    if idx >= 0:
                        name = cursor.getString(idx) or name
            finally:
                cursor.close()
        return name

    def _dispatch(self, callback, *args):
        from kivy.clock import Clock
        Clock.schedule_once(lambda _dt: callback(*args), 0)

    # ---------------------------------------------------------- cámara
    def take_photo(self, callback: PhotoCallback) -> None:
        from jnius import cast  # type: ignore
        values = self.ContentValues()
        name = f"FenoRubus_{_dt.datetime.now():%Y%m%d_%H%M%S}.jpg"
        values.put(self.MediaColumns.DISPLAY_NAME, name)
        values.put(self.MediaColumns.MIME_TYPE, "image/jpeg")
        if self.api >= 29:
            values.put(self.MediaColumns.RELATIVE_PATH, f"Pictures/{APP_NAME}")
        uri = self.resolver.insert(self.ImagesMedia.EXTERNAL_CONTENT_URI, values)
        intent = self.Intent(self.MediaStore.ACTION_IMAGE_CAPTURE)
        intent.putExtra(self.MediaStore.EXTRA_OUTPUT, cast("android.os.Parcelable", uri))
        intent.addFlags(self.Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                        | self.Intent.FLAG_GRANT_READ_URI_PERMISSION)
        self._pending[RC_CAMERA] = (callback, uri)
        self.activity.startActivityForResult(intent, RC_CAMERA)

    # ---------------------------------------------------------- galería
    def pick_image(self, callback: PhotoCallback) -> None:
        if self.api >= 33:
            intent = self.Intent(self.MediaStore.ACTION_PICK_IMAGES)
        else:
            intent = self.Intent(self.Intent.ACTION_GET_CONTENT)
            intent.addCategory(self.Intent.CATEGORY_OPENABLE)
        intent.setType("image/*")
        self._pending[RC_GALLERY] = (callback, None)
        self.activity.startActivityForResult(intent, RC_GALLERY)

    def pick_document(self, callback: Callable[[str | None, str], None]) -> None:
        intent = self.Intent(self.Intent.ACTION_OPEN_DOCUMENT)
        intent.addCategory(self.Intent.CATEGORY_OPENABLE)
        intent.setType("*/*")
        self._pending[RC_DOCUMENT] = (callback, None)
        self.activity.startActivityForResult(intent, RC_DOCUMENT)

    def _on_activity_result(self, request_code, result_code, intent):
        if request_code not in self._pending:
            return
        callback, uri = self._pending.pop(request_code)
        ok = result_code == self.Activity.RESULT_OK
        # La copia del archivo (varios MB) se hace fuera del hilo de la interfaz.
        threading.Thread(target=self._handle_result, daemon=True,
                         args=(request_code, ok, intent, callback, uri)).start()

    def _handle_result(self, request_code, ok, intent, callback, uri):
        try:
            self._process_result(request_code, ok, intent, callback, uri)
        finally:
            from jnius import detach  # type: ignore
            detach()  # obligatorio en hilos Python que usan Pyjnius

    def _process_result(self, request_code, ok, intent, callback, uri):
        try:
            if request_code == RC_CAMERA:
                if not ok:
                    self.resolver.delete(uri, None, None)
                    return self._dispatch(callback, None, "camera")
                dest = os.path.join(self.tmp_dir, "camera_capture.jpg")
                return self._dispatch(callback, self._copy_uri_to_file(uri, dest), "camera")
            if not ok or intent is None or intent.getData() is None:
                return self._dispatch(callback, None,
                                      "gallery" if request_code == RC_GALLERY else "")
            data = intent.getData()
            if request_code == RC_GALLERY:
                dest = os.path.join(self.tmp_dir, "gallery_pick.jpg")
                return self._dispatch(callback, self._copy_uri_to_file(data, dest), "gallery")
            name = self._display_name(data)
            dest = os.path.join(self.tmp_dir, name)
            return self._dispatch(callback, self._copy_uri_to_file(data, dest), name)
        except Exception as exc:  # informar a la UI en vez de cerrar la app
            print("android_bridge error:", exc)
            self._dispatch(callback, None, "error")

    # --------------------------------------------------- exportar/compartir
    def export_to_downloads(self, path: str, mime: str):
        """Copia el archivo a Descargas/FenoRubus y devuelve su content:// URI."""
        from jnius import autoclass  # type: ignore
        name = os.path.basename(path)
        if self.api >= 29:
            values = self.ContentValues()
            values.put(self.MediaColumns.DISPLAY_NAME, name)
            values.put(self.MediaColumns.MIME_TYPE, mime)
            values.put(self.MediaColumns.RELATIVE_PATH, f"Download/{APP_NAME}")
            Downloads = autoclass("android.provider.MediaStore$Downloads")
            uri = self.resolver.insert(Downloads.EXTERNAL_CONTENT_URI, values)
            out = self.resolver.openOutputStream(uri)
            ins = autoclass("java.io.FileInputStream")(path)
            try:
                autoclass("android.os.FileUtils").copy(ins, out)
            finally:
                ins.close()
                out.close()
            return uri
        # Android 8-9: carpeta pública + URI file:// (se relaja StrictMode para compartir).
        Environment = autoclass("android.os.Environment")
        StrictMode = autoclass("android.os.StrictMode")
        StrictMode.setVmPolicy(autoclass("android.os.StrictMode$VmPolicy$Builder")().build())
        folder = os.path.join(Environment.getExternalStoragePublicDirectory(
            Environment.DIRECTORY_DOWNLOADS).getAbsolutePath(), APP_NAME)
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, name)
        shutil.copyfile(path, dest)
        return autoclass("android.net.Uri").fromFile(autoclass("java.io.File")(dest))

    def share(self, path: str, mime: str, title: str = "Compartir informe") -> None:
        from jnius import autoclass, cast  # type: ignore
        String = autoclass("java.lang.String")
        uri = self.export_to_downloads(path, mime)
        intent = self.Intent(self.Intent.ACTION_SEND)
        intent.setType(mime)
        intent.putExtra(self.Intent.EXTRA_STREAM, cast("android.os.Parcelable", uri))
        intent.putExtra(self.Intent.EXTRA_SUBJECT, os.path.basename(path))
        intent.addFlags(self.Intent.FLAG_GRANT_READ_URI_PERMISSION)
        chooser = self.Intent.createChooser(intent, cast("java.lang.CharSequence", String(title)))
        self.activity.startActivity(chooser)

    def open(self, path: str, mime: str) -> None:
        uri = self.export_to_downloads(path, mime)
        intent = self.Intent(self.Intent.ACTION_VIEW)
        intent.setDataAndType(uri, mime)
        intent.addFlags(self.Intent.FLAG_GRANT_READ_URI_PERMISSION)
        self.activity.startActivity(intent)


# ===========================================================================
# Escritorio (desarrollo en PC)
# ===========================================================================
class DesktopMedia:
    """En PC no hay cámara integrada: ambas acciones abren un selector de archivos."""

    def __init__(self, tmp_dir: str, file_chooser: Callable[[Callable[[str | None], None], tuple], None]):
        self.tmp_dir = tmp_dir
        self.file_chooser = file_chooser

    def take_photo(self, callback: PhotoCallback) -> None:
        self.file_chooser(lambda p: callback(p, "camera"), (".jpg", ".jpeg", ".png"))

    def pick_image(self, callback: PhotoCallback) -> None:
        self.file_chooser(lambda p: callback(p, "gallery"), (".jpg", ".jpeg", ".png"))

    def pick_document(self, callback) -> None:
        self.file_chooser(lambda p: callback(p, os.path.basename(p) if p else ""),
                          (".pdf", ".txt", ".md", ".html", ".htm"))

    def share(self, path: str, mime: str, title: str = "") -> None:
        self.open(path, mime)

    def open(self, path: str, mime: str) -> None:
        webbrowser.open("file://" + os.path.abspath(path))

    def export_to_downloads(self, path: str, mime: str):
        dest_dir = os.path.join(os.path.expanduser("~"), "Downloads", APP_NAME)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, os.path.basename(path))
        shutil.copyfile(path, dest)
        return dest


def create_media(tmp_dir: str, desktop_file_chooser=None):
    if IS_ANDROID:
        return AndroidMedia(tmp_dir)
    return DesktopMedia(tmp_dir, desktop_file_chooser)
