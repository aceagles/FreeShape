"""The tab engine: `submit_edit(tab, edit)` -> `outputs(tab)`, with outputs as plain picklable data (no OCCT
objects), so the same contract can run a tab in another process or on another machine (ADR 0002).
Headless -- no Qt. See contract.py for the edit and output formats."""
from .contract import (AssemblyOutputs, ConnectorOut, DrawingOutputs, Engine, EngineError, InstanceOut,
                       MateOut, MeshOut, PartOut, SketchOut, StudioOutputs, TabOutputs, plain)
from .executor import InProcessEngine, file_kind

__all__ = ["AssemblyOutputs", "ConnectorOut", "DrawingOutputs", "Engine", "EngineError", "InProcessEngine",
           "InstanceOut", "MateOut", "MeshOut", "PartOut", "SketchOut", "StudioOutputs", "TabOutputs",
           "file_kind", "plain"]
