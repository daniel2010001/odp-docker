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

import ckan.plugins.toolkit as toolkit
from ckan.tests import factories, helpers


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
