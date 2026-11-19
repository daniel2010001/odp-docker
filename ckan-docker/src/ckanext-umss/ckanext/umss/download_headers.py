"""
Serving headers of uploaded resources (D1b S1).

CKAN 2.12 serves an upload from `ckan.views.resource.download` with
`flask.send_file` (or `storage.as_response` for a file-keeper storage) and then
overwrites Content-Type with the resource's stored `mimetype` field, which any
editor can set. This module rewrites the headers of that response afterwards,
from the stored file's extension only:

* Content-Type is one canonical type per allowlist key, bare (no `; charset`):
  the portal passes it through and previews only exact bare types;
* `X-Content-Type-Options: nosniff` always;
* pdf, png, jpg and jpeg are `inline`, everything else `attachment`, with the
  stored file name (never the name typed in the URL, so a csv is not saved as
  `evil.html`);
* every type except pdf gets a sandbox CSP (a sandbox CSP breaks Chrome's PDF
  viewer);
* an extension outside the allowlist (files stored before PR #36) is served as
  `application/octet-stream`, attachment, with the CSP.

Only a 2xx or 304 answer of the download view for a `url_type=upload`
resource is touched (browsers revalidate, since CKAN sends must-revalidate,
and copy a 304's Content-Disposition into their cached copy); redirects to link
resources and error pages pass through as they are.
"""
import os
import unicodedata
import urllib.parse

from flask import current_app, request

import ckan.model as model
from ckan.views import resource as resource_views


# One served type per `upload_allowlist.ALLOWED_TYPES` key (a test pins the
# key sets equal).
SERVED_TYPES = {
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "txt": "text/plain",
    "json": "application/json",
    "geojson": "application/geo+json",
    "xml": "application/xml",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "zip": "application/zip",
}
FALLBACK_TYPE = "application/octet-stream"
INLINE = frozenset({"pdf", "png", "jpg", "jpeg"})
WITHOUT_CSP = frozenset({"pdf"})
CSP = "default-src 'none'; sandbox"


def _extension(name):
    return os.path.splitext(name or "")[1][1:].lower()


def decide(name):
    """`(content_type, disposition, csp_or_None)` for a stored file name."""
    extension = _extension(name)
    if extension not in SERVED_TYPES:
        return FALLBACK_TYPE, "attachment", CSP
    disposition = "inline" if extension in INLINE else "attachment"
    csp = None if extension in WITHOUT_CSP else CSP
    return SERVED_TYPES[extension], disposition, csp


def _stored_name(resource):
    # An upload's `url` holds the munged file name the allowlist checked (core
    # only prefixes it with the download route when dictizing).
    return (resource.url or "").rstrip("/").rsplit("/", 1)[-1]


def _filename_params(stored_name):
    """`filename` (plus `filename*` for non-ASCII) the way werkzeug's
    `send_file` builds them, so the header stays sendable: an editor can patch
    an upload's `url` to any text, line breaks and non-latin-1 included."""
    name = "".join(ch for ch in stored_name if ch.isprintable()) or "download"
    try:
        name.encode("ascii")
    except UnicodeEncodeError:
        simple = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
        quoted = urllib.parse.quote(name, safe="!#$&+^`|~")
        return {"filename": simple or "download", "filename*": f"UTF-8''{quoted}"}
    return {"filename": name}


def apply(response, stored_name):
    content_type, disposition, csp = decide(stored_name)
    response.headers["Content-Type"] = content_type
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers.set("Content-Disposition", disposition, **_filename_params(stored_name))
    if csp:
        response.headers["Content-Security-Policy"] = csp
    return response


def _is_download():
    endpoint = request.endpoint
    if endpoint is None:
        return False
    # Every `<type>_resource.download` (and the legacy `resource.download`)
    # resolves to core's `download` view. Matched by module and name, not by
    # identity: the test app re-imports `ckan.views.resource` (measured), so
    # the function object registered can differ from the one imported here.
    view = current_app.view_functions.get(endpoint)
    return (
        getattr(view, "__module__", None) == resource_views.download.__module__
        and getattr(view, "__name__", None) == resource_views.download.__name__
    )


def after_request(response):
    status = response.status_code
    if not (200 <= status < 300 or status == 304) or not _is_download():
        return response
    resource_id = (request.view_args or {}).get("resource_id")
    resource = model.Resource.get(resource_id) if resource_id else None
    if resource is None or resource.url_type != "upload":
        return response
    return apply(response, _stored_name(resource))


def install(app):
    """Register the hook on CKAN's Flask app (`IMiddleware.make_middleware`)."""
    app.after_request(after_request)
    return app
