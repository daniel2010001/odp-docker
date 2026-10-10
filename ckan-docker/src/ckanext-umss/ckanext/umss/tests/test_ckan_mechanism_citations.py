"""Verify that the CKAN mechanism citations this extension quotes still exist.

Several docstrings in ``ckanext.umss`` explain the wall by citing code this
repository does **not** own: CKAN core, plus a couple of scripts that live in
the image. A line number is a promise nobody keeps — on a CKAN bump the lines
shift, the citation points at another construct, and the prose keeps the look of
rigor while it has stopped being true. So each citation names the CKAN file and
quotes a short fragment of the code that carries its claim.

This guard resolves the CKAN source the suite actually imports
(``Path(ckan.__file__).resolve().parent``, never a hardcoded container path) and
asserts that every quoted fragment is still in it. Inside the CKAN container
``ckan.__file__`` is ``/srv/app/src/ckan/ckan/__init__.py``, so the tree read
here is the same tree CI executes.

Declared limit, stated plainly: the pairing between a docstring and a row of
``CITATIONS`` is **manual**. What this guard protects is that the quoted
fragments still exist in the CKAN that runs; it does **not** prove the argument
around them, and it cannot notice a fragment that still exists but no longer
means what the prose says it means. The pairing has to be maintained by hand
when a citation is rewritten.

Two consequences of checking presence, named so they are not mistaken for
coverage. A fragment that is **not unique** in its file only proves that *some*
occurrence survives: ``model.repo.commit()`` appears five times in
``logic/action/update.py`` and
``authorized = authz.has_user_permission_for_group_or_org(`` six times in
``logic/auth/update.py``, so the intended one can go while an identical line
elsewhere keeps this green. And the guard reads this table, never the docstrings,
so a table fragment and the prose fragment can diverge without either side
failing. That channel is partly closed by
``test_every_row_is_quoted_by_the_file_it_names``, which asserts the fragment is
quoted by the file each row names -- compared with whitespace collapsed, because
the docstrings are wrapped at 80 columns and a long fragment straddles a line
break, which is also why the two sides cannot be compared character for
character. That test cannot tell *which* docstring in the named file quotes it,
and the file is where its reach stops.
"""

import re
from collections import namedtuple
from pathlib import Path

import ckan
import pytest


# The imported package IS the source under test: resolving through
# ``ckan.__file__`` is what makes the guard read the same bytes the suite runs,
# instead of binding it to one image's layout. This is the ``ckan`` package
# directory itself (``/srv/app/src/ckan/ckan`` inside the container).
CKAN_SOURCE_ROOT = Path(ckan.__file__).resolve().parent


Citation = namedtuple("Citation", "claim path fragment docstring")


def _source_file(citation):
    # Docstrings cite CKAN from its source root (``ckan/logic/action/update.py``);
    # the first component of that path is the imported package itself, so it is
    # resolved away here rather than against a checkout path.
    return CKAN_SOURCE_ROOT / Path(citation.path).relative_to("ckan")


# One row per claim, grouped in the order the docstrings make them. The fragment
# is copied verbatim from the CKAN source and quoted back by ``docstring``; the
# two are a manual pair, which is the limit this guard declares.
CITATIONS = [
    Citation(
        claim="ignore_not_package_admin reaches package_change_state",
        path="ckan/logic/validators.py",
        fragment="logic.check_access('package_change_state',context, {\"id\": pkg.id})",
        docstring=(
            "ckanext/umss/auth.py module docstring, the `package_change_state` "
            "paragraph"
        ),
    ),
    Citation(
        claim="the create schema keeps private in the ignore_missing chain",
        path="ckan/logic/schema/__init__.py",
        fragment="'private': [ignore_missing, boolean_validator,",
        docstring="ckanext/umss/auth.py, package_create docstring",
    ),
    Citation(
        claim="the private column defaults to False",
        path="ckan/model/package.py",
        fragment="Column('private', types.Boolean, default=False)",
        docstring="ckanext/umss/auth.py, package_create docstring",
    ),
    Citation(
        claim="_bulk_update_dataset loops package_patch",
        path="ckan/logic/action/update.py",
        fragment="_get_action('package_patch')(",
        docstring="ckanext/umss/auth.py, bulk_update_public docstring",
    ),
    Citation(
        claim="core's auth for bulk_update_public checks the update permission",
        path="ckan/logic/auth/update.py",
        fragment="authorized = authz.has_user_permission_for_group_or_org(",
        docstring="ckanext/umss/auth.py, bulk_update_public docstring",
    ),
    Citation(
        claim="package_patch delegates to package_update",
        path="ckan/logic/action/patch.py",
        fragment="_get_action('package_update')(update_context, patched)",
        docstring="ckanext/umss/logic/action/publication.py module docstring",
    ),
    Citation(
        claim="package_update is the action package_patch delegates to",
        path="ckan/logic/action/update.py",
        fragment="def package_update(",
        docstring="ckanext/umss/logic/action/publication.py module docstring",
    ),
    Citation(
        claim="package_update commits the session",
        path="ckan/logic/action/update.py",
        fragment="model.repo.commit()",
        docstring="ckanext/umss/logic/action/publication.py module docstring",
    ),
    Citation(
        claim="repo.commit is the session's commit",
        path="ckan/model/__init__.py",
        fragment="self.commit = session.commit",
        docstring="ckanext/umss/logic/action/publication.py module docstring",
    ),
    Citation(
        claim="repo is built over the shared session",
        path="ckan/model/__init__.py",
        fragment="repo = Repository(meta.metadata, meta.Session)",
        docstring="ckanext/umss/logic/action/publication.py module docstring",
    ),
    Citation(
        claim="_prepopulate_context gives the action the shared session",
        path="ckan/logic/__init__.py",
        fragment="context.setdefault('session', model.Session)",
        docstring="ckanext/umss/logic/action/publication.py module docstring",
    ),
    Citation(
        claim="package_patch commits the session it is handed",
        path="ckan/logic/action/update.py",
        fragment="model.repo.commit()",
        docstring="ckanext/umss/logic/action/publication.py, _commit_row docstring",
    ),
    Citation(
        claim="has_user_permission_for_group_or_org walks the org hierarchy",
        path="ckan/authz.py",
        fragment="def has_user_permission_for_group_or_org(",
        docstring=(
            "ckanext/umss/logic/action/publication.py, publication_request_list "
            "docstring"
        ),
    ),
    Citation(
        claim="the stock capacity reused by the publication auth",
        path="ckan/authz.py",
        fragment="def has_user_permission_for_group_or_org(",
        docstring="ckanext/umss/logic/auth/publication.py module docstring",
    ),
    Citation(
        claim="authz short-circuits a sysadmin unless auth_sysadmins_check is set",
        path="ckan/authz.py",
        fragment="if not getattr(auth_function, 'auth_sysadmins_check', False):",
        docstring=(
            "ckanext/umss/logic/auth/publication.py, publication_request_decide "
            "docstring"
        ),
    ),
]


def _failure(citation, problem):
    return (
        "CKAN mechanism citation is stale: %s\n"
        "  claim:     %s\n"
        "  CKAN file: %s\n"
        "  fragment:  %s\n"
        "  quoted by: %s\n"
        "  source:    %s\n"
        "The fragment has to be copied verbatim from the CKAN that runs, and the "
        "docstring and this table updated together." % (
            problem,
            citation.claim,
            citation.path,
            citation.fragment,
            citation.docstring,
            CKAN_SOURCE_ROOT,
        )
    )


@pytest.mark.parametrize(
    "citation", CITATIONS, ids=[citation.claim for citation in CITATIONS]
)
def test_the_quoted_ckan_fragment_still_exists(citation):
    source = _source_file(citation)
    assert source.is_file(), _failure(citation, "the CKAN file no longer exists")

    text = source.read_text(encoding="utf-8")
    assert citation.fragment in text, _failure(citation, "the quoted fragment is gone")


def test_the_citation_paths_are_ckan_relative():
    """A path is resolved against the imported package, not a container layout.

    The first component has to be ``ckan`` — the package the docstrings cite —
    so it can be stripped and the rest resolved inside the imported package. An
    absolute path or a ``..`` would let the guard read source the suite never
    runs. None of the three is allowed here.
    """
    for citation in CITATIONS:
        path = Path(citation.path)
        assert not path.is_absolute(), _failure(citation, "path is absolute")
        assert ".." not in path.parts, _failure(citation, "path climbs out of CKAN")
        assert citation.path.startswith("ckan/"), _failure(
            citation, "path is not under ckan/"
        )


# The extension's own root. The ``docstring`` field names a file relative to it
# (``ckanext/umss/auth.py``), the way the container path reads.
EXTENSION_ROOT = Path(__file__).resolve().parents[3]


def _quoting_file(citation):
    # The field is prose: ``ckanext/umss/auth.py, package_create docstring`` or
    # ``ckanext/umss/auth.py module docstring, the ... paragraph``. The path is
    # its leading token, stopping at ``.py`` -- splitting on whitespace leaves a
    # trailing comma attached and the lookup then fails on a path that exists.
    matched = re.match(r"(\S+\.py)", citation.docstring)
    assert matched, "a row names no ``.py`` file as quoting it: %s" % citation.docstring
    return EXTENSION_ROOT / matched.group(1)


def _collapse_whitespace(text):
    # The docstrings are wrapped at 80 columns, so a fragment long enough to be
    # quoted across a line break is not a contiguous substring of the file. Both
    # sides are collapsed here so the comparison survives the wrap.
    return re.sub(r"\s+", " ", text).strip()


def test_every_row_is_quoted_by_the_file_it_names():
    """The fragment has to be the text the docstring actually quotes.

    Checking the CKAN side alone leaves the pair free to diverge: this guard
    reads the table and never the prose, so a row can agree with CKAN while the
    docstring beside it says something else. This asserts the other half -- the
    fragment appears in the file the row names -- which is what caught a row
    whose fragment carried a trailing comma that the prose did not have.
    """
    for citation in CITATIONS:
        quoting = _quoting_file(citation)
        assert quoting.is_file(), _failure(
            citation, "the file this row names as quoting it does not exist: %s" % quoting
        )
        prose = _collapse_whitespace(quoting.read_text(encoding="utf-8"))
        assert _collapse_whitespace(citation.fragment) in prose, _failure(
            citation, "the fragment is not quoted by the file this row names"
        )
