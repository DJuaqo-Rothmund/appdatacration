"""
ai_share.py
===========
Compartir lo que aprendió la IA entre teléfonos que NO comparten cuenta ni datos.

Un teléfono exporta un paquete «PhenoRubus_IA_<fecha>.zip» (WhatsApp, correo,
Drive…) y el otro lo importa. La importación SUMA conocimiento sin borrar nada:

* referencias etiquetadas (descriptores de imagen + estado BBCH + miniatura);
* estados BBCH editados y palabras clave (escala enriquecida con documentos);
* textos de los documentos técnicos cargados.

Cada referencia se identifica por la huella (SHA-1) de su descriptor: importar
dos veces el mismo paquete, o intercambiarlo en ambos sentidos, no duplica nada.
Así los dos teléfonos quedan con la misma «memoria» y siguen siendo independientes
en todo lo demás (variedades, registros, ajustes).
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass

from platform_utils import data_subdir

PACKAGE_KIND = "phenorubus-ia"
THUMB_SIDE = 256


def _key(embedding: bytes) -> str:
    return hashlib.sha1(embedding).hexdigest()


def _thumb_bytes(path: str | None) -> bytes | None:
    if not path or not os.path.exists(path):
        return None
    try:
        from PIL import Image, ImageOps
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        img.thumbnail((THUMB_SIDE, THUMB_SIDE))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 - una miniatura dañada no impide exportar
        return None


def export_knowledge(db, dest_dir: str | None = None) -> tuple[str, int]:
    """Crea el paquete y devuelve (ruta, n.º de referencias)."""
    dest_dir = dest_dir or data_subdir("backups")
    path = os.path.join(dest_dir, f"PhenoRubus_IA_{_dt.datetime.now():%Y%m%d_%H%M%S}.zip")
    refs = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in db.list_references():
            emb = bytes(r["embedding"])
            key = _key(emb)
            item = {"key": key, "bbch_code": r["bbch_code"], "extractor": r["extractor"],
                    "dim": r["dim"], "embedding": base64.b64encode(emb).decode("ascii"),
                    "created_at": r["created_at"], "image": None}
            data = _thumb_bytes(r["image_path"])
            if data:
                item["image"] = f"referencias/{key}.jpg"
                zf.writestr(item["image"], data, compress_type=zipfile.ZIP_STORED)
            refs.append(item)
        stages = [dict(s) for s in db.list_bbch() if s["source"] != "base"]
        docs = db.ai_db.query("SELECT title, text, stages_found, created_at FROM documents ORDER BY id")
        zf.writestr("conocimiento.json", json.dumps(
            {"kind": PACKAGE_KIND, "version": 1,
             "created": _dt.datetime.now().isoformat(timespec="seconds"),
             "references": refs, "stages": stages, "documents": docs},
            ensure_ascii=False))
    db.log("export", "ai_knowledge", None, f"{os.path.basename(path)} · {len(refs)} referencias")
    return path, len(refs)


@dataclass
class KnowledgeImport:
    added: int = 0          # referencias nuevas
    known: int = 0          # ya estaban (sin duplicar)
    stages: int = 0         # estados BBCH nuevos o enriquecidos
    documents: int = 0

    def summary(self) -> str:
        parts = [f"{self.added} referencias nuevas"]
        if self.known:
            parts.append(f"{self.known} ya estaban")
        if self.stages:
            parts.append(f"{self.stages} estados BBCH enriquecidos")
        if self.documents:
            parts.append(f"{self.documents} documentos")
        return " · ".join(parts)


def is_knowledge_package(path: str) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            return "conocimiento.json" in zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return False


def import_knowledge(db, path: str, classifier=None) -> KnowledgeImport:
    """Suma el conocimiento del paquete a este teléfono (nunca borra ni reemplaza)."""
    if not is_knowledge_package(path):
        raise ValueError("El archivo no es un paquete de conocimiento de la IA "
                         "(«PhenoRubus_IA_….zip»)")
    res = KnowledgeImport()
    with zipfile.ZipFile(path) as zf:
        pkg = json.loads(zf.read("conocimiento.json"))
        if pkg.get("kind") != PACKAGE_KIND:
            raise ValueError("Paquete de conocimiento no reconocido")
        have = {_key(bytes(r["embedding"])) for r in db.list_references()}
        img_dir = data_subdir("ai_referencias")
        for r in pkg.get("references", []):
            emb = base64.b64decode(r["embedding"])
            key = _key(emb)
            if key in have:
                res.known += 1
                continue
            image_path = None
            if r.get("image") and r["image"] in zf.namelist():
                image_path = os.path.join(img_dir, f"{key}.jpg")
                with open(image_path, "wb") as f:
                    f.write(zf.read(r["image"]))
            db.add_reference(int(r["bbch_code"]), r["extractor"], emb, int(r["dim"]),
                             image_path, None)
            have.add(key)
            res.added += 1

    local = {s["code"]: s for s in db.list_bbch()}
    for s in pkg.get("stages", []):
        code = int(s["code"])
        mine = local.get(code)
        if mine is None:
            db.upsert_bbch(code, s["label"], s.get("description") or "", s.get("keywords") or "",
                           source=s.get("source") or "user")
            res.stages += 1
            continue
        kw = set((mine["keywords"] or "").split()) | set((s.get("keywords") or "").split())
        keep_mine = mine["source"] != "base"      # lo editado en este teléfono manda
        label = mine["label"] if keep_mine else s["label"]
        desc = (mine["description"] if keep_mine else s.get("description")) or \
            mine["description"] or s.get("description") or ""
        new_kw = " ".join(sorted(kw))
        if (label, desc, new_kw) != (mine["label"], mine["description"] or "", mine["keywords"] or ""):
            db.upsert_bbch(code, label, desc, new_kw,
                           source=mine["source"] if keep_mine else (s.get("source") or "user"))
            res.stages += 1

    known_docs = {(d["title"], len(d["text"] or "")) for d in
                  db.ai_db.query("SELECT title, text FROM documents")}
    for d in pkg.get("documents", []):
        if (d["title"], len(d.get("text") or "")) in known_docs:
            continue
        db.add_document(d["title"], None, d.get("text") or "", int(d.get("stages_found") or 0))
        res.documents += 1

    if classifier is not None:
        classifier.invalidate()
    db.log("import", "ai_knowledge", None, f"{os.path.basename(path)} · {res.summary()}")
    return res
