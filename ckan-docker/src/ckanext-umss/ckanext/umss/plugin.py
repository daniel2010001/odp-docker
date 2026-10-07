import datetime

import ckan.model as ckan_model
import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
import sqlalchemy as sa

from ckanext.umss import auth
from ckanext.umss import model as umss_model
from ckanext.umss.logic.action import publication as publication_actions
from ckanext.umss.logic.auth import publication as publication_auth


class UmssPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IAuthFunctions)
    plugins.implements(plugins.IActions)
    plugins.implements(plugins.IPackageController, inherit=True)

    # IConfigurer

    def update_config(self, config_):
        toolkit.add_template_directory(config_, "templates")
        toolkit.add_public_directory(config_, "public")
        toolkit.add_resource("assets", "umss")

    # IActions

    def get_actions(self):
        """The queue and the door (D4). The door is the only way a dataset
        becomes public, because the wall in `ckanext.umss.auth` refuses every
        other flip.
        """
        return {
            "publication_request_create": publication_actions.publication_request_create,
            "publication_request_cancel": publication_actions.publication_request_cancel,
            "publication_request_decide": publication_actions.publication_request_decide,
            "publication_publish": publication_actions.publication_publish,
            "publication_request_list": publication_actions.publication_request_list,
        }

    # IAuthFunctions

    def get_auth_functions(self):
        """Two different mechanisms, deliberately in one place:

        * the two **chained** functions that guard core's action names — the
          wall — whose rule and measurements live in `ckanext.umss.auth`;
        * the five plain functions that authorize this extension's own actions
          (D4).

        CKAN raises `ValueError('Authorization function not found: ...')` for an
        action with no auth function (`ckan/authz.py:235-254`), so these are not
        optional decoration on `get_actions` above.
        """
        return {
            "package_update": auth.package_update,
            "package_create": auth.package_create,
            "publication_request_create": publication_auth.publication_request_create,
            "publication_request_cancel": publication_auth.publication_request_cancel,
            "publication_request_decide": publication_auth.publication_request_decide,
            "publication_publish": publication_auth.publication_publish,
            "publication_request_list": publication_auth.publication_request_list,
        }

    # IPackageController

    def after_dataset_delete(self, context, data_dict):
        """A2.7's deleted-object trigger: a `pending` request for a dataset
        that is being deleted is annulled in the same session as the deletion.

        `package_delete` invokes this hook before `entity.delete()` and commits
        once at the end (`ckan/logic/action/delete.py`), so the annulment and
        the deletion exist together or not at all. The dataset id is still
        resolvable here, which is why the raw `data_dict["id"]` (a name is
        legal input) is resolved to the canonical id before it is matched
        against `dataset_id`.

        `dataset_purge` and `bulk_update_delete` do **not** call this hook,
        named here so neither can travel silently:

        * `dataset_purge` purges directly, so a purge of a dataset with a
          `pending` request leaves the row behind.
        * `bulk_update_delete` soft-deletes through
          `_bulk_update_dataset(..., {'state': 'deleted'})`, which loops
          `package_patch` -> `package_update`; the core interface's own
          docstring warns that this callback is bypassed. This is the more
          reachable of the two: the umss wall lets an organization
          administrator — the very caller who owns the decision queue — request
          the `state` change, so a `pending` row can survive a delete performed
          by that admin and stay decidable (the dataset still resolves and
          `owner_org` is intact). That path is the wall's business under the
          spec's `No Other Visibility Path` requirement — no API-reachable
          action may change a dataset's `private` **or** its `state` — and is a
          later unit, not this cut.

        This hook covers the ordinary `package_delete` path the requirement
        names.
        """
        dataset_id = (data_dict or {}).get("id")
        if not dataset_id:
            return
        package = ckan_model.Package.get(dataset_id)
        if package is not None:
            dataset_id = package.id

        # The store table is this extension's migration's. When it is absent
        # (the plugin loaded but the migration not applied — the state
        # `clean_db` leaves before `migrate_db_for` runs) there can be no
        # pending row, and the ordinary delete must not fail because of it. A
        # blind query would abort `package_delete`'s transaction, not merely
        # raise, so existence is checked before the query.
        if not sa.inspect(ckan_model.Session.bind).has_table("publication_requests"):
            return

        row = (
            ckan_model.Session.query(umss_model.PublicationRequest)
            .filter(umss_model.PublicationRequest.dataset_id == dataset_id)
            .filter(umss_model.PublicationRequest.status == umss_model.PENDING)
            .first()
        )
        if row is None:
            return
        row.status = umss_model.ANNULLED
        row.motive = umss_model.MOTIVE_DATASET_DELETED
        row.decided_at = datetime.datetime.now()

