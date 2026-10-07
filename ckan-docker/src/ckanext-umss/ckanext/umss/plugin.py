import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit

from ckanext.umss import auth
from ckanext.umss.logic.action import publication as publication_actions
from ckanext.umss.logic.auth import publication as publication_auth


class UmssPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IAuthFunctions)
    plugins.implements(plugins.IActions)

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

