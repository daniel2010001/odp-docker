"""
Resource-upload type allowlist.

CKAN 2.12 `ResourceUpload` (`ckan/lib/uploader.py:279`) does no type check: the
`ckan.upload.<type>.mimetypes/types` options only reach user and group image
uploads (`Upload.verify_type`, `uploader.py` ~220-270). `get_resource_uploader`
(`uploader.py:75`) asks the `IUploader` plugins first and falls through to the
default uploader when one returns None; `package_update` (`logic/action/update.py:337`)
calls it before anything is stored, and `resource_create` and `resource_update` go
through `package_update`. (`package_create` with a file object in `resources` fails
core validation before any uploader is reached.) `check_upload` runs there: the extension must be allowed, and libmagic's reading of the first 2048
bytes must agree with it, as `Upload.verify_type` does.
"""
import os

import magic

import ckan.plugins.toolkit as toolkit

_TEXT = ("text/plain", "text/csv", "application/csv", "text/tab-separated-values")
_JSON = ("application/json", "application/geo+json", "text/plain")
_OOXML = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/zip")
_ODS = ("application/vnd.oasis.opendocument.spreadsheet", "application/zip")

# The one table: extension -> mimetypes libmagic may report for it. The portal
# mirrors the keys.
ALLOWED_TYPES = {
    "csv": _TEXT,
    "tsv": _TEXT,
    "xlsx": _OOXML,
    "xls": ("application/vnd.ms-excel", "application/x-ole-storage", "application/CDFV2"),
    "ods": _ODS,
    "json": _JSON,
    "geojson": _JSON,
    "xml": ("text/xml", "application/xml"),
    "txt": _TEXT,
    "pdf": ("application/pdf",),
    "png": ("image/png",),
    "jpg": ("image/jpeg",),
    "jpeg": ("image/jpeg",),
    "zip": ("application/zip", "application/x-zip-compressed"),
}

_SNIFF_BYTES = 2048


def _refuse(message):
    raise toolkit.ValidationError({"upload": [message]})


def check_upload(upload):
    """Refuse `upload` unless it is an allowed type; do nothing for anything
    that is not a real file with a filename (links, an unchanged upload)."""
    filename = getattr(upload, "filename", None)
    stream = getattr(upload, "stream", None)
    if not filename or stream is None:
        return
    extension = os.path.splitext(filename)[1].lstrip(".").lower()
    allowed = ", ".join(ALLOWED_TYPES)
    if extension not in ALLOWED_TYPES:
        shown = "." + extension if extension else "(none)"
        _refuse(
            f"File type {shown} is not allowed. "
            f"Allowed extensions: {allowed}."
        )
    content = stream.read(_SNIFF_BYTES)
    stream.seek(0)
    mimetype = magic.from_buffer(content, mime=True)
    if mimetype not in ALLOWED_TYPES[extension]:
        _refuse(
            f"The content of the file ({mimetype}) does not match its "
            f".{extension} extension. Allowed extensions: {allowed}."
        )
