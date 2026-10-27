"""ESX container layer.

An .esx is a ZIP container with a very specific byte profile (per the
public ESX format forensics, verified on 100+ real exports):

  - outer compression: ZIP_STORED (deflated outer = rejected by Xactimate)
  - ZIP metadata: create_system=0, create_version=0 (DOS/FAT)
  - photo entries (.JPG): raw JPEG bytes, stored, never encrypted
  - XACTDOC.XML: the estimate document (plaintext in generated/standards-
    based files)
  - XACTDOC.ZIPXML: AES-256-ECB-encrypted inner ZIP holding XACTDOC.XML
    in native Xactimate exports. This toolkit does NOT decrypt that blob:
    the key is non-public and locked files are protected deliberately.
"""

import io
import zipfile

MEMBER_DOC = "XACTDOC.XML"
MEMBER_ENCRYPTED = "XACTDOC.ZIPXML"


def _dos_zinfo(name, size):
    """ZipInfo matching the real-file DOS/FAT byte profile."""
    zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    zi.create_system = 0      # DOS/FAT (python default of 3 = Unix = rejected)
    zi.create_version = 0
    zi.extract_version = 20
    zi.compress_type = zipfile.ZIP_STORED
    zi.external_attr = 0
    zi.file_size = size
    zi.compress_size = size
    return zi


def write_esx(path, doc_xml_bytes, photos=None):
    """Write a standards-based .esx file.

    doc_xml_bytes: UTF-8 encoded XACTDOC.XML content.
    photos: iterable of (name, jpeg_bytes); JPEGs are stored raw, unencrypted.
    """
    photos = list(photos or [])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(_dos_zinfo(MEMBER_DOC, len(doc_xml_bytes)), doc_xml_bytes)
        for name, data in photos:
            if not data.startswith(b"\xff\xd8"):
                raise ValueError(f"photo {name!r} is not raw JPEG (must start ff d8)")
            zf.writestr(_dos_zinfo(name, len(data)), data)
    with open(path, "wb") as fh:
        fh.write(buf.getvalue())
    return path


def read_members(path):
    """Open an ESX and classify its members.

    Returns dict:
      ok            — bool (container opened)
      container     — {outer_stored, dos_metadata} profile booleans
      doc_xml       — bytes if a plaintext XACTDOC.XML exists, else None
      encrypted     — bool if an encrypted XACTDOC.ZIPXML member exists
      encrypted_probe — first byte of the encrypted blob (header byte)
      photos        — list of (name, jpeg_bytes)
      members       — [(name, size, compress_type)]
      errors        — [str]
    """
    out = {"ok": False, "container": {}, "doc_xml": None, "encrypted": False,
           "encrypted_probe": None, "photos": [], "members": [], "errors": []}
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        out["errors"].append(f"not a readable ZIP container: {e}")
        return out
    out["ok"] = True
    infos = zf.infolist()
    out["container"] = {
        "outer_stored": all(i.compress_type == zipfile.ZIP_STORED for i in infos),
        "dos_metadata": all(i.create_system == 0 for i in infos),
    }
    for info in infos:
        data = zf.read(info.filename)
        out["members"].append((info.filename, info.file_size, info.compress_type))
        upper = info.filename.upper()
        if upper == MEMBER_DOC:
            out["doc_xml"] = data
        elif upper == MEMBER_ENCRYPTED or upper.endswith(".ZIPXML"):
            out["encrypted"] = True
            out["encrypted_probe"] = data[0] if data else None
        elif upper.endswith((".JPG", ".JPEG")):
            if data[:2] == b"\xff\xd8":
                out["photos"].append((info.filename, data))
            else:
                out["errors"].append(
                    f"photo member {info.filename!r} is not raw JPEG "
                    "(encrypted/deflated photos = 'Invalid file format' on import)")
        else:
            out["photos"].append((info.filename, data))  # other attachments pass through
    if out["doc_xml"] is None and not out["encrypted"]:
        out["errors"].append("no XACTDOC.XML member found (files with only "
                             "ZIPXML or no members are not estimable here)")
    return out


def extract_photos(path, out_dir):
    """Dump all photo members to out_dir. Returns list of written paths."""
    import os
    zf = zipfile.ZipFile(path)
    written = []
    os.makedirs(out_dir, exist_ok=True)
    for info in zf.infolist():
        if info.filename.upper().endswith((".JPG", ".JPEG")):
            data = zf.read(info.filename)
            safe = os.path.basename(info.filename)
            dest = os.path.join(out_dir, safe)
            with open(dest, "wb") as fh:
                fh.write(data)
            written.append(dest)
    return written
