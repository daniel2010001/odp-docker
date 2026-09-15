import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit

from ckanext.umss import auth


class UmssPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IAuthFunctions)

    # IConfigurer

    def update_config(self, config_):
        toolkit.add_template_directory(config_, "templates")
        toolkit.add_public_directory(config_, "public")
        toolkit.add_resource("assets", "umss")

    # IAuthFunctions

    def get_auth_functions(self):
        """Chain the publication rule onto core for the two action names that
        carry it. The rule itself, and the measurements behind it, are in
        `ckanext.umss.auth`.
        """
        return {
            "package_update": auth.package_update,
            "package_create": auth.package_create,
        }

