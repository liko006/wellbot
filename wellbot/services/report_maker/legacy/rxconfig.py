import sys
import os
import reflex as rx

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "ppt_agent"))


config = rx.Config(
    app_name="app_main",
    app_module_import="app_main",
    plugins=[
        rx.plugins.TailwindV4Plugin(),
    ],
)