"""
Resource-upload type allowlist.

CKAN 2.12 `ResourceUpload` (`ckan/lib/uploader.py:279`) does no type check: the
`ckan.upload.<type>.mimetypes/types` options only reach user and group image
uploads (`Upload.verify_type`, `uploader.py` ~220-270). `get_resource_uploader`
(`uploader.py:75`) asks the `IUploader` plugins first and falls through to the
default uploader when one returns None; `package_update` (`logic/action/update.py:337`)
calls it before anything is stored, and `resource_create` and `resource_update` go
through `package_update`. (`package_create` with a file object in `resources` fails
core validation before any uploader is reached.) `check_upload` runs there, in
three steps:

1. The extension must be allowed.
2. libmagic's reading of the first 2048 bytes must agree with it, as
   `Upload.verify_type` does.
3. Some extensions get a check of the whole content, because 2048 bytes and a
   magic number do not say enough:
   - `.xml` must parse as XML from start to end, its root element must not be
     `html` (any case), and no element may be in the XHTML or SVG namespace. A
     browser that receives such a file renders it as a page and runs its
     scripts.
   - `.xlsx` must be a readable ZIP with `[Content_Types].xml` and
     `xl/workbook.xml`; `.ods` a readable ZIP whose `mimetype` member is exactly
     `application/vnd.oasis.opendocument.spreadsheet`. libmagic calls any ZIP
     `application/zip`, and that is in both lists. A plain `.zip` has no such
     check.

Every refusal of step 2 or 3 uses the same message, «The content of the file
(<detected>) does not match its .<ext> extension»; the portal matches that text.
Each check leaves the stream at 0, where the uploader starts copying.

The XML parse uses expat directly, with no tree, so memory stays bounded by the
nesting depth and not by the file size. Entities: expat >= 2.4.0 refuses
billion-laughs input itself (measured on the image, Python 3.14.7 with expat
2.8.2: «limit on input amplification factor (from DTD and entities) breached»),
and external entities are never fetched: an external reference is refused as
invalid, as `xml.etree.ElementTree` does.
"""
import os
import zipfile
from xml.parsers import expat

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
        _mismatch(mimetype, extension, allowed)
    check = _CONTENT_CHECKS.get(extension)
    if check is None:
        return
    try:
        detected = check(stream)
    finally:
        stream.seek(0)
    if detected is not None:
        _mismatch(detected, extension, allowed)


def _mismatch(detected, extension, allowed):
    _refuse(
        f"The content of the file ({detected}) does not match its "
        f".{extension} extension. Allowed extensions: {allowed}."
    )


_XHTML_NS = "http://www.w3.org/1999/xhtml"
_SVG_NS = "http://www.w3.org/2000/svg"
# Not a character an XML name or a namespace URI can contain unescaped, so the
# split below is unambiguous.
_NS_SEP = " "


class _Found(Exception):
    def __init__(self, detected):
        super().__init__(detected)
        self.detected = detected


def _xml_problem(stream):
    """What a browser would make of this XML, when that is not data: the type
    to report, or None when the file is plain XML."""
    parser = expat.ParserCreate(namespace_separator=_NS_SEP)
    first = [True]

    def start(name, _attributes):
        namespace, _, local = name.rpartition(_NS_SEP)
        if namespace == _XHTML_NS:
            raise _Found("application/xhtml+xml")
        if namespace == _SVG_NS:
            raise _Found("image/svg+xml")
        if first[0]:
            first[0] = False
            if local.lower() == "html":
                raise _Found("text/html")

    def external_entity(*_args):
        # 0 makes expat stop with an error: nothing outside the file is read.
        return 0

    parser.StartElementHandler = start
    parser.ExternalEntityRefHandler = external_entity
    try:
        parser.ParseFile(stream)
    except _Found as found:
        return found.detected
    except (expat.ExpatError, ValueError, UnicodeError):
        return "invalid XML"
    return None


def _zip_members(stream):
    """The open archive, or None when it is not a readable ZIP."""
    try:
        return zipfile.ZipFile(stream)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError, ValueError):
        return None


def _xlsx_problem(stream):
    archive = _zip_members(stream)
    if archive is None:
        return "invalid ZIP"
    with archive:
        names = set(archive.namelist())
    if {"[Content_Types].xml", "xl/workbook.xml"} <= names:
        return None
    return "application/zip"


_ODS_MIMETYPE = b"application/vnd.oasis.opendocument.spreadsheet"


def _ods_problem(stream):
    archive = _zip_members(stream)
    if archive is None:
        return "invalid ZIP"
    with archive:
        try:
            with archive.open("mimetype") as member:
                # One byte more than the expected value is enough to tell it
                # apart, and a compressed member cannot inflate past it.
                value = member.read(len(_ODS_MIMETYPE) + 1)
        except (KeyError, zipfile.BadZipFile, OSError, EOFError, ValueError,
                NotImplementedError, RuntimeError):
            return "application/zip"
    if value == _ODS_MIMETYPE:
        return None
    return "application/zip"


# Extension -> function that reads the whole stream and returns the type to
# report in the mismatch message, or None when the content is acceptable.
_CONTENT_CHECKS = {
    "xml": _xml_problem,
    "xlsx": _xlsx_problem,
    "ods": _ods_problem,
}
