"""
Serving headers of uploaded resources (`ckanext.umss.download_headers`, D1b S1).

The functional tests GET CKAN's own `<type>_resource.download` URL through the
test app and read the response headers, the way a direct `api.*` consumer gets
them. The served types are pinned bare (no `; charset`): the portal passes
CKAN's Content-Type through and previews only those exact strings.
"""
import struct
import zlib

import flask
import pytest

import ckan.plugins.toolkit as toolkit
from ckan.tests import factories, helpers

from ckanext.umss import download_headers, upload_allowlist


pytestmark = [
    pytest.mark.ckan_config("ckan.plugins", "umss"),
    pytest.mark.usefixtures("clean_db", "with_plugins"),
]

CSP = "default-src 'none'; sandbox"
ADDED = ("X-Content-Type-Options", "Content-Security-Policy")

PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


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


@pytest.fixture(params=["local", "file-keeper"])
def storage(request, ckan_config, monkeypatch, tmp_path):
    """Both ways CKAN 2.12's download view serves an upload.

    * `local`: no `resources` file-keeper storage exists, so core falls back to
      `ResourceUpload` and `flask.send_file` -- what the stack runs (its
      `ckan.ini` defines no `ckan.files.storage.*`). Files land in pytest's own
      temp directory, never in the stack's `ckan.storage_path`.
    * `file-keeper`: `test-core.ini`'s in-memory storage, served through
      `storage.as_response`.
    """
    monkeypatch.setitem(ckan_config, "ckan.storage_path", str(tmp_path))
    if request.param == "local":
        monkeypatch.setitem(
            ckan_config, "ckan.files.default_storages.resource", "umss-test-absent"
        )
    return request.param


served = pytest.mark.usefixtures("storage")


def _download_url(dataset, resource_id, filename=None):
    args = {"id": dataset["id"], "resource_id": resource_id}
    if filename is not None:
        args["filename"] = filename
    return toolkit.url_for(f"{dataset['type']}_resource.download", **args)


def _get(app, create_with_upload, data, filename):
    dataset = factories.Dataset()
    resource = create_with_upload(data, filename, package_id=dataset["id"])
    assert resource["url_type"] == "upload"
    return app.get(_download_url(dataset, resource["id"], filename))


@served
def test_pdf_is_inline_without_csp(app, create_with_upload):
    response = _get(app, create_with_upload, PDF, "doc.pdf")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/pdf"
    assert response.headers["Content-Disposition"] == "inline; filename=doc.pdf"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" not in response.headers
    assert response.data == PDF


@served
def test_png_is_inline_with_csp(app, create_with_upload):
    response = _get(app, create_with_upload, _png(), "img.png")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/png"
    assert response.headers["Content-Disposition"] == "inline; filename=img.png"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"] == CSP


@served
def test_csv_is_an_attachment_with_csp(app, create_with_upload):
    response = _get(app, create_with_upload, "a,b\n1,2\n", "data.csv")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/csv"
    assert response.headers["Content-Disposition"] == "attachment; filename=data.csv"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"] == CSP
    assert response.data == b"a,b\n1,2\n"


@served
def test_download_without_filename_in_the_url(app, create_with_upload):
    dataset = factories.Dataset()
    resource = create_with_upload("a,b\n1,2\n", "data.csv", package_id=dataset["id"])
    response = app.get(_download_url(dataset, resource["id"]))
    rid = resource["id"]
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/csv"
    # The served filename is the stored one, never CKAN's storage location.
    assert rid
    assert response.headers["Content-Disposition"] == "attachment; filename=data.csv"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"] == CSP


@served
def test_stored_mimetype_never_wins(app, create_with_upload):
    dataset = factories.Dataset()
    resource = create_with_upload("a,b\n1,2\n", "data.csv", package_id=dataset["id"])
    patched = helpers.call_action("resource_patch", id=resource["id"], mimetype="text/html")
    assert patched["mimetype"] == "text/html"
    response = app.get(_download_url(dataset, resource["id"], "data.csv"))
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/csv"
    assert response.headers["Content-Disposition"] == "attachment; filename=data.csv"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"] == CSP


@served
def test_a_requested_filename_does_not_choose_the_type(app, create_with_upload):
    # Neither the type nor the saved name follows the name typed in the URL
    # (a csv must not be saved as page.html).
    dataset = factories.Dataset()
    resource = create_with_upload("a,b\n1,2\n", "data.csv", package_id=dataset["id"])
    response = app.get(_download_url(dataset, resource["id"], "page.html"))
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/csv"
    assert response.headers["Content-Disposition"] == "attachment; filename=data.csv"
    assert response.headers["Content-Security-Policy"] == CSP


@served
def test_revalidation_304_keeps_the_headers(app, create_with_upload):
    # Browsers revalidate (CKAN sends must-revalidate) and copy a 304's
    # Content-Disposition into their cached copy. The test app's storages send
    # no validators, so CKAN's 304 is built by hand and run through the hook
    # in a real request context for the download URL.
    dataset = factories.Dataset()
    resource = create_with_upload("a,b\n1,2\n", "data.csv", package_id=dataset["id"])
    url = _download_url(dataset, resource["id"], "data.csv")
    upstream = flask.Response(
        status=304, headers={"Content-Disposition": "inline; filename=data.csv"}
    )
    with app.flask_app.test_request_context(url):
        response = download_headers.after_request(upstream)
    assert response.status_code == 304
    assert response.headers["Content-Disposition"] == "attachment; filename=data.csv"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"] == CSP


@served
@pytest.mark.parametrize(
    "stored, expected",
    [
        (
            "informe-ñandú-€.csv",
            "attachment; filename=informe-nandu-.csv; "
            "filename*=UTF-8''informe-%C3%B1and%C3%BA-%E2%82%AC.csv",
        ),
        ("a\r\nb.csv", "attachment; filename=ab.csv"),
    ],
)
def test_unusual_stored_name_still_downloads(app, create_with_upload, stored, expected):
    # An editor can patch an upload's `url` to any text: the header must stay
    # sendable (latin-1, no line breaks), as CKAN's own send_file made it.
    dataset = factories.Dataset()
    resource = create_with_upload("a,b\n1,2\n", "data.csv", package_id=dataset["id"])
    helpers.call_action("resource_patch", id=resource["id"], url=stored)
    response = app.get(_download_url(dataset, resource["id"]))
    assert response.status_code == 200
    assert response.headers["Content-Disposition"] == expected
    assert response.headers["Content-Type"] == "text/csv"
    assert response.headers["Content-Security-Policy"] == CSP


@served
def test_upload_outside_the_allowlist_is_an_octet_stream(app, create_with_upload):
    # Stands in for a file stored before PR #36: the stored name is moved to an
    # extension the allowlist no longer accepts.
    dataset = factories.Dataset()
    resource = create_with_upload("a,b\n1,2\n", "data.csv", package_id=dataset["id"])
    patched = helpers.call_action("resource_patch", id=resource["id"], url="page.html")
    assert patched["url_type"] == "upload"
    response = app.get(_download_url(dataset, resource["id"], "page.html"))
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/octet-stream"
    assert response.headers["Content-Disposition"] == "attachment; filename=page.html"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"] == CSP


@served
def test_link_resource_redirect_is_untouched(app):
    dataset = factories.Dataset()
    resource = helpers.call_action(
        "resource_create", package_id=dataset["id"], url="https://example.org/run.sh"
    )
    response = app.get(_download_url(dataset, resource["id"]), follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"] == "https://example.org/run.sh"
    for name in ADDED + ("Content-Disposition",):
        assert name not in response.headers


@served
def test_missing_resource_404_is_untouched(app):
    dataset = factories.Dataset()
    response = app.get(_download_url(dataset, "does-not-exist"))
    assert response.status_code == 404
    for name in ADDED + ("Content-Disposition",):
        assert name not in response.headers


# --- the decision itself ------------------------------------------------------


def test_served_types_cover_exactly_the_allowlist():
    assert set(download_headers.SERVED_TYPES) == set(upload_allowlist.ALLOWED_TYPES)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("data.csv", ("text/csv", "attachment", CSP)),
        ("data.tsv", ("text/tab-separated-values", "attachment", CSP)),
        ("notes.txt", ("text/plain", "attachment", CSP)),
        ("data.json", ("application/json", "attachment", CSP)),
        ("map.geojson", ("application/geo+json", "attachment", CSP)),
        ("data.xml", ("application/xml", "attachment", CSP)),
        (
            "book.xlsx",
            (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "attachment",
                CSP,
            ),
        ),
        ("book.xls", ("application/vnd.ms-excel", "attachment", CSP)),
        ("book.ods", ("application/vnd.oasis.opendocument.spreadsheet", "attachment", CSP)),
        ("doc.pdf", ("application/pdf", "inline", None)),
        ("img.png", ("image/png", "inline", CSP)),
        ("img.jpg", ("image/jpeg", "inline", CSP)),
        ("img.jpeg", ("image/jpeg", "inline", CSP)),
        ("IMG.JPEG", ("image/jpeg", "inline", CSP)),
        ("bundle.zip", ("application/zip", "attachment", CSP)),
        ("run.sh", ("application/octet-stream", "attachment", CSP)),
        ("page.html", ("application/octet-stream", "attachment", CSP)),
        ("README", ("application/octet-stream", "attachment", CSP)),
        ("", ("application/octet-stream", "attachment", CSP)),
    ],
)
def test_decide(name, expected):
    assert download_headers.decide(name) == expected
