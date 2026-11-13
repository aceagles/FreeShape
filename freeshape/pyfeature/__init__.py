"""Python features: user-written parametric features supplied by Plugins (see registry.py for the rules).

Headless: nothing here imports Qt. Importing this package registers the bundled plugins
(freeshape/plugins/); `load_user_plugins()` adds the user's. Plugin modules import from
`freeshape.pyfeature.api`, never from this package.
"""
from __future__ import annotations

from . import api, host, registry
from .api import Angle, Bool, Choice, Feature, Float, Inputs, Integer, Length, Param
from .registry import (PluginInfo, default_params, feature_types, get, is_python_type, load_bundled_plugins,
                       load_user_plugins, missing_message, notices, plugin, plugin_for, plugins, problems,
                       register_plugin, unregister_plugin, user_root)

__all__ = ["api", "host", "registry", "Angle", "Bool", "Choice", "Feature", "Float", "Inputs", "Integer",
           "Length", "Param", "PluginInfo", "default_params", "feature_types", "get", "is_python_type",
           "load_bundled_plugins", "load_user_plugins", "missing_message", "notices", "plugin", "plugin_for",
           "plugins", "problems", "register_plugin", "unregister_plugin", "user_root"]

registry.load_bundled_plugins()
