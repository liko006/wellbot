import reflex as rx
from reflex.plugins.sitemap import SitemapPlugin

config = rx.Config(
    app_name="wellbot",
    show_built_with_reflex=False,
    plugins=[SitemapPlugin()],
    state_auto_setters=True,
)
