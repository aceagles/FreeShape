"""The tab engine: `submit_edit(tab, edit)` -> `outputs(tab)`, with outputs as plain picklable data (no OCCT
objects), so the same contract can run a tab in another process or on another machine (ADR 0002).
Headless -- no Qt. See contract.py for the edit and output formats; executor.py runs every tab in this
process, process.py runs each tab in a worker process of its own."""
from .contract import (AssemblyOutputs, ConnectorOut, DrawingOutputs, Engine, EngineError, InstanceOut,
                       LinkCycleError, MateOut, MeshOut, PartOut, SketchOut, StudioOutputs, TabErrored, TabOutputs, VariablesOutputs, plain)
from .executor import InProcessEngine, file_kind
from .process import ProcessEngine

__all__ = ["AssemblyOutputs", "ConnectorOut", "DrawingOutputs", "Engine", "EngineError", "InProcessEngine",
           "InstanceOut", "LinkCycleError", "MateOut", "MeshOut", "PartOut", "ProcessEngine", "SketchOut", "StudioOutputs",
           "TabErrored", "TabOutputs", "VariablesOutputs", "file_kind", "plain"]
