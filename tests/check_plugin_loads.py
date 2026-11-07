"""Verify the plugin loads through the real MoleditPy PluginManager."""

import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from moleditpy.plugins.plugin_manager import PluginManager

PLUGIN_SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "orca_calculator"
)


def main() -> int:
    # Isolated plugins dir: only our plugin, so other user plugins can't interfere
    tmp = tempfile.mkdtemp(prefix="moleditpy_plugins_")
    try:
        shutil.copytree(
            PLUGIN_SRC,
            os.path.join(tmp, "orca_calculator"),
            ignore=shutil.ignore_patterns("__pycache__", ".git", "tests"),
        )
        manager = PluginManager()
        manager.plugin_dir = tmp
        plugins = manager.discover_plugins()
        for plugin in plugins:
            print(f"{plugin.get('name')} | {plugin.get('status')} | "
                  f"{plugin.get('version')}")
        ok = any(
            plugin.get("name") == "ORCA Calculator"
            and "Error" not in str(plugin.get("status"))
            and plugin.get("status") not in (None, "No Entry Point")
            for plugin in plugins
        )
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
