import reflex as rx
from reflex.plugins.sitemap import SitemapPlugin

from wellbot.styles import THEME

config = rx.Config(
    app_name="wellbot",
    show_built_with_reflex=False,
    plugins=[
        SitemapPlugin(),
        # 0.9: 테마는 App(theme=) 대신 RadixThemesPlugin 으로 설정(암묵 Radix 활성 deprecated)
        rx.plugins.RadixThemesPlugin(theme=THEME),
    ],
)
