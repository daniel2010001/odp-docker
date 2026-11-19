"""
Resource-upload type allowlist (`ckanext.umss.upload_allowlist`).

Everything goes through the public actions. `create_with_upload` builds the
`FileStorage` CKAN's own uploader receives, so a refusal here is the one a
publisher gets. "Nothing stored" is read back through `package_show`.
"""
import io
import struct
import zlib
import zipfile

import pytest
from werkzeug.datastructures import FileStorage

import ckan.plugins.toolkit as toolkit
from ckan.tests import factories, helpers

from ckanext.umss.upload_allowlist import check_upload


pytestmark = [
    pytest.mark.ckan_config("ckan.plugins", "umss"),
    pytest.mark.usefixtures("clean_db", "with_plugins"),
]

ALLOWED = (
    "csv, tsv, xlsx, xls, ods, json, geojson, xml, txt, pdf, png, jpg, jpeg, zip"
)


def _png():
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixel = zlib.compress(b"\x00\xff\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixel)
        + chunk(b"IEND", b"")
    )


def _jpeg():
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


def _zip(*names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in names:
            z.writestr(name, "x")
    return buf.getvalue()


def _xlsx():
    return _zip("[Content_Types].xml", "xl/workbook.xml")


ODS_MIMETYPE = "application/vnd.oasis.opendocument.spreadsheet"


def _ods(mimetype=ODS_MIMETYPE):
    # `mimetype` first and stored, as ODF requires; that is what libmagic reads.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", mimetype)
        z.writestr("content.xml", "<office:document-content/>")
    return buf.getvalue()


PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
GEOJSON = '{"type": "FeatureCollection", "features": []}'

CASES = {
    "data.csv": "a,b\n1,2\n3,4\n",
    "data.tsv": "a\tb\n1\t2\n",
    "data.json": '{"a": 1}',
    "data.geojson": GEOJSON,
    "data.txt": "hello world\n",
    "data.xml": '<?xml version="1.0"?><root><a>1</a></root>',
    "doc.pdf": PDF,
    "img.png": _png(),
    "img.jpg": _jpeg(),
    "img.jpeg": _jpeg(),
    "book.xlsx": _xlsx(),
    "sheet.ods": _ods(),
    "bundle.zip": _zip("a.txt"),
}


def _count(dataset):
    return len(helpers.call_action("package_show", id=dataset["id"])["resources"])


def _upload(create_with_upload, data, filename, dataset):
    return create_with_upload(data, filename, package_id=dataset["id"])


@pytest.mark.parametrize("filename", sorted(CASES))
def test_allowed_kinds_are_stored(create_with_upload, filename):
    dataset = factories.Dataset()
    resource = _upload(create_with_upload, CASES[filename], filename, dataset)
    assert resource["url_type"] == "upload"
    assert _count(dataset) == 1


def test_uppercase_extension_is_accepted(create_with_upload):
    dataset = factories.Dataset()
    resource = _upload(create_with_upload, "a,b\n1,2\n", "DATA.CSV", dataset)
    assert resource["url_type"] == "upload"


def test_shell_script_is_refused_and_nothing_is_stored(create_with_upload):
    dataset = factories.Dataset()
    with pytest.raises(toolkit.ValidationError) as err:
        _upload(create_with_upload, "#!/bin/sh\necho hi\n", "run.sh", dataset)
    assert err.value.error_dict == {
        "upload": [
            f"File type .sh is not allowed. Allowed extensions: {ALLOWED}."
        ]
    }
    assert _count(dataset) == 0


def test_script_renamed_to_csv_is_refused(create_with_upload):
    dataset = factories.Dataset()
    with pytest.raises(toolkit.ValidationError) as err:
        _upload(create_with_upload, "#!/bin/sh\necho hi\n", "data.csv", dataset)
    assert err.value.error_dict == {
        "upload": [
            "The content of the file (text/x-shellscript) does not match its "
            f".csv extension. Allowed extensions: {ALLOWED}."
        ]
    }
    assert _count(dataset) == 0


def test_file_without_extension_is_refused(create_with_upload):
    dataset = factories.Dataset()
    with pytest.raises(toolkit.ValidationError) as err:
        _upload(create_with_upload, "a,b\n1,2\n", "README", dataset)
    assert err.value.error_dict == {
        "upload": [
            f"File type (none) is not allowed. Allowed extensions: {ALLOWED}."
        ]
    }
    assert _count(dataset) == 0


def test_link_resource_is_unaffected():
    dataset = factories.Dataset()
    resource = helpers.call_action(
        "resource_create",
        package_id=dataset["id"],
        url="https://example.org/run.sh",
    )
    assert resource["url"] == "https://example.org/run.sh"
    assert _count(dataset) == 1


def test_metadata_update_of_an_existing_upload_is_unaffected(create_with_upload):
    dataset = factories.Dataset()
    resource = _upload(create_with_upload, "a,b\n1,2\n", "data.csv", dataset)
    updated = helpers.call_action(
        "resource_update", id=resource["id"], url=resource["url"], name="renamed"
    )
    assert updated["name"] == "renamed"
    assert updated["url_type"] == "upload"
    assert _count(dataset) == 1


# --- check_upload content checks (S2 xml, S3 zip containers, S4 message) ---

XHTML = "http://www.w3.org/1999/xhtml"
SVG = "http://www.w3.org/2000/svg"
# Pushes what follows past libmagic's 2048-byte window, so only the full parse
# can see it.
PADDING = "<row><a>1</a><b>2</b></row>" * 120


def _storage(data, filename):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return FileStorage(stream=io.BytesIO(data), filename=filename)


def _mismatch(detected, extension):
    return {
        "upload": [
            f"The content of the file ({detected}) does not match its "
            f".{extension} extension. Allowed extensions: {ALLOWED}."
        ]
    }


def _refused(data, filename):
    with pytest.raises(toolkit.ValidationError) as err:
        check_upload(_storage(data, filename))
    return err.value.error_dict


def _passes(data, filename):
    # The content checks read the whole stream; the uploader then copies it
    # from wherever it was left.
    upload = _storage(data, filename)
    check_upload(upload)
    return upload.stream.tell()


REFUSED = {
    "xhtml document": (
        '<?xml version="1.0"?><html xmlns="%s"><body><script>alert(1)</script>'
        "</body></html>" % XHTML,
        "data.xml",
        "application/xhtml+xml",
    ),
    "html root without namespace": (
        '<?xml version="1.0"?><html><body>x</body></html>',
        "data.xml",
        "text/html",
    ),
    "HTML root in capitals": (
        '<?xml version="1.0"?><HTML><body>x</body></HTML>',
        "data.xml",
        "text/html",
    ),
    "xhtml script deep inside": (
        '<?xml version="1.0"?><root>%s<a><b><x:script xmlns:x="%s">alert(1)'
        "</x:script></b></a></root>" % (PADDING, XHTML),
        "data.xml",
        "application/xhtml+xml",
    ),
    "svg element inside": (
        '<?xml version="1.0"?><root>%s<a><svg xmlns="%s"><script>alert(1)'
        "</script></svg></a></root>" % (PADDING, SVG),
        "data.xml",
        "image/svg+xml",
    ),
    "malformed xml": (
        '<?xml version="1.0"?><root><a>1</root>',
        "data.xml",
        "invalid XML",
    ),
    "malformed after the sniff window": (
        '<?xml version="1.0"?><root>%s<a>1</root>' % PADDING,
        "data.xml",
        "invalid XML",
    ),
    "entity expansion bomb": (
        '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a0 "aaaaaaaaaa">'
        + "".join(
            '<!ENTITY a%d "%s">' % (i, ("&a%d;" % (i - 1)) * 10) for i in range(1, 10)
        )
        + "]><l>&a9;</l>",
        "data.xml",
        "invalid XML",
    ),
    "bare zip as xlsx": (_zip("a.txt"), "book.xlsx", "application/zip"),
    "xlsx without workbook": (
        _zip("[Content_Types].xml", "xl/other.xml"),
        "book.xlsx",
        "application/zip",
    ),
    "bare zip as ods": (_zip("a.txt"), "sheet.ods", "application/zip"),
    "ods with the wrong mimetype": (
        _ods("application/x-foo"),
        "sheet.ods",
        "application/zip",
    ),
    "ods mimetype with a trailing newline": (
        _ods(ODS_MIMETYPE + "\n"),
        "sheet.ods",
        "application/zip",
    ),
    "truncated zip as xlsx": (_xlsx()[:-30], "book.xlsx", "invalid ZIP"),
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_content_check_refuses(case):
    data, filename, detected = REFUSED[case]
    extension = filename.rsplit(".", 1)[1]
    assert _refused(data, filename) == _mismatch(detected, extension)


def _openpyxl_book():
    openpyxl = pytest.importorskip("openpyxl")
    buf = io.BytesIO()
    openpyxl.Workbook().save(buf)
    return buf.getvalue()


PASSES = {
    "data xml": (
        '<?xml version="1.0"?><root xmlns:d="urn:example:data">%s<d:item>1</d:item>'
        "</root>" % PADDING,
        "data.xml",
    ),
    "xml with an html element in another namespace": (
        '<?xml version="1.0"?><root><html xmlns="urn:example:other">x</html></root>',
        "data.xml",
    ),
    "xlsx members": (_xlsx(), "book.xlsx"),
    "ods mimetype": (_ods(), "sheet.ods"),
    "plain zip": (_zip("a.txt"), "bundle.zip"),
    "csv": ("a,b\n1,2\n", "data.csv"),
}


@pytest.mark.parametrize("case", sorted(PASSES))
def test_content_check_passes_and_rewinds(case):
    data, filename = PASSES[case]
    assert _passes(data, filename) == 0


def test_openpyxl_workbook_passes_and_rewinds():
    assert _passes(_openpyxl_book(), "book.xlsx") == 0


def test_xhtml_upload_is_refused_and_nothing_is_stored(create_with_upload):
    dataset = factories.Dataset()
    data, filename, detected = REFUSED["xhtml script deep inside"]
    with pytest.raises(toolkit.ValidationError) as err:
        _upload(create_with_upload, data, filename, dataset)
    assert err.value.error_dict == _mismatch(detected, "xml")
    assert _count(dataset) == 0
