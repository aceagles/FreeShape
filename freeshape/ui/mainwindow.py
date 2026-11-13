"""Main window: ties the document, viewport, panels, toolbars and dialogs together."""
from __future__ import annotations

import base64
import copy
import os
import sys

import numpy as np
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QColor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QToolButton, QLabel, QFrame, QSplitter, QMenu,
    QStackedWidget, QFileDialog, QMessageBox, QInputDialog, QColorDialog, QTabBar, QSizePolicy, QApplication, QDialog,
)

from .. import kernel as K
from .. import measure as M
from .. import features as F
from ..document import PartStudioDoc
from ..assembly import AssemblyDoc
from ..drawing import DrawingDoc
from .. import blobstore
from .. import doc_folder
from .. import docstore
from .. import doc_registry
from .. import tabkinds as TK
from .. import links
from ..sketch import Sketch
from . import theme, navstyles
from .icons import icon
from .viewport import Viewport, Pick
from .panels import LeftPanel, STD_PLANES, DocTabBar, DependenciesPanel
from .dialogs import FeatureDialog, SketchDialog, pick_to_item
from .section import SectionPanel
from .sketcher import Sketcher, CONSTRAINT_TOOLS
from .numparse import fmt
from .filedirs import start_dir, remember_dir
from .assembly_ui import AssemblyController
from .partstudio_engine import StudioEngine, element_index
from .variable_panel import VariableTabs, DefinedVariables

APP_NAME = "FreeShape"
DERIVED_LOOSE = "Add this Part Studio to a Document to use Derived for a loose .fshape."
ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")


def app_icon():
    from PySide6.QtGui import QIcon
    ic = QIcon()
    for size in (16, 32, 48, 64, 128, 256, 512):
        path = os.path.join(ASSETS, f"logo_{size}.png")
        if os.path.exists(path):
            ic.addFile(path)
    return ic


def regions_of_sketch(state, fid):
    """Region picks for every closed region of sketch `fid` (empty if it has none)."""
    sr = state.sketches.get(fid)
    return [Pick("region", (fid, i)) for i in range(len(sr.regions))] if sr is not None else []


def _sep():
    f = QFrame()
    f.setObjectName("sep")
    f.setFixedWidth(1)
    f.setFixedHeight(26)
    return f


class MainWindow(QMainWindow):
    def __init__(self, path=None, engine=False):
        super().__init__()
        self.setStyleSheet(theme.STYLESHEET)
        self.setWindowIcon(app_icon())
        self.resize(1440, 900)
        self.doc = None             # the document in the current tab
        self.docs = {}              # tab key -> Document
        self.folder = None          # the open folder Document (doc_folder.FolderDocument), if any
        self._views = {}            # tab key -> saved camera state
        self._studio_counter = 0
        self.mode = "idle"          # idle | dialog | sketch
        self.dialog = None
        self.sketcher = None
        self.sk_ctx = None
        self.sketch_waiting_for_plane = False
        self._sketch_buttons = {}
        self.section_panel = None
        from PySide6.QtCore import QSettings
        self.settings = QSettings("FreeShape", "FreeShape")
        self.resolver = links.LinkResolver(find_open_studio=self._find_open_studio,
                                           find_open_assembly=self._find_open_assembly,
                                           flat_pattern_of=self.flat_pattern_of)
        self.studio_engine = StudioEngine(self) if engine else None    # Part Studio tabs rebuild in the engine
        self._build_ui()
        self.var_tabs = VariableTabs(self)      # Variable Studio tabs (I3.8)
        self._bind_shortcuts()
        self.set_theme(str(self.settings.value("theme", "light")), save=False)
        self.set_orbit_mode(str(self.settings.value("orbit", "cursor")), save=False)
        self.set_nav_style(str(self.settings.value("mouse_style", self.settings.value("nav_style", "onshape"))),
                           save=False)
        self.set_zoom_speed(str(self.settings.value("zoom_speed", "normal")), save=False)
        self.set_shiny(str(self.settings.value("shiny", "true")).lower() != "false", save=False)
        self._add_tab(self._new_document())
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self.autosave)
        self._autosave_timer.start(int(os.environ.get("FREESHAPE_AUTOSAVE_MS", "60000")))
        if not os.environ.get("FREESHAPE_NO_RECOVERY"):
            QTimer.singleShot(500, self.offer_recovery)
        if path:
            self.open_any(path)
        elif not os.environ.get("FREESHAPE_NO_HOME"):
            self.show_home()
        QTimer.singleShot(50, lambda: self.viewport.set_standard_view("Isometric", animate=False))

    # ================================================================== UI construction
    def _build_ui(self):
        self.asm = AssemblyController(self)
        from .drawing_ui import DrawingController
        self.dwg = DrawingController(self)
        central = QWidget()
        central.setObjectName("central")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_topbar())
        self.main_stack = QStackedWidget()
        work = QWidget()
        root_outer = root
        root = QVBoxLayout(work)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.tool_stack = QStackedWidget()
        self.tool_stack.setObjectName("toolbar")
        self.tool_stack.addWidget(self._build_feature_toolbar())
        self.tool_stack.addWidget(self._build_sketch_toolbar())
        self.tool_stack.addWidget(self._build_assembly_toolbar())
        self.tool_stack.addWidget(self._build_drawing_toolbar())
        self.tool_stack.setFixedHeight(44)
        root.addWidget(self.tool_stack)

        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(1)
        self.left = LeftPanel()
        self.defined_vars_header = QLabel("Variables")          # a Part Studio's variables, overridden ones marked
        self.defined_vars_header.setObjectName("sectionHeader")
        self.defined_vars = DefinedVariables()
        self.defined_vars.setMaximumHeight(150)
        self.left.layout().addWidget(self.defined_vars_header)
        self.left.layout().addWidget(self.defined_vars)
        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(self.left)
        self.left_stack.addWidget(self.asm.panel)
        self.left_stack.addWidget(self.dwg.panel)
        self.dependencies = DependenciesPanel()                 # I6.3: what this tab uses / what uses it
        self.dependencies.tabRequested.connect(self._open_dependency_tab)
        left_col = QWidget()
        lcl = QVBoxLayout(left_col)
        lcl.setContentsMargins(0, 0, 0, 0)
        lcl.setSpacing(0)
        lcl.addWidget(self.left_stack, 1)
        lcl.addWidget(self.dependencies)
        split.addWidget(left_col)
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        self.viewport = Viewport()
        self.view_stack = QStackedWidget()       # the 3D view, or the drawing sheet
        self.view_stack.addWidget(self.viewport)
        self.view_stack.addWidget(self.dwg.canvas)
        cl.addWidget(self.view_stack, 1)
        self.tabs = DocTabBar()
        self.tabs.currentChanged.connect(self._switch_tab)
        self.tabs.middleClicked.connect(self.close_tab)
        self.tabs.tabBarDoubleClicked.connect(self._rename_tab)
        self.tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self._tab_context)
        tabwrap = QWidget()
        tabwrap.setObjectName("tabstrip")
        tabwrap.setStyleSheet("QWidget#tabstrip { background: #eef0f3; border-top: 1px solid %s; }" % theme.BORDER)
        tl = QHBoxLayout(tabwrap)
        tl.setContentsMargins(0, 0, 8, 0)
        tl.setSpacing(0)
        plus = QToolButton()
        plus.setText("+")
        plus.setToolTip("Create a Part Studio, an Assembly or a Drawing")
        plus.setFixedSize(40, 32)
        plus.setStyleSheet("QToolButton { font-size: 20px; color: #3b4250; border: none; border-right: 1px solid #d5d9de;"
                           " border-radius: 0; background: #eef0f3; }"
                           "QToolButton:hover { background: #e3e8ef; }")
        plus.setPopupMode(QToolButton.InstantPopup)
        pm = QMenu(plus)
        pm.addAction(icon("part", 16), "Create Part Studio", self.new_doc)
        pm.addAction(icon("assembly", 16), "Create Assembly", self.new_assembly)
        pm.addAction(icon("drawing", 16), "Create Drawing…", lambda: self.new_drawing())
        plus.setMenu(pm)
        tl.addWidget(plus)
        tl.addWidget(self.tabs, 1)
        tl.addStretch(0)
        self.units_label = QLabel("Units: millimetre")
        self.units_label.setObjectName("muted")
        tl.addWidget(self.units_label)
        self._tabwrap = tabwrap
        split.addWidget(center)
        split.setStretchFactor(1, 1)
        split.setSizes([270, 1170])
        root.addWidget(split, 1)
        root.addWidget(self._tabwrap)
        from .home import HomePage
        self.home = HomePage()
        self.main_stack.addWidget(work)
        self.main_stack.addWidget(self.home)
        root_outer.addWidget(self.main_stack, 1)
        hp = self.home
        hp.openPath.connect(self.open_any)
        hp.newStudio.connect(self._home_new_studio)
        hp.newAssembly.connect(self.new_assembly)
        hp.newDocument.connect(self._home_new_document)
        hp.openDocument.connect(self.open_document_dialog)
        hp.openDialog.connect(self.open_dialog)
        hp.importStep.connect(self.import_step)
        hp.backToWork.connect(self.show_workspace)
        hp.removeRecent.connect(self._remove_recent)
        hp.clearRecent.connect(self._clear_recent)
        self.setAcceptDrops(True)
        self.setCentralWidget(central)

        self.measure = QLabel(self.viewport)
        self.measure.setStyleSheet("background: rgba(255,255,255,235); border: 1px solid %s; border-radius: 5px;"
                                   "padding: 6px 10px; color: %s;" % (theme.BORDER, theme.TEXT))
        self.measure.hide()
        self.status = self.statusBar()
        self.status.showMessage("Ready")

        lp = self.left
        lp.features.editRequested.connect(self.edit_feature)
        lp.features.contextRequested.connect(self._tree_context)
        lp.features.context_status = self._context_status_of
        lp.features.orderChanged.connect(self._tree_reordered)
        lp.features.planeClicked.connect(self._tree_plane_clicked)
        lp.features.featureHovered.connect(self._feature_hovered)
        lp.features.itemSelectionChanged.connect(self._update_feature_hl)
        self._hl_hover = None
        self._hl_cache = {}
        lp.filter.textChanged.connect(lambda t: (lp.features.set_filter(t), self._populate_lists()))
        lp.features.eyeClicked.connect(self._tree_eye)
        lp.parts.eyeClicked.connect(lambda pid: self.doc.toggle_part_hidden(pid) if self.doc is not None else None)
        lp.parts.partClicked.connect(self._part_clicked)
        lp.parts.contextRequested.connect(self._part_context)
        self.viewport.selectionChanged.connect(self._selection_changed)
        self.viewport.contextRequested.connect(self._viewport_context)
        self.viewport.owner_name = self._pick_owner_name
        self.viewport.label_override = lambda pk: self.asm.row_label(pk) if self.is_asm else None
        self.viewport.doubleClicked.connect(self._viewport_double)
        self.viewport.hoverChanged.connect(self._hover_changed)

    def _build_topbar(self):
        bar = QWidget()
        bar.setObjectName("topbar")
        bar.setFixedHeight(44)
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 0, 12, 0)
        mark = QLabel()
        mark.setPixmap(app_icon().pixmap(28, 28))
        h.addWidget(mark)
        h.addSpacing(6)
        logo = QLabel("freeshape")
        logo.setObjectName("logo")
        h.addWidget(logo)
        h.addSpacing(10)
        home_btn = QToolButton()
        home_btn.setObjectName("menuButton")
        home_btn.setText("⌂  Home")
        home_btn.setToolTip("Home — new projects and recent files")
        home_btn.clicked.connect(lambda: self.show_home() if self.main_stack.currentIndex() == 0 else self.show_workspace())
        self.home_btn = home_btn
        h.addWidget(home_btn)
        menu_btn = QToolButton()
        menu_btn.setObjectName("menuButton")
        menu_btn.setText("☰  Document")
        menu_btn.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(menu_btn)
        self._add_action(m, "New Part Studio", self.new_doc, "Ctrl+N")
        self._add_action(m, "New Assembly", self.new_assembly, "Ctrl+Shift+N")
        self._add_action(m, "New Drawing…", lambda: self.new_drawing())
        self._add_action(m, "Open…", self.open_dialog, "Ctrl+O")
        self._add_action(m, "New Document folder…", self._home_new_document)
        self._add_action(m, "Open Document folder…", self.open_document_dialog)
        self._add_action(m, "Add files to Document…", self.add_to_document)
        self.recent_menu = m.addMenu("Open recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self._add_action(m, "Close tab", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+W")
        m.addSeparator()
        self._add_action(m, "Save", self.save, "Ctrl+S")
        self._add_action(m, "Save as…", self.save_as, "Ctrl+Shift+S")
        m.addSeparator()
        self._add_action(m, "Import STEP (new tab)…", self.import_step)
        self._add_action(m, "Import STEP into this tab…", lambda: self.import_step(into_current=True))
        self._add_action(m, "Export STEP…", lambda: self.export(None, "step"))
        self._add_action(m, "Export STL…", lambda: self.export(None, "stl"))
        m.addSeparator()
        rm = m.addMenu("Add relation (Assembly)")
        from ..assembly import RELATION_TYPES as _RT
        for t, spec in _RT.items():
            rm.addAction(icon("r_" + t, 16), spec["label"],
                         lambda tt=t: self.asm.start_relation(tt) if self.is_asm else None)
        m.addSeparator()
        self._add_action(m, "Keyboard shortcuts", self.show_shortcuts)
        self._add_action(m, "About FreeShape", self.show_about)
        self._add_action(m, "Quit", self.close, "Ctrl+Q")
        menu_btn.setMenu(m)
        h.addWidget(menu_btn)
        self._build_workspace_switcher(h)
        self._build_merge_button(h)
        self._build_versions_bar(h)
        view_btn = QToolButton()
        view_btn.setObjectName("menuButton")
        view_btn.setText("View")
        view_btn.setPopupMode(QToolButton.InstantPopup)
        vm = QMenu(view_btn)
        for i, name in enumerate(("Front", "Back", "Left", "Right", "Top", "Bottom", "Isometric"), 1):
            self._add_action(vm, name, lambda n=name: self.viewport.set_standard_view(n), f"Shift+{i}")
        vm.addSeparator()
        self.act_section = self._add_action(vm, "Section view", self.toggle_section, "Ctrl+Shift+X")
        self.act_section.setCheckable(True)
        self._add_action(vm, "Zoom to fit", lambda: self.dwg.canvas.fit() if self.is_dwg else self.viewport.fit(), "F")
        self.act_planes = self._add_action(vm, "Show default planes", self._toggle_planes)
        self.act_planes.setCheckable(True)
        self.act_planes.setChecked(True)
        self._add_action(vm, "Show all parts", self._show_all_parts)
        self.act_shiny = self._add_action(vm, "Shiny surfaces", lambda on: self.set_shiny(on))
        self.act_shiny.setCheckable(True)
        rm = vm.addMenu("Rotate around")
        from PySide6.QtGui import QActionGroup as _AG
        rgrp = _AG(self)
        self.orbit_actions = {}
        for mode, label in (("cursor", "The point under the mouse (model centre over empty space)"),
                            ("centre", "The centre of the model"), ("origin", "The origin")):
            a = QAction(label, self)
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, md=mode: self.set_orbit_mode(md))
            rgrp.addAction(a)
            rm.addAction(a)
            self.orbit_actions[mode] = a
        nm = vm.addMenu("Mouse style")
        nm.setToolTipsVisible(True)
        ngrp = _AG(self)
        self.nav_actions = {}
        for mode, st in navstyles.STYLES.items():
            a = QAction(st.label, self)
            a.setToolTip(st.help_line())
            a.setStatusTip(st.help_line())
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, md=mode: self.set_nav_style(md))
            ngrp.addAction(a)
            nm.addAction(a)
            self.nav_actions[mode] = a
        zm = vm.addMenu("Zoom speed")
        zgrp = _AG(self)
        self.zoom_actions = {}
        for mode, label in (("slow", "Slow"), ("normal", "Normal"), ("fast", "Fast")):
            a = QAction(label, self)
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, md=mode: self.set_zoom_speed(md))
            zgrp.addAction(a)
            zm.addAction(a)
            self.zoom_actions[mode] = a
        vm.addSeparator()
        tm = vm.addMenu("Theme")
        from PySide6.QtGui import QActionGroup
        grp = QActionGroup(self)
        self.theme_actions = {}
        for mode, label in (("light", "Light"), ("dark", "Dark")):
            a = QAction(label, self)
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, md=mode: self.set_theme(md))
            grp.addAction(a)
            tm.addAction(a)
            self.theme_actions[mode] = a
        view_btn.setMenu(vm)
        h.addWidget(view_btn)
        # ---- what a click / box selection may pick: vertices, edges, faces, solids, sketches, planes
        self.sel_btn = QToolButton()
        self.sel_btn.setObjectName("menuButton")
        self.sel_btn.setText("Select")
        self.sel_btn.setToolTip("Choose which kinds of things can be selected (vertices, edges, faces …)")
        self.sel_btn.setPopupMode(QToolButton.InstantPopup)
        selm = QMenu(self.sel_btn)
        self.sel_actions = {}
        for key, label, kinds, on in (
                ("vertex", "Vertices", ("vertex",), False), ("edge", "Edges", ("edge",), True),
                ("face", "Faces", ("face",), True), ("part", "Solids (whole parts)", ("part",), False),
                ("sketch", "Sketches", ("sketch_line", "region"), True), ("plane", "Planes", ("plane",), True)):
            a = QAction(label, self)
            a.setCheckable(True)
            a.setChecked(on)
            a.setData(kinds)
            a.toggled.connect(self._selection_filter_changed)
            selm.addAction(a)
            self.sel_actions[key] = a
        selm.addSeparator()
        ra = QAction("Reset to default", self)
        ra.triggered.connect(self._reset_selection_filter)
        selm.addAction(ra)
        self.sel_btn.setMenu(selm)
        h.addWidget(self.sel_btn)
        search_btn = QToolButton()
        search_btn.setObjectName("menuButton")
        search_btn.setText("Search tools…")
        search_btn.setIcon(icon("search"))
        search_btn.setToolTip("Find a tool by name (Alt+C or Ctrl+Space)")
        search_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        search_btn.clicked.connect(self.open_command_search)
        h.addWidget(search_btn)
        h.addSpacing(16)
        self.doc_label = QLabel("Untitled document")
        self.doc_label.setObjectName("docname")
        h.addWidget(self.doc_label)
        h.addStretch(1)
        hint = QLabel("Right-drag: rotate · Middle-drag: pan · Wheel: zoom")
        hint.setObjectName("muted")
        h.addWidget(hint)
        return bar

    # ------------------------------------------------------------------ command search
    def _palette_commands(self):
        from .palette import Command
        F_ = self.start_feature
        C = lambda key, name, desc, fn, kw="", grp="Feature": Command(key, name, desc, fn, kw, grp)
        if self.var_tabs.is_variables(self.doc):
            return self._palette_variable_commands(C)
        if self.is_dwg:
            return self.dwg.palette_commands(C)
        if self.is_asm:
            return self.asm.palette_commands(C)
        if self.mode == "sketch":
            return self._palette_sketch_commands(C)
        cmds = [
            C("sketch", "Sketch", "Draw 2D profiles on a plane or a flat face. With vertices, edges or faces selected "
              "that define a plane, the plane is made for you.", lambda: self.start_sketch(), "draw 2d profile"),
            C("extrude", "Extrude", "Add or remove material by extending sketch regions or flat faces. Blind, "
              "symmetric or through all.", lambda: F_("extrude"), "boss cut pad pocket"),
            C("revolve", "Revolve", "Spin a sketch region around an axis to make round parts.",
              lambda: F_("revolve"), "lathe turn"),
            C("sweep", "Sweep", "Move a profile along a path to make a pipe or rail.", lambda: F_("sweep"), "pipe path"),
            C("loft", "Loft", "Blend smoothly between two or more profiles.", lambda: F_("loft"), "blend transition"),
            C("fillet", "Fillet", "Round off edges or faces with a radius.", lambda: F_("fillet"), "round blend corner"),
            C("chamfer", "Chamfer", "Create beveled edges or faces with a distance.", lambda: F_("chamfer"),
              "bevel"),
            C("draft", "Draft", "Angle side faces so a part releases from a mould.", lambda: F_("draft"), "taper"),
            C("shell", "Shell", "Hollow a part, leaving walls of a thickness; chosen faces are left open.",
              lambda: F_("shell"), "hollow"),
            C("hole", "Hole", "Custom holes or ISO clearance, tapped, counterbore and countersink holes at sketch "
              "points.", lambda: F_("hole"), "drill tap counterbore countersink screw"),
            C("thread", "Thread", "Model real ISO metric threads on holes and shafts.", lambda: F_("thread"),
              "screw bolt"),
            C("delete_face", "Delete face", "Remove faces from a part. Heal closes the gap by extending the "
              "surrounding faces, Cap covers it with a flat face, or leave the void open. Useful for imported "
              "parts: removes fillets, chamfers, bosses and holes.", lambda: F_("delete_face"),
              "defeature remove fillet chamfer protrusion step import heal cap"),
            C("move_face", "Move face", "Push or pull flat faces along their normal to resize a part.",
              lambda: F_("move_face"), "offset face resize push pull direct edit"),
            C("offset_surface", "Offset surface", "Make a new surface a set distance from selected faces (flat or "
              "curved). The result is a surface body you can use as a reference or delete faces from.",
              lambda: F_("offset_surface"), "offset face surface copy shell skin"),
            C("delete_part", "Delete part", "Remove the selected parts from the model from this point on.",
              lambda: F_("delete_part"), "remove erase discard body"),
            C("sm_base", "Sheet metal", "A flat sheet from a sketch with thickness, bend radius and K-factor.",
              lambda: F_("sm_base"), "bracket enclosure"),
            C("sm_flange", "Flange", "Bend the sheet up along edges.", lambda: F_("sm_flange"), "sheet metal bend"),
            C("plane", "Plane", "Make a datum plane: offset, through a point, line and angle, three points, mid "
              "plane, normal to a curve or tangent to a surface.", lambda: F_("plane"), "datum"),
            C("mirror", "Mirror", "Copy parts across a plane.", lambda: F_("mirror"), "symmetry"),
            C("linear_pattern", "Linear pattern", "Repeat parts along a direction.", lambda: F_("linear_pattern"),
              "array copies"),
            C("circular_pattern", "Circular pattern", "Repeat parts around an axis.",
              lambda: F_("circular_pattern"), "array copies polar"),
            C("boolean", "Boolean", "Add, subtract or intersect parts.", lambda: F_("boolean"),
              "union subtract intersect combine merge"),
            C("split", "Split", "Cut a part in two with a plane or a flat face.", lambda: F_("split"), "cut"),
            C("move", "Move", "Move, rotate or copy parts by exact amounts.", lambda: F_("move"), "translate"),
            C("mate_connector", "Mate connector", "Put a named frame (origin, Z and X) on a face or edge, with the "
              "same snap points as an Assembly. Assemblies can mate to it.", lambda: F_("mate_connector"),
              "mate connector frame triad origin snap mate"),
            C("update_context", "Update context", "Recapture the selected Context feature from its Assembly, so "
              "the neighbouring parts match the Assembly again.", lambda: self.update_context(),
              "context assembly refresh recapture out of date"),
            C("variable", "Variable", "Define #name values to use in any dimension.", lambda: F_("variable"),
              "parameter"),
            C("variable_studio_ref", "Variable Studio reference", "Use the variables of a Variable Studio tab "
              "of this Document: all of them or the ones you tick.", lambda: F_("variable_studio_ref"),
              "variables studio link import parameter"),
            Command("derived", "Derived", "Bring parts and sketches from another Part Studio of this Document into "
                    "this one; they follow the source when it changes.", self._start_derived,
                    "link import reference skeleton master", "Feature", self._derived_blocked() or ""),
            C("import", "Import STEP", "Open a STEP file in a new tab.", lambda: self.import_step(), "open file",
              "File"),
            C("export", "Export", "Export the model as STEP or STL.", lambda: self.export(None, None),
              "save stl step", "File"),
            C("undo", "Undo", "Undo the last change (Ctrl+Z).", self.undo, "", "Edit"),
            C("redo", "Redo", "Redo what was undone (Ctrl+Y).", self.redo, "", "Edit"),
            C("section", "Section view", "Cut the model open with a plane to see inside (Ctrl+Shift+X).",
              self.toggle_section, "cross", "View"),
            C("fit", "Zoom to fit", "Fit the whole model in the view (F).", lambda: self.viewport.fit(), "", "View"),
            C("iso", "Isometric view", "Look at the model from the isometric corner (Shift+7).",
              lambda: self.viewport.set_standard_view("Isometric"), "", "View"),
        ]
        for i, name in enumerate(("Front", "Back", "Left", "Right", "Top", "Bottom"), 1):
            cmds.append(C("iso", f"{name} view", f"Look at the model from the {name.lower()} (Shift+{i}).",
                          lambda n=name: self.viewport.set_standard_view(n), "", "View"))
        from . import pyfeature_ui
        return cmds + pyfeature_ui.palette_commands(self)

    # the sketch toolbar's tools and constraints, in toolbar order: (key, name, extra match words)
    SKETCH_PALETTE = (
        ("select", "Select", "pointer arrow"), ("line", "Line", "segment"), ("rect", "Corner rectangle", "box"),
        ("center_rect", "Center rectangle", "centre box"), ("circle", "Circle", "round"),
        ("polygon", "Polygon", "hexagon pentagon regular sides"), ("arc3", "3-point arc", "three point"),
        ("arc_center", "Center-point arc", "centre point"), ("point", "Point", "vertex"),
        ("dimension", "Dimension", "smart dimension size measure"),
        ("sk_fillet", "Sketch fillet", "round corner radius"), ("offset", "Offset", "parallel copy"),
        ("slot", "Slot", "obround stadium"), ("sk_mirror", "Mirror", "symmetry"),
        ("sk_lpattern", "Linear pattern", "array copies"), ("sk_cpattern", "Circular pattern", "array copies polar"),
        ("use", "Use", "project convert intersect part edges"), ("trim", "Trim", "cut remove"),
        ("extend", "Extend", "lengthen"))

    def _palette_sketch_commands(self, C):
        """Search tools entries while a sketch is open: the sketch toolbar's tools, constraints and view commands."""
        from .sketcher import CONSTRAINT_TOOLS, TOOL_HINTS
        grp = "Sketch"
        cmds = [C("undo", "Undo", "Undo the last sketch change (Ctrl+Z).", self.undo, "", "Edit"),
                C("redo", "Redo", "Redo what was undone (Ctrl+Y).", self.redo, "", "Edit")]
        for key, name, kw in self.SKETCH_PALETTE:
            cmds.append(C("point" if key == "select" else key, name,
                          TOOL_HINTS.get(key) or f"{name} — sketch tool.", lambda k=key: self._sk_tool(k), kw, grp))
        cmds.append(C("construction", "Construction", "Turn the selected geometry into construction geometry, or "
                      "back (Q).", lambda: self._sk("toggle_construction"), "toggle reference helper", grp))
        for name, tip in CONSTRAINT_TOOLS.items():
            cmds.append(C(name, name.capitalize(), tip + ".", lambda n=name: self._sk_tool(n), "constraint relation",
                          "Constraint"))
        cmds += [C("delete", "Delete selected", "Delete the selected sketch geometry (Del).",
                   lambda: self._sk("delete_selection"), "remove erase", grp),
                 C("normal_to", "Normal to sketch", "Turn the view to look straight at the sketch plane (N).",
                   self._normal_to_sketch, "view perpendicular plane", "View"),
                 C("fit", "Zoom to fit", "Fit the whole model in the view (F).", lambda: self.viewport.fit(), "",
                   "View"),
                 C("iso", "Isometric view", "Look at the model from the isometric corner (Shift+7).",
                   lambda: self.viewport.set_standard_view("Isometric"), "", "View")]
        for i, name in enumerate(("Front", "Back", "Left", "Right", "Top", "Bottom"), 1):
            cmds.append(C("iso", f"{name} view", f"Look at the model from the {name.lower()} (Shift+{i}).",
                          lambda n=name: self.viewport.set_standard_view(n), "", "View"))
        cmds.append(C("sketch", "Finish sketch", "Save the sketch and leave sketch mode.", self.finish_sketch,
                      "done accept exit close ok", grp))
        return cmds

    def _palette_variable_commands(self, C):
        """Search tools entries for a Variable Studio tab: only what needs no 3D view."""
        return [
            C("part", "New Part Studio", "Create a Part Studio tab (Ctrl+N).", self.new_doc, "create tab", "File"),
            C("assembly", "New Assembly", "Create an Assembly tab (Ctrl+Shift+N).", self.new_assembly,
              "create tab", "File"),
            C("drawing", "New Drawing", "Create a Drawing tab.", lambda: self.new_drawing(), "create tab", "File"),
            C("import", "Import STEP", "Open a STEP file in a new tab.", lambda: self.import_step(), "open file",
              "File"),
            C("export", "Save", "Save this tab (Ctrl+S).", self.save, "write store", "File"),
            C("export", "Save as", "Save this tab under a new name (Ctrl+Shift+S).", self.save_as, "write store",
              "File"),
            C("export", "Keyboard shortcuts", "List the keyboard shortcuts.", self.show_shortcuts,
              "keys help hotkeys", "Help"),
        ]

    def _derived_blocked(self):
        """Why Derived cannot start in the current tab (None when it can): a Part Studio outside a Document has
        no other tab to read from (decision d-i1-7-loose)."""
        if self._folder_tab(self.doc) is None:
            return DERIVED_LOOSE
        return None

    def _start_derived(self):
        why = self._derived_blocked()
        if why:
            self.status.showMessage(why, 6000)
            return
        self.start_feature("derived")

    def open_command_search(self):
        if self.mode == "dialog":
            self.status.showMessage("Finish or cancel the open tool first, then search tools.", 5000)
            return
        host = self.view_stack.currentWidget()      # the 3D view, the drawing sheet or the variables table
        if getattr(self, "_palette", None) is None:
            from .palette import CommandPalette
            self._palette = CommandPalette(host, self._palette_commands)
        elif self._palette.parentWidget() is not host:
            self._palette.setParent(host)
        self._palette.open()

    SELECT_DEFAULTS = {"vertex": False, "edge": True, "face": True, "part": False, "sketch": True, "plane": True}

    def _selection_filter_changed(self, _checked=None):
        kinds = set()
        for a in self.sel_actions.values():
            if a.isChecked():
                kinds.update(a.data())
        vp = self.viewport
        is_default = all(a.isChecked() == self.SELECT_DEFAULTS[k] for k, a in self.sel_actions.items())
        vp.user_filter = None if is_default else kinds
        self.sel_btn.setText("Select" if is_default else "Select •")
        # drop anything already selected that can no longer be selected
        managed = {k for a in self.sel_actions.values() for k in a.data()}
        keep = [pk for pk in vp.selection if pk.kind not in managed or vp._allowed(pk.kind)]
        if len(keep) != len(vp.selection):
            vp.set_selection(keep)
        vp.hover = None
        vp.update()

    def _reset_selection_filter(self):
        for k, a in self.sel_actions.items():
            a.blockSignals(True)
            a.setChecked(self.SELECT_DEFAULTS[k])
            a.blockSignals(False)
        self._selection_filter_changed()

    def _add_action(self, menu, text, slot, shortcut=None):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.WindowShortcut)
            self.addAction(a)
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    def _tb(self, layout, name, tip, slot, checkable=False):
        b = QToolButton()
        b.setIcon(icon(name))
        b.setProperty("icon_name", name)
        b.setIconSize(QSize(26, 26))
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setAutoRaise(True)
        b.clicked.connect(slot)
        layout.addWidget(b)
        return b

    def _build_feature_toolbar(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 2, 8, 2)
        h.setSpacing(2)
        self.btn_undo = self._tb(h, "undo", "Undo (Ctrl+Z)", self.undo)
        self.btn_redo = self._tb(h, "redo", "Redo (Ctrl+Y)", self.redo)
        h.addWidget(_sep())
        self._tb(h, "variable", "Variable — define #name values to use in any dimension", lambda: self.start_feature("variable"))
        self._tb(h, "variable_studio_ref", "Variable Studio reference — use the variables of a Variable Studio tab", lambda: self.start_feature("variable_studio_ref"))
        self._tb(h, "derived", "Derived — parts and sketches from another Part Studio of this Document", self._start_derived)
        h.addWidget(_sep())
        self._tb(h, "sketch", "Sketch (Shift+S)", lambda: self.start_sketch())
        h.addWidget(_sep())
        for name, tip in (("extrude", "Extrude (Shift+E)"), ("revolve", "Revolve (Shift+W)"),
                          ("sweep", "Sweep — a profile along a path"), ("loft", "Loft — blend between profiles"),
                          ("fillet", "Fillet (Shift+F)"), ("chamfer", "Chamfer"), ("draft", "Draft — angle side faces"),
                          ("shell", "Shell"), ("hole", "Hole — custom or ISO clearance / tapped"),
                          ("thread", "Thread — real ISO threads on holes and shafts"),
                          ("delete_face", "Delete face — remove fillets, chamfers, bosses and holes from "
                                          "imported parts (heal, cap or leave open)"),
                          ("move_face", "Move face — push or pull flat faces"),
                          ("offset_surface", "Offset surface — a new surface a set distance from faces"),
                          ("delete_part", "Delete part — remove parts from the model from here on")):
            self._tb(h, name, tip, lambda _=False, n=name: self.start_feature(n))
        h.addWidget(_sep())
        self._tb(h, "sm_base", "Sheet metal — a flat sheet from a sketch (thickness, bend radius, K-factor)",
                 lambda: self.start_feature("sm_base"))
        self._tb(h, "sm_flange", "Flange — bend the sheet up along edges", lambda: self.start_feature("sm_flange"))
        self.btn_flat = self._tb(h, "flat", "Flat pattern — show sheet metal parts unfolded", self.toggle_flat,
                                 checkable=True)
        self._tb(h, "flat_dxf", "Export the flat pattern as DXF (for laser / plasma cutting)",
                 lambda: self.export_flat_dxf())
        from . import pyfeature_ui
        pyfeature_ui.install()
        pyfeature_ui.toolbar_button(self, h)
        h.addWidget(_sep())
        self._tb(h, "plane", "Plane", lambda: self.start_feature("plane"))
        h.addWidget(_sep())
        for name, tip in (("mirror", "Mirror"), ("linear_pattern", "Linear pattern"),
                          ("circular_pattern", "Circular pattern"), ("boolean", "Boolean"),
                          ("split", "Split a part with a plane"), ("move", "Move / rotate / copy parts"),
                          ("mate_connector", "Mate connector — a named frame on a face or edge, for Assemblies "
                                             "to mate to")):
            self._tb(h, name, tip, lambda _=False, n=name: self.start_feature(n))
        h.addWidget(_sep())
        self._tb(h, "import", "Import STEP (opens in a new tab)", lambda: self.import_step())
        self._tb(h, "export", "Export STEP / STL", lambda: self.export(None, None))
        h.addStretch(1)
        self.btn_section = self._tb(h, "section", "Section view (Ctrl+Shift+X) — select a face or plane first to cut there",
                                    self.toggle_section, checkable=True)
        self._tb(h, "fit", "Zoom to fit (F)", lambda: self.viewport.fit())
        self._tb(h, "iso", "Isometric (Shift+7)", lambda: self.viewport.set_standard_view("Isometric"))
        return w

    def _build_assembly_toolbar(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 2, 8, 2)
        h.setSpacing(2)
        a = self.asm
        self.btn_aundo = self._tb(h, "undo", "Undo (Ctrl+Z)", self.undo)
        self.btn_aredo = self._tb(h, "redo", "Redo (Ctrl+Y)", self.redo)
        h.addWidget(_sep())
        self._tb(h, "insert", "Insert — add parts from your Part Studios or a file (I)", a.toggle_insert)
        h.addWidget(_sep())
        from ..assembly import MATE_TYPES, RELATION_TYPES
        for t, (label, tip) in MATE_TYPES.items():
            key = {"fastened": " (Shift+F)", "revolute": " (Shift+R)", "slider": " (Shift+L)"}.get(t, "")
            self._tb(h, "m_" + t, f"{label} mate{key} — {tip}", lambda _=False, tt=t: a.start_mate(tt))
        h.addWidget(_sep())
        for t, spec in RELATION_TYPES.items():
            self._tb(h, "r_" + t, f"{spec['label']} relation — {spec['help']}",
                     lambda _=False, tt=t: a.start_relation(tt))
        h.addWidget(_sep())
        self._tb(h, "move", "Move / rotate the selected parts by exact amounts", lambda: a.start_move())
        self._tb(h, "fix", "Fix / unfix the selected parts", self._asm_toggle_fix)
        self.btn_group = self._tb(h, "group", "Group the selected parts so they move as one body", self._asm_group)
        h.addWidget(_sep())
        self.btn_interf = self._tb(h, "interference", "Interference check — show where parts overlap",
                                   lambda: a.toggle_interference(), checkable=True)
        self._tb(h, "bom", "Bill of materials", a.show_bom)
        self._tb(h, "export", "Export the assembly (STEP keeps the parts, names and colours)",
                 lambda: self.export(None, None))
        h.addStretch(1)
        self.btn_asection = self._tb(h, "section", "Section view (Ctrl+Shift+X)", self.toggle_section, checkable=True)
        self._tb(h, "fit", "Zoom to fit (F)", lambda: self.viewport.fit())
        self._tb(h, "iso", "Isometric (Shift+7)", lambda: self.viewport.set_standard_view("Isometric"))
        return w

    def _build_drawing_toolbar(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 2, 8, 2)
        h.setSpacing(2)
        d = self.dwg
        self._tb(h, "undo", "Undo (Ctrl+Z)", self.undo)
        self._tb(h, "redo", "Redo (Ctrl+Y)", self.redo)
        h.addWidget(_sep())
        self._tb(h, "base_view", "Insert view — front, top, side, isometric …", d.insert_view)
        self._dwg_buttons = {}
        self._dwg_buttons["projected"] = self._tb(h, "proj_view", "Projected view — pick a view, then click beside "
                                                  "it (off a corner for an isometric)", lambda: d.set_tool("projected"),
                                                  checkable=True)
        h.addWidget(_sep())
        self._dwg_buttons["dim"] = self._tb(h, "dim", "Dimension (D) — click an edge, a circle, or two points, then "
                                            "where it goes", lambda: d.set_tool("dim"), checkable=True)
        self._dwg_buttons["note"] = self._tb(h, "note", "Note (N)", lambda: d.set_tool("note"), checkable=True)
        h.addWidget(_sep())
        self._tb(h, "sheet", "Sheet size, scale, projection and title block", d.sheet_setup)
        h.addWidget(_sep())
        self._tb(h, "dxf", "Export DXF — opens in AutoCAD", lambda: d.export("dxf"))
        self._tb(h, "pdf", "Export PDF", lambda: d.export("pdf"))
        h.addStretch(1)
        self._tb(h, "fit", "Zoom to fit (F)", lambda: d.canvas.fit())
        return w

    def dwg_tool_changed(self, tool):
        for name, b in getattr(self, "_dwg_buttons", {}).items():
            b.setChecked(name == tool)

    @property
    def is_dwg(self):
        return getattr(self.doc, "kind", "studio") == "drawing"

    def _asm_group(self):
        if not self.asm.can_group():
            self.status.showMessage("Select two or more parts that are not already in a group", 4000)
            return
        self.asm.group_selected()

    def _asm_toggle_fix(self):
        ids = self.asm.selected_instance_ids()
        if not ids:
            self.status.showMessage("Select a part first (in the list or the graphics area)", 4000)
            return
        fix = not all(self.doc.instance(i).get("fixed") for i in ids)
        self.doc.checkpoint()
        for i in ids:
            self.doc.instance(i)["fixed"] = fix
        self.doc.solve()
        self.doc.notify()

    @property
    def is_asm(self):
        return getattr(self.doc, "kind", "studio") == "assembly"

    def _build_sketch_toolbar(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 2, 8, 2)
        h.setSpacing(2)
        self._tb(h, "undo", "Undo (Ctrl+Z)", lambda: self._sk("undo"))
        self._tb(h, "redo", "Redo (Ctrl+Y)", lambda: self._sk("redo"))
        h.addWidget(_sep())
        tools = [("select", "Select (S)", "point"),
                 ("line", "Line (L)", None), ("rect", "Corner rectangle (R)", None),
                 ("center_rect", "Center rectangle", None), ("circle", "Circle (C)", None),
                 ("polygon", "Polygon — centre, corner, then sides (Shift: circumscribed)", None),
                 ("arc3", "3-point arc (A)", None), ("arc_center", "Center-point arc", None),
                 ("point", "Point (P)", None)]
        for name, tip, ic in tools:
            b = self._tb(h, ic or name, tip, lambda _=False, n=name: self._sk_tool(n), checkable=True)
            if name == "select":
                b.setIcon(self._arrow_icon())
                b.setProperty("icon_name", "__arrow")
            self._sketch_buttons[name] = b
        h.addWidget(_sep())
        b = self._tb(h, "construction", "Construction (Q)", lambda: self._sk("toggle_construction"), checkable=True)
        self._sketch_buttons["construction"] = b
        b = self._tb(h, "dimension", "Dimension (D)", lambda: self._sk_tool("dimension"), checkable=True)
        self._sketch_buttons["dimension"] = b
        h.addWidget(_sep())
        for name in ("coincident", "horizontal", "vertical", "parallel", "perpendicular", "tangent",
                     "equal", "midpoint", "concentric", "symmetric", "fix", "normal"):
            label = name.capitalize()
            keys = {"coincident": " (I)", "horizontal": " (H)", "vertical": " (V)", "equal": " (E)", "tangent": " (T)"}
            b = self._tb(h, name, label + keys.get(name, "") + " — " + CONSTRAINT_TOOLS[name],
                         lambda _=False, n=name: self._sk_tool(n), checkable=True)
            self._sketch_buttons[name] = b
        h.addWidget(_sep())
        for name, tip in (("sk_fillet", "Sketch fillet — round a corner"), ("offset", "Offset (O)"),
                          ("slot", "Slot"), ("sk_mirror", "Mirror — select geometry, then the mirror line"),
                          ("sk_lpattern", "Linear pattern — select geometry, then a direction line"),
                          ("sk_cpattern", "Circular pattern — select geometry, then the centre point")):
            self._sketch_buttons[name] = self._tb(h, name, tip, lambda _=False, n=name: self._sk_tool(n),
                                                  checkable=True)
        h.addWidget(_sep())
        b = self._tb(h, "use", "Use (project) part edges (U)", lambda: self._sk_tool("use"), checkable=True)
        self._sketch_buttons["use"] = b
        b = self._tb(h, "trim", "Trim", lambda: self._sk_tool("trim"), checkable=True)
        self._sketch_buttons["trim"] = b
        b = self._tb(h, "extend", "Extend", lambda: self._sk_tool("extend"), checkable=True)
        self._sketch_buttons["extend"] = b
        self._tb(h, "delete", "Delete selected (Del)", lambda: self._sk("delete_selection"))
        h.addStretch(1)
        self._tb(h, "normal_to", "Normal to sketch (N)", self._normal_to_sketch)
        self._tb(h, "fit", "Zoom to fit (F)", lambda: self.viewport.fit())
        done = QToolButton()
        done.setText("✓  Finish sketch")
        done.setStyleSheet("QToolButton { background:%s; color:white; font-weight:600; padding:5px 12px; border-radius:4px; }"
                           "QToolButton:hover { background:%s; }" % (theme.ACCENT, theme.ACCENT_DARK))
        done.clicked.connect(self.finish_sketch)
        h.addWidget(done)
        return w

    def _arrow_icon(self):
        from PySide6.QtGui import QPixmap, QPainter, QPolygonF, QIcon
        from PySide6.QtCore import QPointF
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(2, 2)
        p.setBrush(QColor(theme.ICON_INK))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(10, 5), QPointF(10, 25), QPointF(15, 20), QPointF(19, 28),
                                 QPointF(22, 26.5), QPointF(18, 19), QPointF(24, 19)]))
        p.end()
        pm.setDevicePixelRatio(2)
        return QIcon(pm)

    def _bind_shortcuts(self):
        def sc(key, fn):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WindowShortcut)
            s.activated.connect(fn)
            return s
        sc("Alt+C", self.open_command_search)
        sc("Ctrl+Space", self.open_command_search)
        sc("Alt+`", self.open_command_search)
        sc("Ctrl+Z", self.undo)
        sc("Ctrl+Y", self.redo)
        sc("Ctrl+Shift+Z", self.redo)
        sc("Shift+S", lambda: self.start_sketch() if self.mode == "idle" and not self.is_dwg else None)
        sc("Shift+E", lambda: self.finish_sketch_and_start("extrude") if self.mode == "sketch"
           else self.start_feature("extrude"))
        sc("Shift+W", lambda: self.finish_sketch_and_start("revolve") if self.mode == "sketch"
           else self.start_feature("revolve"))
        sc("Shift+F", lambda: self.asm.start_mate("fastened") if self.is_asm else self.start_feature("fillet"))

        sc("N", lambda: self.dwg.set_tool("note") if self.is_dwg else self._normal_to_sketch())
        sc("Delete", self._delete_key)
        sc("Escape", self._escape)

    # ================================================================== doc → view
    def _dependency_view(self):
        """links.dependency_view of the current tab, or None (a loose file, no Document)."""
        tab = self._folder_tab(self.doc) if self.doc is not None else None
        if tab is None:
            return None
        try:
            return links.dependency_view(self.folder, tab.id)
        except K.KernelError:
            return None

    def _dependency_marks(self):
        """{feature id: state label} for the Derived, Variable Studio reference and Context rows of this studio."""
        view = self._dependency_view()
        return {e["feature"]: e["label"] for e in view["uses"] if e.get("feature")} if view else {}

    def _open_dependency_tab(self, tab_id):
        tab = self.folder.tab(tab_id) if self.folder is not None else None
        i = self._tab_index(tab.doc) if tab is not None and tab.doc is not None else -1
        if i >= 0:
            self.tabs.setCurrentIndex(i)

    def _on_doc_changed(self):
        self.dependencies.populate(self._dependency_view())
        if self.var_tabs.is_variables(self.doc):
            self.var_tabs.on_changed(self.doc)
            return
        if self.is_dwg:
            self.dwg.on_changed()
            name = self.doc.name
            self.doc_label.setText(name + ("  •" if self.doc.dirty else ""))
            self.setWindowTitle(f"{name} — {APP_NAME}")
            self._refresh_tab_title(self.doc)
            return
        if self.is_asm:
            self.asm.on_changed()
            self.btn_aundo.setEnabled(self.doc.can_undo())
            self.btn_aredo.setEnabled(self.doc.can_redo())
            name = self.doc.name
            self.doc_label.setText(name + ("  •" if self.doc.dirty else ""))
            self.setWindowTitle(f"{name} — {APP_NAME}")
            self._refresh_tab_title(self.doc)
            return
        self._populate_lists()
        if self.mode == "idle":
            self._show_state(self.doc.state)
        elif self.mode == "dialog" and hasattr(self.dialog, "schedule_preview"):
            # e.g. a sketch shown/hidden from the feature list while a feature is open: redraw its preview
            self.dialog.schedule_preview()
        self._update_feature_hl()
        self.btn_undo.setEnabled(self.doc.can_undo())
        self.btn_redo.setEnabled(self.doc.can_redo())
        name = self.doc.name
        self.doc_label.setText(name + ("  •" if self.doc.dirty else ""))
        self.setWindowTitle(f"{name} — {APP_NAME}")
        self._refresh_tab_title(self.doc)
        n = len(self.doc.state.parts)
        self.left.parts_header.setText(f"Parts ({n})")
        self.left.feat_header.setText(f"Features ({len(self.doc.features)})")
        errs = len(self.doc.errors)
        if errs:
            self.status.showMessage(f"{errs} feature(s) failed to regenerate — hover them in the feature list for details")

    def _populate_lists(self):
        if self.is_dwg:
            self.dwg.on_changed()
            return
        if self.is_asm:
            self.asm.panel.populate(self.doc)
            return
        editing = None
        if self.mode == "dialog" and self.dialog and not self.dialog.is_new:
            editing = self.dialog.feature["id"]
        if self.mode == "sketch" and self.sk_ctx and not self.sk_ctx["is_new"]:
            editing = self.sk_ctx["feature"]["id"]
        self.left.features.populate(self.doc, editing, self._dependency_marks())
        self.left.parts.populate(self.doc)
        self.defined_vars.populate(self.doc, self._studio_variables)
        self.defined_vars_header.setVisible(self.defined_vars.rowCount() > 0)
        self.defined_vars.setVisible(self.defined_vars.rowCount() > 0)

    def _studio_variables(self, src):
        """{name: value} of the Variable Studio tab a reference's source names (live, as the table shows it)."""
        tab = self.folder.tab(src.get("tab")) if self.folder is not None and src.get("tab") else None
        return None if tab is None or tab.doc is None or not hasattr(tab.doc, "values") else dict(tab.doc.values()[0])

    def _connector_triads(self, state):
        """The Part Studio's mate connectors as the triads the Assembly draws (viewport.triads); a connector
        whose feature row is selected or hovered in the feature list is drawn larger and thicker."""
        picked = set(self.left.features.selected_feature_ids())
        if self._hl_hover:
            picked.add(self._hl_hover)
        out = []
        for fid, c in getattr(state, "connectors", {}).items():
            if fid in self.doc.hidden_features or c["part"] in self.doc.hidden_parts:
                continue
            fr = c["frame"]
            z, x = np.asarray(fr["z"], float), np.asarray(fr["x"], float)
            tri = {"o": np.asarray(fr["o"], float), "x": x, "y": np.cross(z, x), "z": z, "id": fid}
            if fid in picked:
                tri.update(size_px=56, width=5.0)
            out.append(tri)
        return out

    def _planes_for(self, state):
        half = self.viewport.plane_half()
        planes = []
        if self.act_planes.isChecked():
            for name in STD_PLANES:
                if name not in self.doc.hidden_features:
                    planes.append((("std", name), K.std_plane(name), name, half))
        for fid, pl in state.planes.items():
            if fid not in self.doc.hidden_features:
                f = self.doc.feature(fid)
                planes.append((("plane", fid), pl, f["name"] if f else "", half * 0.6))
        return planes

    def _show_state(self, state, active_sketch=None, also_show=()):
        if self.is_dwg:
            return
        if self.is_asm:
            self.asm.show_scene()
            return
        parts = [(p.id, self._flat_or_folded(state, p), p.color,
                  None if self._flat_on(state, p) else p.face_colors, None, None, p.reference)
                 for p in state.parts.values() if p.id not in self.doc.hidden_parts]
        sketches = []
        for fid, sr in state.sketches.items():
            if fid in self.doc.hidden_features and fid != active_sketch and fid not in also_show:
                continue
            sketches.append((fid, sr))
        self.viewport.set_scene(parts, self._planes_for(state), sketches)
        self.viewport.triads = self._connector_triads(state)

    # ================================================================== feature dialogs
    def finish_sketch_and_start(self, ftype):
        """Shift+E / Shift+W while sketching: save the sketch, then open the feature with its regions selected."""
        if self.mode != "sketch":
            return
        if self.sketcher is not None and self.sketcher._editor is not None:
            return                                   # typing in the dimension box
        fid = self.sk_ctx["feature"]["id"]
        self.finish_sketch()
        if self.mode != "idle":
            return
        self.start_feature(ftype, preselect=regions_of_sketch(self.doc.state, fid))

    def start_feature(self, ftype, edit_fid=None, preselect=None):
        if self.is_asm or self.is_dwg:
            return
        if self.mode != "idle":
            if self.mode == "dialog" and self.dialog and self.dialog.is_new and self.dialog.ftype != ftype \
                    and edit_fid is None:
                self.cancel_dialog()
            else:
                return
        if edit_fid:
            feat = copy.deepcopy(self.doc.feature(edit_fid))
            index = self.doc.feature_index(edit_fid)
            is_new = False
            pre = []
        else:
            feat = self.doc.make_feature(ftype)
            index = self.doc.insert_index()
            is_new = True
            pre = list(preselect) if preselect else list(self.viewport.selection)
        self.mode = "dialog"
        self.viewport.feature_hl = None
        self._dialog_sketches = set()        # sketches this dialog has referenced (see show_preview)
        self.dialog = FeatureDialog(self, feat, is_new, index, pre)
        self._adapt_tree(self.dialog)
        self.dialog.accepted.connect(self.commit_dialog)
        self.dialog.rejected.connect(self.cancel_dialog)
        self.viewport.pick_handler = self.dialog.handle_pick
        self.measure.hide()
        self._populate_lists()

    def show_preview(self, feature, index, overlays):
        st, err = self.doc.preview(feature, index)
        p = feature["params"]
        # sketches a feature uses stay visible while it is being edited
        show = {r["sketch"] for r in p.get("regions", [])}
        if p.get("sketch") and isinstance(p.get("sketch"), str):
            show.add(p["sketch"])
        ax = p.get("axis") or {}
        if ax.get("kind") == "sketch_line":
            show.add(ax["sketch"])
        if feature["type"] == "mate_connector":      # every sketch below it, so its geometry can be picked (I1.6)
            show |= set(st.sketches)
        # ...and stay visible after their reference is cleared, so a lost region can be picked again
        self._dialog_sketches = show = getattr(self, "_dialog_sketches", set()) | show
        self._show_state(st, also_show=show)
        self.viewport.overlays = overlays
        self.viewport.update()
        return err

    def commit_dialog(self):
        d = self.dialog
        if d is None:
            return
        f = d.feature
        self._close_dialog()
        if d.is_new:
            # like Onshape, sketches consumed by a new feature are hidden to keep the view clean
            used = {r["sketch"] for r in f["params"].get("regions", [])}
            if f["type"] == "hole" and f["params"].get("sketch"):
                used.add(f["params"]["sketch"])
            for item in f["params"].get("profiles", []) if f["type"] == "loft" else []:
                if item.get("kind") == "region":
                    used.add(item["ref"]["sketch"])
            for r in f["params"].get("path", []) if f["type"] == "sweep" else []:
                if r.get("kind") == "sketch_chain":
                    used.add(r["sketch"])
            self.doc.hidden_features |= used
            self.doc.add_feature(f, d.index)
        else:
            self.doc.replace_feature(f)
        err = self.doc.errors.get(f["id"])
        self.status.showMessage(f"{f['name']}: {err}" if err else f"{f['name']} created" if d.is_new
                                else f"{f['name']} updated", 6000)

    def cancel_dialog(self):
        self._close_dialog()
        self._show_state(self.doc.state)

    def _close_dialog(self):
        if self.dialog:
            self.dialog.hide()
            self.dialog.deleteLater()
        self.dialog = None
        self.mode = "idle"
        vp = self.viewport
        vp.pick_handler = None
        vp.select_filter = None
        vp.snap_provider = None
        vp.clear_snaps()
        vp.overlays = []
        vp.manipulators = []
        vp._manip_drag = None
        vp.clear_selection()
        self._populate_lists()

    def edit_feature(self, fid):
        f = self.doc.feature(fid)
        if f is None or self.mode != "idle":
            return
        if f["type"] == "sketch":
            self.start_sketch(fid)
        elif f["type"] == "import":
            QMessageBox.information(self, APP_NAME, "Imported geometry has no editable parameters.")
        else:
            self.start_feature(f["type"], fid)

    # ================================================================== sketch mode
    def start_sketch(self, edit_fid=None):
        if self.mode != "idle" or self.is_asm or self.is_dwg:
            return
        extra, base = [], None
        if edit_fid:
            feat = copy.deepcopy(self.doc.feature(edit_fid))
            index = self.doc.feature_index(edit_fid)
            is_new = False
            has_plane = True
        else:
            feat = self.doc.make_feature("sketch")
            index = self.doc.insert_index()
            is_new = True
            has_plane = False
            auto_plane = self._plane_for_selection(index)
            if auto_plane is not None:
                # e.g. three vertices are selected: make the plane they define and sketch on it
                pf, base = auto_plane
                extra = [pf]
                feat["params"]["plane"] = {"kind": "plane", "fid": pf["id"]}
                has_plane = True
            else:
                for pk in self.viewport.selection:
                    item = pick_to_item(self.viewport, pk, {"plane"}, getattr(self.doc, "state", None))
                    if item:
                        feat["params"]["plane"] = item[1]
                        has_plane = True
                        break
        self.mode = "sketch"
        self.viewport.feature_hl = None
        self.sk_ctx = {"feature": feat, "index": index, "is_new": is_new,
                       "extra": extra, "base": base if base is not None else self.doc.state_before(index)}
        self.viewport.clear_selection()
        self.measure.hide()
        self._show_state(self.sk_ctx["base"])
        self.dialog = SketchDialog(self, feat, has_plane)
        self._adapt_tree(self.dialog)
        self.dialog.accepted.connect(self.finish_sketch)
        self.dialog.rejected.connect(self.cancel_sketch)
        self.dialog.planePicked.connect(self._sketch_plane_picked)
        self.dialog.show_cons.toggled.connect(self._sketch_display_toggles)
        self.dialog.show_regions.toggled.connect(self._sketch_display_toggles)
        self.dialog.see_through.toggled.connect(self._sketch_see_through_toggled)
        self.viewport.sketch_see_through = self.dialog.see_through.isChecked()
        self.viewport.pick_handler = self.dialog.handle_pick
        if has_plane:
            self._enter_sketcher(animate=is_new)
        else:
            self.viewport.status_text = "Select a plane or planar face to sketch on"
            self.dialog.set_status("Select a plane or a planar face in the graphics area.")
        self._populate_lists()

    # selections that define a plane by themselves, so Sketch can make the plane for you
    AUTO_SKETCH_PLANES = {"three_point", "plane_point", "point_normal", "curve_point", "mid_plane", "tangent"}

    def highlight_element(self, name, kind="face", part=None, hover=False):
        """Show the face / edge / vertex called `name` as selected (or hovered): name -> 1-based index through
        the engine's element-name map -> the viewport pick, whose mesh triangles (faces) are then drawn.
        Returns the Pick, or None when no part has that name."""
        st = getattr(self.doc, "state", None)
        for pid in ([part] if part else list(getattr(st, "engine_names", None) or {})):
            i = element_index(st, pid, kind, name)
            if i is not None:
                pk = Pick(kind, (pid, i - 1))
                if hover:
                    self.viewport.hover = pk
                    self.viewport.update()
                else:
                    self.viewport.set_selection([pk])
                return pk
        return None

    def _plane_for_selection(self, index):
        """When what is selected defines a plane (three vertices, a face and a vertex, two faces …), a new plane
        feature for it and the model state with that plane, so a sketch can start on it. None otherwise —
        including for a single plane or face, which is simply sketched on."""
        vp = self.viewport
        want = {"plane", "face", "edge", "vertex"}
        items = [it for it in (pick_to_item(vp, pk, want, getattr(self.doc, "state", None)) for pk in vp.selection) if it]
        if len(items) < 2:
            return None
        roles = [F.plane_role(k, r) for k, r in items]
        typ = F.detect_plane_type(roles)
        if typ not in self.AUTO_SKETCH_PLANES or (typ == "tangent" and "point" not in roles):
            return None
        pf = self.doc.make_feature("plane")
        pf["params"].update(type="auto", refs=[{"k": k, "ref": r} for k, r in items])
        st, err = self.doc.preview(pf, index)
        if err or pf["id"] not in st.planes:
            return None
        return pf, st

    def _sketch_plane_picked(self, ref):
        self.sk_ctx["feature"]["params"]["plane"] = ref
        self._enter_sketcher(animate=True)

    def _enter_sketcher(self, animate=True):
        ctx = self.sk_ctx
        feat = ctx["feature"]
        try:
            plane = F.resolve_plane(ctx["base"], feat["params"]["plane"])
        except K.KernelError as e:
            self.dialog.set_status(f"Can't use that plane: {e}")
            return
        sk = Sketch.from_dict(feat["params"]["sketch"])
        self.sketcher = Sketcher(plane, sk, feat["id"], self._sketch_changed, self._sketch_tool_changed)
        self.sketcher.variables = dict(ctx["base"].vars)
        self._sketch_display_toggles()
        vp = self.viewport
        vp.sketcher = self.sketcher
        vp.pick_handler = None
        vp.select_filter = None
        self.sketch_waiting_for_plane = False
        self.tool_stack.setCurrentIndex(1)
        self._sketch_tool_changed("select")
        if animate:
            vp.normal_to(plane)
        self._sketch_changed(True)
        vp.setFocus()
        if ctx["is_new"]:
            self._sk_tool("line")

    def _sketch_display_toggles(self):
        if self.sketcher and self.dialog:
            self.sketcher.show_constraints = self.dialog.show_cons.isChecked()
            self.sketcher.show_regions = self.dialog.show_regions.isChecked()
            self.viewport.update()

    def _sketch_see_through_toggled(self, on):
        self.settings.setValue("sketch_see_through", "true" if on else "false")
        self.viewport.sketch_see_through = bool(on)
        self.viewport.update()

    def _sketch_changed(self, final):
        ctx = self.sk_ctx
        if not ctx or not self.sketcher:
            return
        ctx["feature"]["params"]["sketch"] = self.sketcher.sk.to_dict()
        if final:
            st, err = self.doc.preview(ctx["feature"], ctx["index"], ctx.get("extra", ()))
            self._show_state(st, active_sketch=ctx["feature"]["id"])
        st = self.sketcher.sk.status
        if not st.get("ok", True):
            msg, ok = "Conflicting constraints (shown in red)", False
        elif not self.sketcher.sk.entities:
            msg, ok = "", False
        elif st.get("dof", 0) == 0:
            msg, ok = "Sketch is fully defined", True
        else:
            msg, ok = f"Under-defined — {st.get('dof', 0)} degree(s) of freedom (blue geometry can move)", False
        if self.dialog and isinstance(self.dialog, SketchDialog):
            self.dialog.set_status(msg, ok)
        self.viewport.status_text = self.sketcher.hint()
        self.viewport.update()

    def _sketch_tool_changed(self, tool):
        for name, b in self._sketch_buttons.items():
            if name == "construction":
                b.setChecked(bool(self.sketcher and self.sketcher.construction))
            else:
                b.setChecked(name == tool)
        if self.sketcher:
            self.viewport.status_text = self.sketcher.hint()
            self.viewport.update()

    def _sk(self, method):
        if self.sketcher:
            getattr(self.sketcher, method)()
            self.viewport.update()
            self.viewport.setFocus()

    def _sk_tool(self, tool):
        if self.sketcher:
            self.sketcher.set_tool(tool)
            self.viewport.update()
            self.viewport.setFocus()

    def finish_sketch(self):
        if self.mode != "sketch":
            return
        ctx = self.sk_ctx
        feat = ctx["feature"]
        if self.sketcher:
            self.sketcher._close_editor()
            feat["params"]["sketch"] = self.sketcher.sk.to_dict()
        if isinstance(self.dialog, SketchDialog):
            feat["name"] = self.dialog.title.text().strip() or feat["name"]
        self._exit_sketch()
        if self.sketcher is None and ctx["is_new"] and not feat["params"]["sketch"]["entities"]:
            self._show_state(self.doc.state)
            return
        if ctx["is_new"]:
            extra = ctx.get("extra", [])
            for k, ex in enumerate(extra):          # the plane made for this sketch goes in first
                self.doc.add_feature(ex, ctx["index"] + k)
            self.doc.add_feature(feat, ctx["index"] + len(extra))
        else:
            self.doc.replace_feature(feat)
        self.status.showMessage(f"{feat['name']} saved", 5000)

    def cancel_sketch(self):
        self._exit_sketch()
        self._show_state(self.doc.state)

    def _exit_sketch(self):
        if self.sketcher:
            self.sketcher._close_editor()
        self.sketcher = None
        vp = self.viewport
        vp.sketcher = None
        vp.pick_handler = None
        vp.select_filter = None
        vp.status_text = ""
        if self.dialog:
            self.dialog.hide()
            self.dialog.deleteLater()
        self.dialog = None
        self.mode = "idle"
        self.tool_stack.setCurrentIndex(0)
        self._populate_lists()

    def _normal_to_sketch(self):
        if self.sketcher:
            self.viewport.normal_to(self.sketcher.plane)
            return
        for pk in self.viewport.selection:
            item = pick_to_item(self.viewport, pk, {"plane"}, getattr(self.doc, "state", None))
            if item:
                try:
                    self.viewport.normal_to(F.resolve_plane(self.doc.state, item[1]))
                except K.KernelError:
                    pass
                return

    def set_shiny(self, on, save=True):
        self.viewport.shiny = bool(on)
        self.act_shiny.blockSignals(True)
        self.act_shiny.setChecked(bool(on))
        self.act_shiny.blockSignals(False)
        self.viewport.update()
        if save:
            self.settings.setValue("shiny", "true" if on else "false")

    def set_orbit_mode(self, mode, save=True):
        mode = mode if mode in ("cursor", "centre", "origin") else "cursor"
        self.viewport.orbit_mode = mode
        for m_, a in self.orbit_actions.items():
            a.setChecked(m_ == mode)
        if save:
            self.settings.setValue("orbit", mode)

    def set_nav_style(self, mode, save=True):
        mode = mode if mode in navstyles.STYLES else navstyles.DEFAULT_STYLE
        self.viewport.nav_style = mode
        for m_, a in self.nav_actions.items():
            a.setChecked(m_ == mode)
        if save:
            self.settings.setValue("mouse_style", mode)

    def set_zoom_speed(self, name, save=True):
        name = name if name in self.viewport.ZOOM_SPEEDS else "normal"
        self.viewport.zoom_speed = self.viewport.ZOOM_SPEEDS[name]
        for m_, a in self.zoom_actions.items():
            a.setChecked(m_ == name)
        if save:
            self.settings.setValue("zoom_speed", name)

    # ================================================================== theme
    def set_theme(self, mode, save=True):
        from . import icons as ICONS
        theme.apply(mode)
        ICONS.set_ink(theme.ICON_INK)
        self.setStyleSheet(theme.stylesheet())
        self._adapt_tree(self, include_root=False)
        for b in self.findChildren(QToolButton):
            nm = b.property("icon_name")
            if nm == "__arrow":
                b.setIcon(self._arrow_icon())
            elif nm:
                b.setIcon(icon(nm))
        for i in range(self.tabs.count()):
            d = self.docs.get(self.tabs.tabData(i))
            self.tabs.setTabIcon(i, icon("assembly" if getattr(d, "kind", "") == "assembly" else "part", 16))
        for mode_, a in getattr(self, "theme_actions", {}).items():
            a.setChecked(mode_ == theme.MODE)
        if self.doc is not None:
            self._populate_lists()
        self.viewport.update()
        if save:
            self.settings.setValue("theme", theme.MODE)

    def _adapt_tree(self, root, include_root=True):
        """Re-colour inline widget styles (written with light colours) for the current theme."""
        widgets = ([root] if include_root else []) + root.findChildren(QWidget)
        for w_ in widgets:
            css = w_.property("light_css")
            if css is None:
                css = w_.styleSheet()
                if not css:
                    continue
                w_.setProperty("light_css", css)
            w_.setStyleSheet(theme.adapt(css))

    # ================================================================== recent files
    def _recent(self):
        v = self.settings.value("recent", [])
        if isinstance(v, str):
            v = [v]
        return [x for x in (v or []) if isinstance(x, str)]

    def _recent_times(self):
        import json
        try:
            return json.loads(str(self.settings.value("recent_times", "{}")))
        except Exception:
            return {}

    def _add_recent(self, path):
        import json, time
        if not path:
            return
        path = os.path.abspath(path)
        lst = [path] + [x for x in self._recent() if os.path.abspath(x) != path]
        lst = lst[:30]
        self.settings.setValue("recent", lst)
        times = self._recent_times()
        times[path] = time.time()
        self.settings.setValue("recent_times", json.dumps({k: v for k, v in times.items() if k in lst}))
        if self.main_stack.currentIndex() == 1:
            self._refresh_home()

    def _remove_recent(self, path):
        self.settings.setValue("recent", [x for x in self._recent() if os.path.abspath(x) != os.path.abspath(path)])
        self._refresh_home()

    def _clear_recent(self):
        if QMessageBox.question(self, APP_NAME, "Clear the list of recent files? (The files themselves are kept.)") \
                == QMessageBox.Yes:
            self.settings.setValue("recent", [])
            self._refresh_home()

    # ------------------------------------------------------------------ home page
    def _refresh_home(self):
        times = self._recent_times()
        items = [(p, times.get(os.path.abspath(p), 0), self._document_info(p)) for p in self._recent()]
        back = None
        for i in range(self.tabs.count()):
            d = self.docs.get(self.tabs.tabData(i))
            if d is not None and (self._has_content(d) or d.path):
                back = self.doc.name if self.doc is not None and (self._has_content(self.doc) or self.doc.path) \
                    else d.name
                break
        self.home.set_recent(items, back)

    def show_home(self):
        if self.main_stack.currentIndex() == 0:
            self._leave_modes()
            self._update_thumbnail()
        self._refresh_home()
        self.main_stack.setCurrentIndex(1)
        self.doc_label.setText("Home")
        self.setWindowTitle(APP_NAME)

    def show_workspace(self):
        if self.main_stack.currentIndex() != 0:
            self.main_stack.setCurrentIndex(0)
            if self.doc is not None:
                self.setWindowTitle(f"{self.doc.name} — {APP_NAME}")
                self.doc_label.setText(self.doc.name + ("  •" if self.doc.dirty else ""))
                self.viewport.update()

    def _home_new_studio(self):
        if self._is_pristine(self.doc) and self.doc.kind == "studio":
            self.show_workspace()
        else:
            self.new_doc()

    def open_any(self, path):
        """Open a FreeShape document, or import a STEP file — whatever the file is."""
        if not os.path.exists(path):
            QMessageBox.information(self, APP_NAME, f"“{os.path.basename(path)}” can't be found — it may have "
                                    "been moved or deleted.")
            return
        if os.path.isdir(path):
            if self.open_document(path):
                self.show_workspace()
            return
        ext = os.path.splitext(path)[1].lower()
        if ext in (".step", ".stp"):
            if self.mode != "idle":
                self._leave_modes()
            self.import_step_file(path)
        elif TK.kind_for_ext(ext) is not None or ext == ".fcad":
            self.open_file(path)
        else:
            QMessageBox.information(self, APP_NAME, f"FreeShape opens {TK.extension_list()} documents and STEP files.")
            return
        self.show_workspace()

    def _thumb_key(self, doc):
        return self._doc_key(doc) or doc.path or getattr(doc, "_thumb_for", None)

    def _update_thumbnail(self, doc=None):
        """Save a picture of the current model for its card on the home page."""
        from .home import thumb_path
        doc = doc or self.doc
        if doc is None or doc is not self.doc or self.mode != "idle" or self.main_stack.currentIndex() != 0:
            return
        key = self._thumb_key(doc)
        if not key:
            return
        if isinstance(key, tuple):
            self._note_document_tab(self.folder, key[1])     # the tab its recent card shows
        try:
            img = self.dwg.thumbnail() if doc.kind == "drawing" else self.viewport.render_thumbnail()
            if img is not None:
                if isinstance(key, tuple):               # a Document tab: keyed by Document id + tab id
                    from .home import keyed_thumb_path
                    img.save(keyed_thumb_path(*key))
                else:
                    img.save(thumb_path(key))
        except Exception:
            pass

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            if url.toLocalFile():
                self.open_any(url.toLocalFile())

    def _fill_recent(self):
        m = self.recent_menu
        m.clear()
        lst = [x for x in self._recent() if os.path.exists(x)]
        if not lst:
            a = m.addAction("No recent documents")
            a.setEnabled(False)
            return
        for path in lst:
            a = m.addAction(os.path.basename(path), lambda p=path: self.open_any(p))
            a.setToolTip(path)
        m.addSeparator()
        m.addAction("Clear list", self._clear_recent)

    # ================================================================== folder Documents
    def _folder_tab(self, doc):
        """The manifest tab (doc_folder.Tab) this document is, or None for a loose file."""
        tid = getattr(doc, "tab_id", None)
        if self.folder is None or tid is None:
            return None
        tab = self.folder.tab(tid)
        return tab if tab is not None and tab.doc is doc else None

    def _doc_key(self, doc):
        """(Document id, tab id) for a Document tab -- what recents, thumbnails and autosaves are keyed by."""
        tab = self._folder_tab(doc)
        return (self.folder.id, tab.id) if tab is not None else None

    def _resolver_for(self, doc):
        return self.folder.resolver if self._folder_tab(doc) is not None else self.resolver

    def studio_link(self, studio, part=None, **extra):
        """The source an assembly instance or drawing stores for a Part Studio tab: tab id + part inside a
        Document, uid + path for a loose file."""
        tab = self._folder_tab(studio)
        if tab is not None:
            return {**links.studio_source(tab, part), **extra}
        return {"kind": "studio", "uid": studio.uid, "part": part, "path": studio.path,
                "studio_name": studio.name, **extra}

    def assembly_link(self, asm):
        tab = self._folder_tab(asm)
        if tab is not None:
            return links.assembly_source(tab)
        return {"kind": "assembly", "uid": asm.uid, "path": asm.path, "name": asm.name}

    def show_linked_studio(self, src):
        """The Part Studio a source names, switched to (a Document tab by id, else the loose uid + path way)."""
        if src.get("tab") is not None and self.folder is not None:
            tab = self.folder.tab(src["tab"])
            if tab is None or tab.doc is None:
                return None
            if self._tab_index(tab.doc) < 0:
                self._add_tab(tab.doc)
            else:
                self.tabs.setCurrentIndex(self._tab_index(tab.doc))
            return tab.doc
        return self.studio_by_uid(src.get("uid"), src.get("path"), load=True, show=True)

    def _register_in_document(self, doc, like=None):
        """A tab made while a Document is open goes into that Document (`like`: the tab it copies, else the
        current one)."""
        ref = like if like is not None else self.doc
        if ref is None or self._folder_tab(ref) is None:
            return
        tab = self.folder.adopt_tab(doc, doc.kind)
        doc.tab_id = tab.id
        if doc.kind == "assembly":
            doc.refresh(solve=False, notify=False)
        elif doc.kind == "drawing":
            doc.refresh(notify=False)

    def _document_info(self, path):
        """{"id", "name", "tab"} for a recent entry that is a Document folder, else None."""
        data = doc_folder.FolderDocument.peek_manifest(path)
        if data is None:
            return None
        did = data.get("id")
        info = self._recent_documents().get(did, {}) if isinstance(did, str) else {}
        return {"id": did, "name": data.get("name") or os.path.basename(path), "tab": info.get("tab")}

    def _recent_documents(self):
        import json
        try:
            v = json.loads(str(self.settings.value("recent_documents", "{}")))
        except Exception:
            return {}
        return v if isinstance(v, dict) else {}

    def _note_document_tab(self, fd, tab_id):
        """Remember which tab of a Document was worked on last, keyed by Document id (its card's thumbnail)."""
        import json
        if fd is None:
            return
        docs = self._recent_documents()
        docs[fd.id] = {"path": os.path.abspath(fd.path), "name": fd.name, "tab": tab_id}
        self.settings.setValue("recent_documents", json.dumps(docs))

    def _pick_new_document_path(self):
        path, _ = QFileDialog.getSaveFileName(self, "New Document (a folder)",
                                              self._dialog_dir("doc", "Document 1"), "Document folder")
        return path

    def _pick_document_path(self):
        return QFileDialog.getExistingDirectory(self, "Open Document folder", self._dialog_dir("doc"))

    def _pick_files_to_add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add to Document", self._dialog_dir("doc"),
                                                "FreeShape files (" + " ".join(TK.glob(k) for k in TK.kinds()) + ")")
        return paths

    def _home_new_document(self):
        self._leave_modes()
        path = self._pick_new_document_path()
        if path:
            self.create_document(path)

    def open_document_dialog(self):
        self._leave_modes()
        path = self._pick_document_path()
        if path:
            self.open_any(path)

    def create_document(self, path, name=None):
        """Make a new Document folder with one empty Part Studio tab and open it."""
        if os.path.isdir(path) and os.listdir(path):
            QMessageBox.warning(self, APP_NAME, f"“{os.path.basename(path)}” already has files in it. "
                                "Pick a new folder name for the Document.")
            return None
        if not self._close_document():
            return None
        try:
            fd = doc_folder.FolderDocument.create(path, name or os.path.basename(os.path.normpath(path)))
            tab = fd.add_tab("studio", "Part Studio 1")
            tab.doc.rebuild()
            fd.save()
        except (K.KernelError, OSError) as e:
            QMessageBox.critical(self, APP_NAME, f"Could not create the Document:\n{e}")
            return None
        self._adopt_document(fd)
        self.show_workspace()
        self._note_git_problem(fd)
        return fd

    def open_document(self, path):
        """Open a Document folder: one tab per manifest entry."""
        if self.folder is not None and os.path.abspath(self.folder.path) == os.path.abspath(path):
            return self.folder
        if not self._close_document():
            return None
        try:
            fd = doc_folder.FolderDocument.open_document(path, registry=doc_registry.default_registry())
        except (K.KernelError, OSError, ValueError) as e:
            QMessageBox.critical(self, APP_NAME, f"Could not open the Document {path}:\n{e}")
            return None
        self._adopt_document(fd)
        self.show_workspace()
        self._note_git_problem(fd)
        if fd.errors:
            QMessageBox.warning(self, APP_NAME, "Some tabs could not be opened:\n\n" + "\n".join(
                f"• {fd.tab(t).name if fd.tab(t) else t}: {m}" for t, m in fd.errors.items()))
        return fd

    def _adopt_document(self, fd):
        """Show every tab of an opened Document and make it the open Document."""
        self.folder = fd
        fd.resolver.flat_pattern_of = self.flat_pattern_of
        loose_blank = [d for d in self.docs.values() if self._is_pristine(d)]
        for tab in fd.tabs:
            if tab.doc is not None:
                tab.doc.tab_id = tab.id
                tab.doc.name = tab.name or tab.doc.name         # the manifest names the tab
                self._add_tab(tab.doc, switch=False)
        last = self._recent_documents().get(fd.id, {}).get("tab")
        active = fd.tab(last) if fd.tab(last) is not None and fd.tab(last).doc is not None else \
            next((t for t in fd.tabs if t.doc is not None), None)
        if active is not None:
            self.tabs.setCurrentIndex(self._tab_index(active.doc))
        for d in loose_blank:
            i = self._tab_index(d)
            if i >= 0:
                self.tabs.removeTab(i)
            self.docs.pop(id(d), None)
            self._views.pop(id(d), None)
        self._note_document_tab(fd, active.id if active is not None else None)
        self._add_recent(fd.path)
        self._refresh_workspaces()

    def _close_document(self):
        """Close the open Document's tabs (asking about unsaved ones). False if the person cancelled."""
        fd = self.folder
        if fd is None:
            return True
        self._leave_modes()
        self.versions_ui.close_view()
        shown = [t.doc for t in fd.tabs if t.doc is not None and self._tab_index(t.doc) >= 0]
        for doc in shown:
            if doc.dirty and self._has_content(doc):
                self.tabs.setCurrentIndex(self._tab_index(doc))
                if not self._confirm_discard(doc):
                    return False
        if self.tabs.count() == len(shown):
            self._add_tab(self._new_document(), switch=False)
            self.tabs.setCurrentIndex(self.tabs.count() - 1)
        for doc in shown:
            self._drop_autosave(doc)
            self._remove_tab_silently(doc)
        self.folder = None
        self._refresh_workspaces()
        return True

    def _remove_tab_silently(self, doc):
        i = self._tab_index(doc)
        if i >= 0:
            self.tabs.removeTab(i)
        self.docs.pop(id(doc), None)
        self._views.pop(id(doc), None)

    def add_to_document(self, paths=None):
        """Copy loose tab files (see tabkinds) into the open Document and rewrite the links in the copies
        to tab ids. The originals are not touched. Saves the Document, since the copies are new files."""
        if self.folder is None:
            QMessageBox.information(self, APP_NAME, "Open or create a Document first, then add files to it.")
            return []
        if paths is None:
            paths = self._pick_files_to_add()
        if not paths:
            return []
        self._leave_modes()
        for tab in self.folder.tabs:                          # the folder save below writes every dirty tab
            if tab.doc is not None:
                tab.name = tab.doc.name
        try:
            added = self.folder.add_files(paths)
        except (K.KernelError, OSError, ValueError) as e:
            QMessageBox.warning(self, APP_NAME, f"Couldn't add to the Document:\n{e}")
            return []
        for tab in added:
            tab.doc.tab_id = tab.id
            self._add_tab(tab.doc, switch=False)
        for tab in self.folder.tabs:
            if tab.doc is not None:
                self._refresh_tab_title(tab.doc)
        self.tabs.setCurrentIndex(self._tab_index(added[0].doc))
        self._on_doc_changed()
        self.status.showMessage(f"Added {len(added)} file(s) to the Document", 5000)
        return added

    def _save_document(self):
        fd = self.folder
        for tab in fd.tabs:
            if tab.doc is not None:
                tab.name = tab.doc.name
                if tab.kind in ("assembly", "drawing"):
                    links.repair_links(fd.resolver, tab.doc)
        try:
            fd.save()
        except OSError as e:
            QMessageBox.critical(self, APP_NAME, f"Could not save:\n{e}")
            return False
        for tab in fd.tabs:
            if tab.doc is not None:
                self._refresh_tab_title(tab.doc)
                self._drop_autosave(tab.doc)
        self._on_doc_changed()
        self._update_thumbnail()
        self._add_recent(fd.path)
        self.status.showMessage(f"Saved {fd.path}", 4000)
        self._refresh_versions()
        return True

    # ================================================================== Workspaces (git branches of the Document)
    def _build_workspace_switcher(self, layout):
        """The top-bar switcher: a combo box of the Document's Workspaces and a button to start a new one from a version."""
        from PySide6.QtWidgets import QComboBox
        self.workspace_label = QLabel("Workspace")
        self.workspace_label.setObjectName("muted")
        self.workspace_combo = QComboBox()
        self.workspace_combo.setObjectName("workspaceSwitcher")
        self.workspace_combo.setMinimumWidth(120)
        self.workspace_combo.setToolTip("Workspace: a line of work in this Document")
        self.workspace_combo.currentIndexChanged.connect(self._workspace_chosen)
        self.workspace_new = QToolButton()
        self.workspace_new.setObjectName("menuButton")
        self.workspace_new.setText("+ Workspace")
        self.workspace_new.setToolTip("New Workspace from a version")
        self.workspace_new.clicked.connect(lambda: self.new_workspace_dialog())
        self._ws_updating = False
        self._ws_widgets = (self.workspace_label, self.workspace_combo, self.workspace_new)
        layout.addSpacing(8)
        for wdg in self._ws_widgets:
            layout.addWidget(wdg)
            wdg.setVisible(False)

    def _refresh_workspaces(self):
        """Fill the switcher from the open Document (hidden when there is none)."""
        fd = self.folder
        combo = self.workspace_combo
        self._ws_updating = True
        try:
            combo.clear()
            problem = fd.git_problem() if fd is not None else None
            if fd is not None and problem is None:
                try:
                    names, current = fd.workspaces(), fd.current_workspace()
                except K.KernelError as e:
                    problem = str(e)
            if fd is not None and problem is None:
                combo.addItems(names)
                combo.setCurrentIndex(max(0, combo.findText(current)))
            for wdg in self._ws_widgets:
                wdg.setVisible(fd is not None)
                wdg.setEnabled(problem is None)
            if problem:
                combo.setToolTip(f"Workspaces are unavailable: {problem}")
            else:
                combo.setToolTip("Workspace: a line of work in this Document")
            self._refresh_merge_ui()
        finally:
            self._ws_updating = False
        self._refresh_versions()

    def _note_git_problem(self, fd):
        """Tell the person, without a dialog, when a Document has no repository (git missing, or none in the folder)."""
        problem = fd.git_problem() if fd is not None else None
        if problem:
            self.status.showMessage(f"Versions and Workspaces are off for this Document: {problem}", 15000)

    def _workspace_chosen(self, index):
        if self._ws_updating or self.folder is None or index < 0:
            return
        self.switch_workspace(self.workspace_combo.itemText(index))

    def _workspace_dirty_choice(self, what):
        """How to treat unsaved tabs before a Workspace change: "raise" (none to worry about), "discard", or None
        when the person cancelled (or chose Save and the save failed). Save is done here."""
        fd = self.folder
        unsaved = fd.unsaved_tabs()
        if not unsaved:
            return "raise"
        r = QMessageBox.question(self, APP_NAME, f"Save changes to {', '.join(f'“{n}”' for n in unsaved)} before "
                                 f"{what}?", QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return "raise" if self._save_document() else None
        return "discard" if r == QMessageBox.Discard else None

    def _workspace_op(self, what, op):
        """Run a Workspace change (`op(dirty)` on the Document) behind the dirty guard, then reload the tabs."""
        fd = self.folder
        try:
            if fd is None:
                return False
            self._leave_modes()
            self.versions_ui.close_view()
            dirty = self._workspace_dirty_choice(what)
            if dirty is None:
                return False
            old = [t.doc for t in fd.tabs if t.doc is not None and self._tab_index(t.doc) >= 0]
            cur = self._folder_tab(self.doc)
            before = fd.current_workspace()
            try:
                op(dirty)
            except doc_folder.DirtyWorkspaceError as e:
                QMessageBox.warning(self, APP_NAME, str(e))
                return False
            except (K.KernelError, OSError) as e:
                QMessageBox.warning(self, APP_NAME, f"Couldn't change Workspace:\n{e}")
                return False
            if fd.current_workspace() != before:
                self._reload_document_tabs(old, cur.id if cur is not None else None)
                self.status.showMessage(f"Workspace: {fd.current_workspace()}", 5000)
            return True
        finally:
            self._refresh_workspaces()

    def _reload_document_tabs(self, old_docs, tab_id):
        """Replace every shown tab of the open Document with the freshly loaded ones, keeping the same tab current."""
        fd = self.folder
        if self.studio_engine is not None:
            self.studio_engine.live.reset(old_docs)      # close the old tabs' engine tabs: the Link graph starts again
        for doc in old_docs:
            self._drop_autosave(doc)
        self._note_document_tab(fd, tab_id)
        self._adopt_document(fd)
        for doc in old_docs:
            self._remove_tab_silently(doc)
        self._on_doc_changed()

    def switch_workspace(self, name):
        """Switch the open Document to Workspace `name`. Unsaved tabs bring up Save / Discard / Cancel first."""
        fd = self.folder
        if fd is None or name == fd.current_workspace():
            self._refresh_workspaces()
            return fd is not None
        return self._workspace_op(f"switching to Workspace “{name}”", lambda dirty: fd.switch_workspace(name, dirty))

    def create_workspace(self, name, version):
        """Start Workspace `name` from version `version` of the open Document and switch to it."""
        fd = self.folder
        if fd is None:
            return False
        return self._workspace_op(f"starting Workspace “{name}”",
                                  lambda dirty: fd.create_workspace(name, version, dirty=dirty))

    def new_workspace_dialog(self):
        """Ask for a version and a name, then create the Workspace."""
        fd = self.folder
        if fd is None:
            return False
        try:
            versions = fd.versions()
        except K.KernelError as e:
            QMessageBox.warning(self, APP_NAME, f"Workspaces are unavailable:\n{e}")
            return False
        if not versions:
            QMessageBox.information(self, APP_NAME, "A Workspace starts from a version. Create a version first.")
            return False
        labels = [v["name"] + (f" — {v['note']}" if v["note"] and v["note"] != v["name"] else "") for v in versions]
        label, ok = QInputDialog.getItem(self, APP_NAME, "Start from version:", labels, len(labels) - 1, False)
        if not ok:
            return False
        name, ok = QInputDialog.getText(self, APP_NAME, "Workspace name:")
        if not ok or not name.strip():
            return False
        return self.create_workspace(name.strip(), versions[labels.index(label)]["name"])

    # ================================================================== Versions (create, list, read-only view, link updates)
    def _build_versions_bar(self, layout):
        """Top-bar buttons next to the Workspace switcher; the dialogs and the read-only view live in versions_ui.py."""
        from .versions_ui import VersionsUI
        self.versions_ui = VersionsUI(self)
        self.versions_ui.build(layout)

    def _refresh_versions(self):
        """Recompute the Versions buttons and the update badge (on open, close, save and Workspace change)."""
        self.versions_ui.refresh()

    def create_version_dialog(self):
        return self.versions_ui.create_dialog()

    def versions_dialog(self):
        return self.versions_ui.list_dialog()

    def updates_dialog(self):
        return self.versions_ui.updates_dialog()

    # ================================================================== Merge (another Workspace into this one)
    def _build_merge_button(self, layout):
        """The top-bar Merge button, next to the Workspace switcher."""
        self.merge_btn = QToolButton()
        self.merge_btn.setObjectName("menuButton")
        self.merge_btn.setText("Merge…")
        self.merge_btn.setToolTip("Merge another Workspace into this one")
        self.merge_btn.clicked.connect(lambda: self.merge_dialog())
        layout.addWidget(self.merge_btn)
        self.merge_btn.setVisible(False)
        self._show_document_warnings(None)

    def _refresh_merge_ui(self):
        """Show the Merge button with a Document that has Workspaces, and the open Document's warnings."""
        fd = self.folder
        btn = getattr(self, "merge_btn", None)
        if btn is not None:
            btn.setVisible(fd is not None)
            btn.setEnabled(fd is not None and not fd.read_only and fd.git_problem() is None)
        self._show_document_warnings(fd)

    def _show_document_warnings(self, fd):
        """Put the Document's warnings (found on open: dangling links, tab files missing from the manifest or the
        folder) in a label on the status bar; the tooltip lists them all. Hidden when there are none."""
        lab = getattr(self, "doc_warnings", None)
        if lab is None:
            lab = self.doc_warnings = QLabel()
            lab.setObjectName("documentWarnings")
            lab.setStyleSheet(f"color: {theme.ERROR}; padding: 0 6px;")
            lab.setMaximumWidth(720)
            self.statusBar().addPermanentWidget(lab)
        warns = getattr(fd, "warnings", None) or []
        if not warns:
            lab.clear()
            lab.setToolTip("")
            lab.setVisible(False)
            return
        lab.setText(f"⚠ {len(warns)} Document warning{'s' if len(warns) > 1 else ''}: " +
                    "; ".join(w["message"] for w in warns))
        lab.setToolTip("\n".join(f"• {w['message']}" for w in warns))
        lab.setVisible(True)

    def merge_dialog(self):
        """Ask which Workspace to merge into the current one, then preview and merge it."""
        fd = self.folder
        if fd is None:
            return False
        try:
            current = fd.current_workspace()
            names = [n for n in fd.workspaces() if n != current]
        except K.KernelError as e:
            QMessageBox.warning(self, APP_NAME, f"Merging is unavailable:\n{e}")
            return False
        if not names:
            QMessageBox.information(self, APP_NAME, "There is no other Workspace to merge. Make one with + Workspace.")
            return False
        name, ok = QInputDialog.getItem(self, APP_NAME, f"Merge into “{current}” from Workspace:", names, 0, False)
        if not ok:
            return False
        return self.merge_workspace(name)

    def merge_workspace(self, name, choose=None):
        """Merge Workspace `name` into the current one: preview it, let the person pick ours or theirs for each tab
        changed on both sides (the Merge button stays off while the merged tree has a problem such as a dangling
        link), then commit and reload the tabs. `choose(preview)` stands in for the dialog (tests): it makes the
        choices and returns True to merge."""
        from .. import doc_merge
        fd = self.folder
        if fd is None:
            return False
        self._leave_modes()
        try:
            dirty = self._workspace_dirty_choice(f"merging Workspace “{name}”")
            if dirty is None:
                return False
            try:
                preview = fd.merge_preview(name, dirty)
            except (K.KernelError, OSError) as e:
                QMessageBox.warning(self, APP_NAME, f"Couldn't preview the merge:\n{e}")
                return False
            if preview.up_to_date:
                QMessageBox.information(self, APP_NAME, f"“{preview.ours}” already has everything in “{name}”.")
                return False
            if choose is not None:
                ok = choose(preview)
            else:
                ok = self._merge_preview_dialog(preview).exec() == QDialog.Accepted
            if not ok:
                return False
            old = [t.doc for t in fd.tabs if t.doc is not None and self._tab_index(t.doc) >= 0]
            cur = self._folder_tab(self.doc)
            try:
                fd.merge_workspace(name, preview.choices, dirty)
            except doc_merge.MergeRefused as e:
                QMessageBox.warning(self, APP_NAME, str(e))
                return False
            except (K.KernelError, OSError) as e:
                QMessageBox.warning(self, APP_NAME, f"Couldn't merge:\n{e}")
                return False
            self._reload_document_tabs(old, cur.id if cur is not None else None)
            self.status.showMessage(f"Merged Workspace “{name}” into “{preview.ours}”", 5000)
            return True
        finally:
            self._refresh_workspaces()

    def _merge_preview_dialog(self, preview):
        """The merge preview: tabs taken from one side, an ours / theirs choice per tab changed on both sides, and
        the problems that block the merge. Merge is enabled only when there are none."""
        from PySide6.QtWidgets import QButtonGroup, QDialogButtonBox, QGridLayout, QRadioButton
        p = preview
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Merge “{p.theirs}” into “{p.ours}”")
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(f"Merge Workspace “{p.theirs}” into “{p.ours}”."))
        if p.auto:
            side = {"ours": f"from “{p.ours}”", "theirs": f"from “{p.theirs}”", "both": "from both"}
            v.addWidget(QLabel("Taken without asking:\n" + "\n".join(
                f"• {a['name']} — {a['detail']} ({side[a['side']]})" for a in p.auto)))
        dlg.radios = {}
        if p.conflicts:
            v.addWidget(QLabel("Changed in both Workspaces — choose which to keep:"))
            grid = QGridLayout()
            for row, c in enumerate(p.conflicts):
                grid.addWidget(QLabel(f"{c.name}  ({c.detail})"), row, 0)
                group = QButtonGroup(dlg)
                for col, which, text in ((1, "ours", f"Keep “{p.ours}”"), (2, "theirs", f"Take “{p.theirs}”")):
                    rb = QRadioButton(text)
                    rb.setChecked(p.choices.get(c.key) == which)
                    group.addButton(rb)
                    grid.addWidget(rb, row, col)
                    rb.toggled.connect(lambda on, k=c.key, s=which: on and (p.choose(k, s), update()))
                    dlg.radios[(c.key, which)] = rb
            v.addLayout(grid)
        problems = QLabel()
        problems.setObjectName("mergeProblems")
        problems.setWordWrap(True)
        v.addWidget(problems)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        merge = buttons.button(QDialogButtonBox.Ok)
        merge.setText("Merge")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        v.addWidget(buttons)

        def update():
            blocking = p.blocking
            problems.setStyleSheet(f"color: {theme.ERROR};" if blocking else "")
            problems.setText("Can't merge yet:\n" + "\n".join(f"• {b}" for b in blocking) if blocking
                             else "Ready to merge.")
            merge.setEnabled(p.clean)

        update()
        dlg.preview, dlg.problems_label, dlg.merge_button = p, problems, merge
        return dlg

    def _recover_document_tab(self, path, meta):
        """Put an autosaved Document tab back into its Document. False if the Document is gone (the caller then
        recovers it as a loose file)."""
        info = meta["document"]
        folder = info.get("path")
        if not folder or not doc_folder.FolderDocument.has_manifest_at(folder):
            return False
        if self.folder is None or self.folder.id != info.get("id"):
            self.open_document(folder)
            if self.folder is None or self.folder.id != info.get("id"):
                return False
        kind = info.get("kind")
        if kind not in doc_folder.LOADERS:
            return False
        doc = self.folder.recover_tab(kind, os.path.basename(path), info.get("tab"), os.path.dirname(path))
        doc.name = info.get("tab_name") or meta.get("name") or doc.name
        doc.dirty = True
        old = None
        tab = self.folder.tab(info.get("tab"))
        if tab is None:
            tab = self.folder.adopt_tab(doc, kind, info.get("tab"), doc.name, info.get("file"))
        else:
            old, tab.doc = tab.doc, doc
            self.folder._attach(tab)
        doc.tab_id = tab.id
        self._add_tab(doc)
        if old is not None:
            self._remove_tab_silently(old)
        self.folder.refresh_links()
        return True

    # ================================================================== autosave & recovery
    @staticmethod
    def autosave_root():
        from PySide6.QtCore import QStandardPaths
        # a fixed location (not tied to the app name Qt was given) so every start finds the same folder
        generic = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)
        base = os.path.join(generic, "FreeShape") if generic else os.path.expanduser("~/.freeshape")
        d = os.path.join(base, "autosave")
        os.makedirs(d, exist_ok=True)
        return d

    @classmethod
    def autosave_dir(cls):
        """This running copy's own folder, so two FreeShape windows never touch each other's autosaves."""
        d = os.path.join(cls.autosave_root(), str(os.getpid()))
        os.makedirs(d, exist_ok=True)
        return d

    @staticmethod
    def _process_alive(pid):
        if pid == os.getpid():
            return True
        if sys.platform == "win32":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return bool(ok) and code.value == 259                       # STILL_ACTIVE
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _orphaned_autosaves(self):
        """Autosave files left behind by FreeShape windows that are no longer running (i.e. crashed)."""
        root = self.autosave_root()
        out = []
        for name in os.listdir(root):
            full = os.path.join(root, name)
            if name.endswith(TK.extensions()) and os.path.isfile(full):
                out.append(full)                                          # older single-folder layout
            elif os.path.isdir(full) and name.isdigit() and not self._process_alive(int(name)):
                out += [os.path.join(full, f) for f in os.listdir(full) if f.endswith(TK.extensions())]
                docstore.sweep_side_blobs(full)            # blobs no autosave there names
                if not os.listdir(full):
                    try:
                        os.rmdir(full)
                    except OSError:
                        pass
        return sorted(out)

    def _autosave_path(self, doc):
        import re
        import uuid
        key = self._doc_key(doc)
        if key:                                          # a Document tab: one file per Document id + tab id
            doc._autosave_id = ".".join(re.sub(r"[^A-Za-z0-9_-]", "_", str(k)) for k in key)
        elif not getattr(doc, "_autosave_id", None):
            doc._autosave_id = uuid.uuid4().hex
        ext = (TK.find(doc.kind) or TK.get("studio")).ext
        return os.path.join(self.autosave_dir(), doc._autosave_id + ext)

    def autosave(self):
        import json, time
        for doc in list(self.docs.values()):
            if not (doc.dirty and self._has_content(doc)):
                continue
            try:
                links.repair_links(self._resolver_for(doc), doc)
                if hasattr(doc, "write_blobs"):         # the blobs its data names go first (I4.9)
                    doc.write_blobs(store=self._autosave_blobs(doc))
                d = json.loads(doc.to_json())
                d["autosave"] = {"path": doc.path, "name": doc.name, "time": time.time()}
                tab = self._folder_tab(doc)
                if tab is not None:
                    d["autosave"]["document"] = {"id": self.folder.id, "name": self.folder.name,
                                                 "path": self.folder.path, "tab": tab.id, "kind": tab.kind,
                                                 "file": tab.file, "tab_name": tab.name}
                if tab is not None:
                    doc._autosave_id = self.folder.autosave_id(tab.id)
                    self.folder.autosave_tab(tab.id, tab.kind, json.dumps(d).encode("utf-8"),
                                             where=self.autosave_dir())
                    continue
                tmp = self._autosave_path(doc) + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump(d, fh)
                os.replace(tmp, self._autosave_path(doc))
            except Exception:
                pass

    def _autosave_blobs(self, doc):
        """Where a doc's autosave keeps the blobs its data names, each written once: the store's side blobs for a
        Document tab (FolderDocument.autosave_blobs), `blobs/` beside the autosave file for a loose file -- the
        same folder for a Document in a folder. Recovery reads them there; dropping the autosave sweeps them."""
        if self._folder_tab(doc) is not None:
            return self.folder.autosave_blobs(self.autosave_dir())
        return docstore.NewBlobsOnly(blobstore.store_for(self._autosave_path(doc)))

    def _drop_autosave(self, doc):
        if doc is not None and getattr(doc, "_autosave_id", None):
            try:
                tab = self._folder_tab(doc)
                if tab is not None:
                    self.folder.drop_autosave(tab.id, tab.kind, where=self.autosave_dir())
                    return
                os.remove(self._autosave_path(doc))
                docstore.sweep_side_blobs(self.autosave_dir())
            except OSError:
                pass

    def offer_recovery(self):
        import json, time
        files = self._orphaned_autosaves()
        if not files:
            return
        names = []
        for f in files:
            try:
                with open(f) as fh:
                    meta = json.load(fh).get("autosave", {})
                where = meta.get("document")
                label = f"{meta.get('name', 'Untitled')}" + (
                    f" (tab “{where.get('tab_name')}” of Document “{where.get('name')}”)" if where else "")
                names.append(f"• {label}  —  saved {time.strftime('%d %b %H:%M', time.localtime(meta.get('time', 0)))}")
            except Exception:
                names.append("• (unreadable)")
        r = QMessageBox.question(
            self, APP_NAME, "FreeShape didn't close properly last time. Recover these unsaved documents?\n\n"
            + "\n".join(names), QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        for f in files:
            if r == QMessageBox.Yes:
                try:
                    with open(f) as fh:
                        meta = json.load(fh).get("autosave", {})
                    if meta.get("document") and self._recover_document_tab(f, meta):
                        self.folder.discard_autosave(os.path.basename(f), os.path.dirname(f))
                        continue
                    kind = TK.kind_for_path(f)
                    doc = AssemblyDoc.load(f, resolver=self._resolve_source) if kind == "assembly" \
                        else DrawingDoc.load(f, resolver=self._resolve_drawing_source,
                                                      hlr_async=self.dwg.request_exact) if kind == "drawing" \
                        else TK.get(kind or "studio").load(f)
                    doc_folder.hold_blobs(doc)                  # its blobs are beside `f`, removed below
                    doc.path = meta.get("path")
                    doc.name = (meta.get("name") or "Recovered") + " (recovered)"
                    doc.dirty = True
                    self._open_in_tab(doc)
                except Exception as e:
                    QMessageBox.warning(self, APP_NAME, f"Couldn't recover {os.path.basename(f)}:\n{e}")
            try:
                os.remove(f)
                folder = os.path.dirname(f)
                docstore.sweep_side_blobs(folder)
                if folder != self.autosave_root() and not os.listdir(folder):
                    os.rmdir(folder)
            except OSError:
                pass

    # ================================================================== section view
    def toggle_section(self, *_):
        if self.is_dwg and self.section_panel is None:
            return
        if self.section_panel is None:
            self.section_panel = SectionPanel(self)
            self._adapt_tree(self.section_panel)
            self.section_panel.closed.connect(self.toggle_section)
            chosen = None
            for pk in self.viewport.selection if self.is_asm else ():
                if pk.kind == "face":
                    shp = self.viewport.part_shapes.get(pk.key[0])
                    if shp is not None:
                        chosen = K.planar_face_plane(K.faces(shp)[pk.key[1]])
                        if chosen is not None:
                            break
            for pk in self.viewport.selection if not self.is_asm else ():
                item = pick_to_item(self.viewport, pk, {"plane"}, getattr(self.doc, "state", None))
                if item:
                    try:
                        chosen = F.resolve_plane(self.doc.state, item[1])
                    except K.KernelError:
                        chosen = None
                    break
            if chosen is not None:
                self.section_panel.use_selection(chosen)
            else:
                self.section_panel._plane_changed()
            self.section_panel.place()
        else:
            self.section_panel.hide()
            self.section_panel.deleteLater()
            self.section_panel = None
            self.viewport.set_section(None)
        on = self.section_panel is not None
        for w_ in (self.act_section, self.btn_section, self.btn_asection):
            w_.blockSignals(True)
            w_.setChecked(on)
            w_.blockSignals(False)

    # ================================================================== sheet metal
    def _flat_on(self, state, p):
        return getattr(self, "_flat_view", False) and p.id in getattr(state, "sheet", {})

    def flat_pattern_of(self, state, pid):
        """The flat pattern of a sheet metal part (cached while the part is unchanged)."""
        from .. import sheetmetal as SM
        part = state.parts[pid]
        cache = self.__dict__.setdefault("_flat_cache", {})
        hit = cache.get(pid)
        if hit is not None and hit[0] is part.shape:
            return hit[1]
        fp = SM.flat_pattern(part.shape, state.sheet[pid], state.named(pid))
        if len(cache) > 32:
            cache.clear()
        cache[pid] = (part.shape, fp)
        return fp

    def _flat_or_folded(self, state, p):
        if not self._flat_on(state, p):
            return p.shape
        try:
            return self.flat_pattern_of(state, p.id).solid()
        except Exception as e:
            self.status.showMessage(f"Couldn't unfold {p.name}: {e}", 6000)
            return p.shape

    def toggle_flat(self, *_):
        self._flat_view = not getattr(self, "_flat_view", False)
        self.btn_flat.setChecked(self._flat_view)
        if self.is_asm or self.is_dwg:
            return
        if self.mode == "idle":
            self._show_state(self.doc.state)
        if self._flat_view and not getattr(self.doc.state, "sheet", None):
            self.status.showMessage("No sheet metal parts here yet — make one with Sheet metal first", 5000)
        else:
            self.status.showMessage("Showing sheet metal parts flat" if self._flat_view else
                                    "Showing parts folded", 3000)

    def export_flat_dxf(self, pid=None):
        from .. import sheetmetal as SM
        if self.is_asm or self.is_dwg:
            return
        state = self.doc.state
        ids = [i for i in getattr(state, "sheet", {}) if i in state.parts]
        if not ids:
            QMessageBox.information(self, APP_NAME, "There are no sheet metal parts — make one with the Sheet metal "
                                    "tool first.")
            return
        if pid is None:
            sel = [pk.key[0] for pk in self.viewport.selection if pk.kind in ("face", "edge", "part")]
            sel = [i for i in sel if i in ids]
            pid = sel[0] if sel else (ids[0] if len(ids) == 1 else None)
        if pid is None:
            names = [state.parts[i].name for i in ids]
            name, ok = QInputDialog.getItem(self, "Export flat pattern", "Which part?", names, 0, False)
            if not ok:
                return
            pid = ids[names.index(name)]
        part = state.parts[pid]
        path, _ = QFileDialog.getSaveFileName(
            self, "Export flat pattern", self._dialog_dir("export", part.name.replace(" ", "_") + "_flat.dxf"),
            "DXF (*.dxf)")
        if not path:
            return
        remember_dir(self.settings, "export", path)
        if not path.lower().endswith(".dxf"):
            path += ".dxf"
        try:
            fp = self.flat_pattern_of(state, pid)
            _n, nb = SM.flat_dxf(fp, path, part.name)
            self.status.showMessage(f"Exported the flat pattern of {part.name} ({nb} bend line(s)): {path}", 8000)
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, f"Couldn't export the flat pattern:\n{e}")

    # ================================================================== undo / misc
    def undo(self):
        if self.mode == "sketch":
            self._sk("undo")
        elif self.mode == "idle":
            self.doc.undo()

    def redo(self):
        if self.mode == "sketch":
            self._sk("redo")
        elif self.mode == "idle":
            self.doc.redo()

    def _escape(self):
        if self.is_dwg:
            self.dwg.key(_FakeKey(Qt.Key_Escape))
            return
        if self.is_asm:
            self.asm.escape()
            return
        if self.mode == "sketch":
            if self.sketcher:
                self.sketcher.escape()
                self.viewport.update()
            return
        if self.mode == "dialog":
            self.cancel_dialog()
        elif self.mode == "idle":
            self.viewport.clear_selection()

    def _delete_key(self):
        if self.is_dwg:
            if self.dwg.selection:
                self.doc.delete(self.dwg.selection)
                self.dwg.selection = []
            return
        if self.mode == "sketch":
            self._sk("delete_selection")
            return
        if self.mode != "idle":
            return
        if self.is_asm:
            self.asm.delete_selected()
            return
        ids = self.left.features.selected_feature_ids()
        if ids:
            self._delete_features(ids)

    def _delete_features(self, ids):
        names = ", ".join(self.doc.feature(i)["name"] for i in ids)
        if QMessageBox.question(self, APP_NAME, f"Delete {names}?") == QMessageBox.Yes:
            self.doc.delete_features(ids)

    # ================================================================== tree interactions
    def _tree_reordered(self, ids, rollback):
        if self.mode != "idle":
            self._populate_lists()
            return
        order = {fid: i for i, fid in enumerate(ids)}
        new = sorted(self.doc.features, key=lambda f: order.get(f["id"], 1e9))
        if [f["id"] for f in new] != [f["id"] for f in self.doc.features] or rollback != self.doc.active_count():
            self.doc.checkpoint()
            self.doc.features = new
            self.doc.set_rollback(rollback if rollback < len(new) else None)
            self.doc.rebuild()
        else:
            self._populate_lists()

    # ------------------------------------------------------------------ feature-list highlighting (like Onshape)
    def _feature_hovered(self, fid):
        self._hl_hover = fid
        self._update_feature_hl()

    def _update_feature_hl(self):
        """Show what the hovered / selected features made: their faces on the model, or a sketch's curves."""
        vp = self.viewport
        if self.is_asm or self.mode != "idle":
            if vp.feature_hl is not None:
                vp.feature_hl = None
                vp.update()
            return
        fids = list(self.left.features.selected_feature_ids())
        if self._hl_hover and self._hl_hover not in fids:
            fids.append(self._hl_hover)
        faces, polys = {}, []
        for fid in fids:
            try:
                f_, p_ = self._feature_geometry(fid)
            except Exception:
                continue
            for pid, fis in f_.items():
                faces.setdefault(pid, set()).update(fis)
            polys += p_
        edges = {}
        many = sum(len(v) for v in faces.values()) > 3000      # huge imports: tint the faces, skip outlines
        for pid, fis in ({} if many else faces).items():
            shp = vp.part_shapes.get(pid)
            if shp is not None:
                edges[pid] = K.face_edge_indices(shp, fis)
        vp.feature_hl = {"faces": faces, "edges": edges, "polys": polys} if (faces or polys) else None
        vp.triads = self._connector_triads(getattr(self.doc, "state", None))
        vp.update()

    def _feature_geometry(self, fid):
        """(faces by part in the model shown now, world polylines) for one feature — cached per model state."""
        doc = self.doc
        key = (fid, id(doc.state), doc.active_count())
        hit = self._hl_cache.get(key)
        if hit is not None:
            return hit
        if len(self._hl_cache) > 200:
            self._hl_cache.clear()
        f = doc.feature(fid)
        idx = doc.feature_index(fid)
        faces, polys = {}, []
        if f is None or f.get("suppressed") or idx is None or idx >= doc.active_count():
            pass
        elif f["type"] == "sketch":
            sr = doc.state.sketches.get(fid)
            if sr is not None:
                for eid, e in sr.sketch.entities.items():
                    if e["type"] == "point":
                        continue
                    polys.append(np.array([sr.plane.to_world(u, v) for u, v in sr.sketch.polyline(eid, 48)]))
        elif f["type"] == "plane":
            pl = doc.state.planes.get(fid)
            if pl is not None:
                h = self.viewport.plane_half() * 0.6
                polys.append(np.array([pl.to_world(u, v) for u, v in ((-h, -h), (h, -h), (h, h), (-h, h), (-h, -h))]))
        elif f["type"] == "mate_connector":
            pass                              # its triad is drawn by _connector_triads
        elif f["type"] != "variable":
            before = doc.state_before(idx)
            after = doc.state_before(idx + 1)
            new = F.feature_new_faces(before, after)
            faces = F.map_faces_to(after, new, doc.state) if idx + 1 < doc.active_count() else new
            faces = {pid: fis for pid, fis in faces.items() if pid not in doc.hidden_parts}
        self._hl_cache[key] = (faces, polys)
        return faces, polys

    def _tree_plane_clicked(self, key):
        """A plane clicked in the feature list acts like clicking it in the graphics area."""
        kind, ident = key.split(":", 1)
        if kind == "std":
            pick = Pick("plane", ("std", ident))
        else:
            f = self.doc.feature(ident)
            if f is not None and f["type"] == "sketch" and self._tree_sketch_to_dialog(ident):
                return
            if f is None or f["type"] != "plane" or ident not in self.doc.state.planes:
                return
            pick = Pick("plane", ("plane", ident))
        if self.mode == "idle":
            self.viewport.set_selection([pick])
        elif self.viewport.pick_handler is not None:
            # an open dialog (e.g. a new sketch waiting for its plane) takes the plane
            self.viewport.pick_handler(pick)

    def _tree_sketch_to_dialog(self, fid):
        """A sketch clicked in the feature list while a feature dialog wants sketch areas (Extrude, Loft …): use the
        sketch's closed areas, like Onshape. A loft profile is one area, so for Loft the sketch's biggest area (its
        outline, with any holes in it) is used. Returns True if the click was used."""
        d = self.dialog
        fld = getattr(d, "active_field", None) if d is not None else None
        if fld is None or "region" not in fld.want:
            return False
        sr = self.doc.state.sketches.get(fid)
        if sr is None or not sr.regions:
            self.status.showMessage("That sketch isn't a closed shape, so it has no area to use. Close its outline "
                                    "(the inside turns blue) and pick it again.", 8000)
            return True
        idx = list(range(len(sr.regions)))
        if d.feature.get("type") == "loft" and len(idx) > 1:
            idx = [max(idx, key=lambda i: K.face_info(sr.regions[i]).get("area", 0.0))]
        for i in idx:
            item = ("region", F.region_ref(sr, fid, sr.regions[i]))
            if not any(ex[0] == "region" and ex[1].get("sketch") == fid and ex[1] == item[1] for ex in fld.items):
                fld.toggle(item)
        return True

    def _tree_eye(self, key):
        """The eye in the feature list: show / hide a sketch or plane."""
        _kind, fid = key.split(":", 1)
        if self.doc is not None and hasattr(self.doc, "toggle_feature_hidden"):
            self.doc.toggle_feature_hidden(fid)

    def _tree_context(self, key, gpos):
        kind, fid = key.split(":", 1)
        m = QMenu(self)
        if kind == "origin":
            if self.mode == "idle":
                self._add_origin_connector_action(m)
                m.exec(gpos)
            return
        if kind == "std_plane":
            m.addAction("New sketch", lambda: (self.viewport.set_selection([Pick("plane", ("std", fid))]),
                                               self.start_sketch()))
            m.addAction("Normal to", lambda: self.viewport.normal_to(K.std_plane(fid)))
            m.addAction("Show" if fid in self.doc.hidden_features else "Hide",
                        lambda: self.doc.toggle_feature_hidden(fid))
            m.exec(gpos)
            return
        f = self.doc.feature(fid)
        if f is None or self.mode != "idle":
            return
        m.addAction("Edit…", lambda: self.edit_feature(fid))
        m.addAction("Rename…", lambda: self._rename_feature(fid))
        if f["type"] == "context":
            m.addAction("Update context", lambda: self.update_context(fid))
        if f["type"] in ("sketch", "plane"):
            m.addAction("Show" if fid in self.doc.hidden_features else "Hide",
                        lambda: self.doc.toggle_feature_hidden(fid))
        if f["type"] == "plane":
            m.addAction("New sketch on plane", lambda: (self.viewport.set_selection([Pick("plane", ("plane", fid))]),
                                                        self.start_sketch()))
        m.addSeparator()
        m.addAction("Unsuppress" if f.get("suppressed") else "Suppress",
                    lambda: self.doc.set_suppressed(fid, not f.get("suppressed")))
        idx = self.doc.feature_index(fid)
        m.addAction("Roll to here", lambda: (self.doc.checkpoint(), self.doc.set_rollback(idx + 1)))
        if self.doc.rollback is not None:
            m.addAction("Roll to end", lambda: (self.doc.checkpoint(), self.doc.set_rollback(None)))
        m.addSeparator()
        ids = self.left.features.selected_feature_ids() or [fid]
        if fid not in ids:
            ids = [fid]
        m.addAction("Delete", lambda: self._delete_features(ids))
        err = self.doc.errors.get(fid)
        if err:
            m.addSeparator()
            a = m.addAction("⚠ " + err)
            a.setEnabled(False)
        m.exec(gpos)

    # ------------------------------------------------------------------ Context features (ADR 0004)
    def _context_status_of(self, fid):
        """context_status of Context feature `fid` of the current studio, or None outside a Document."""
        tab = self._folder_tab(self.doc)
        if tab is None:
            return None
        try:
            return self.folder.context_status(tab.id, fid)
        except K.KernelError:
            return None

    def update_context(self, fid=None):
        """Recapture a Context feature of the current studio from its Assembly (one undoable edit). Acts on `fid`,
        else the selected Context row, else the studio's only Context feature."""
        tab = self._folder_tab(self.doc)
        if self.is_asm or self.is_dwg or tab is None:
            return
        ctxs = [f["id"] for f in self.doc.features if f["type"] == "context"]
        if fid is None:
            sel = [i for i in self.left.features.selected_feature_ids() if i in ctxs]
            fid = sel[0] if sel else (ctxs[0] if len(ctxs) == 1 else None)
        if fid is None:
            self.status.showMessage("Select a Context feature to update", 5000)
            return
        try:
            self.folder.update_context(tab.id, fid)
        except K.KernelError as e:
            self.status.showMessage(str(e), 6000)
        self._populate_lists()              # the row's status text

    def _add_origin_connector_action(self, m):
        a = m.addAction("Mate connector at origin", self.mate_connector_at_origin)
        a.setObjectName("mate_connector_at_origin")
        return a

    def mate_connector_at_origin(self):
        """Open the Mate connector dialog with the origin preselected and an Owner part row."""
        self.start_feature("mate_connector")
        if self.mode == "dialog" and self.dialog is not None and self.dialog.ftype == "mate_connector":
            self.dialog.use_origin()

    def _rename_feature(self, fid):
        f = self.doc.feature(fid)
        name, ok = QInputDialog.getText(self, "Rename feature", "Name:", text=f["name"])
        if ok and name.strip():
            self.doc.rename_feature(fid, name.strip())

    # ================================================================== parts interactions
    def _part_clicked(self, pid, _):
        if self.mode == "idle":
            self.viewport.set_selection([Pick("part", (pid,))])
        elif self.mode == "dialog" and self.dialog:
            self.dialog.handle_pick(Pick("part", (pid,)))

    def _part_context(self, pid, gpos):
        p = self.doc.state.parts.get(pid)
        if p is None:
            return
        m = QMenu(self)
        m.addAction("Rename…", lambda: self._rename_part(pid))
        m.addAction("Change colour…", lambda: self._recolor_part(pid))
        m.addAction("Material…", lambda: self._set_part_material(pid))
        m.addAction("Show" if pid in self.doc.hidden_parts else "Hide", lambda: self.doc.toggle_part_hidden(pid))
        m.addAction("Isolate", lambda: self._isolate(pid))
        m.addSeparator()
        m.addAction("Mass properties", lambda: self._mass_props(pid))
        m.addAction(icon("drawing", 16), "Create drawing…", lambda: self.new_drawing(
            self.studio_link(self.doc, pid)))
        if pid in getattr(self.doc.state, "sheet", {}):
            m.addAction(icon("flat_dxf", 16), "Export flat pattern (DXF)…", lambda: self.export_flat_dxf(pid))
            m.addAction(icon("drawing", 16), "Create drawing of the flat pattern…", lambda: self.new_drawing(
                self.studio_link(self.doc, pid, flat=True)))
        m.addAction("Export STEP…", lambda: self.export([pid], "step"))
        m.addAction("Export STL…", lambda: self.export([pid], "stl"))
        m.exec(gpos)

    def _rename_part(self, pid):
        p = self.doc.state.parts[pid]
        name, ok = QInputDialog.getText(self, "Rename part", "Name:", text=p.name)
        if ok and name.strip():
            self.doc.set_part_override(pid, name=name.strip())

    def _recolor_part(self, pid):
        p = self.doc.state.parts[pid]
        c = QColorDialog.getColor(QColor.fromRgbF(*p.color), self, "Part colour")
        if c.isValid():
            self.doc.set_part_override(pid, color=[c.redF(), c.greenF(), c.blueF()])

    def _isolate(self, pid):
        self.doc.hidden_parts = {p for p in self.doc.state.parts if p != pid}
        self.doc.notify()

    def _show_all_parts(self):
        self.doc.hidden_parts = set()
        self.doc.notify()

    def _toggle_planes(self):
        self._show_state(self.doc.state)

    def _mass_props(self, pid):
        p = self.doc.state.parts[pid]
        self._mass_props_shape(p.name, p.shape, self.doc.part_overrides.get(pid, {}).get("material"))

    def _set_part_material(self, pid):
        """Ask for a part's material and store it as a part override (saved with the file)."""
        names = ["(none: weighed as PLA)"] + list(M.MATERIALS)
        cur = M.material_name(self.doc.part_overrides.get(pid, {}).get("material"))
        name, ok = QInputDialog.getItem(self, "Material", "Material of this part:", names,
                                        names.index(cur) if cur else 0, False)
        if ok:
            self.doc.set_part_override(pid, material=None if name == names[0] else name)

    def _mass_props_shape(self, name, shape, material=None):
        """Mass properties of one shape. The mass is for `material` (PLA when none); "Other material…" gives a
        one-off reading with another one and changes nothing in the document."""
        mp = K.mass_props(shape)
        lo, hi = K.bbox(shape)
        c = mp["centroid"]
        while True:
            mat = M.material_name(material) or M.DEFAULT_MATERIAL
            box = QMessageBox(self)
            box.setWindowTitle("Mass properties")
            box.setText(
                f"<b>{name}</b><br><br>Volume: {fmt(mp['volume'], 3)} mm³<br>"
                f"Surface area: {fmt(mp['area'], 3)} mm²<br>"
                f"Mass ({mat}, {M.MATERIALS[mat]:g} g/cm³): {fmt(M.mass_g(mp['volume'], mat), 2)} g<br>"
                f"Centre of mass: ({fmt(c[0], 3)}, {fmt(c[1], 3)}, {fmt(c[2], 3)})<br>"
                f"Bounding box: {fmt(hi[0] - lo[0], 3)} × {fmt(hi[1] - lo[1], 3)} × {fmt(hi[2] - lo[2], 3)} mm")
            other = box.addButton("Other material…", QMessageBox.ActionRole)
            box.addButton(QMessageBox.Ok)
            box.exec()
            if box.clickedButton() is not other:
                return
            names = list(M.MATERIALS)
            pick, ok = QInputDialog.getItem(self, "Mass properties", "Weigh as:", names, names.index(mat), False)
            if ok:
                material = pick

    def _mass_props_assembly(self, doc):
        """Mass properties of the visible instances of an Assembly: mass by material, total, centre of mass."""
        s = doc.mass_summary()
        if not s["by_material"]:
            QMessageBox.information(self, "Mass properties", "Nothing visible to weigh.")
            return
        rows = "".join(f"{k}: {fmt(v, 2)} g<br>" for k, v in sorted(s["by_material"].items()))
        c = s["com"]
        QMessageBox.information(
            self, "Mass properties",
            f"<b>{doc.name}</b> (visible instances)<br><br>{rows}<b>Total: {fmt(s['mass'], 2)} g</b><br>"
            f"Centre of mass: ({fmt(c[0], 3)}, {fmt(c[1], 3)}, {fmt(c[2], 3)})")

    # ================================================================== viewport interactions
    def _hover_changed(self, pk):
        if self.mode == "idle":
            self.status.showMessage(pk.label() if pk else "", 0)

    def _viewport_double(self, pk):
        if self.mode != "idle" or pk is None or self.is_asm:
            return
        if pk.kind in ("region", "sketch_line"):
            self.edit_feature(pk.key[0])
        elif pk.kind == "plane" and pk.key[0] == "plane":
            self.edit_feature(pk.key[1])

    def _pick_owner_name(self, pk):
        """The name the left panel shows for what a pick belongs to: its part, its sketch, or the plane itself."""
        if self.is_asm and self.doc is not None and pk.kind in ("face", "edge", "vertex", "part"):
            inst = self.doc.instance(pk.key[0])         # the name the Instances list shows
            return inst["name"] if inst else None
        if self.doc is None or not hasattr(self.doc, "state"):
            return None
        if pk.kind in ("face", "edge", "vertex", "part"):
            p = self.doc.state.parts.get(pk.key[0])
            return p.name if p is not None else None
        if pk.kind == "plane":
            if pk.key[0] == "std":
                return pk.key[1]
            f = self.doc.feature(pk.key[1])
            return f["name"] if f else None
        f = self.doc.feature(pk.key[0])            # region, sketch_line, sketch_point: the sketch
        return f["name"] if f else None

    def _viewport_context(self, pk, gpos):
        if self.is_asm:
            if self.mode == "idle":
                self.asm.viewport_menu(pk, gpos)
            return
        m = QMenu(self)
        if self.mode == "sketch":
            if self.viewport.sketcher:
                lp_ = self.viewport.mapFromGlobal(gpos)
                self.viewport.sketcher.add_context_actions(m, self.viewport, lp_.x(), lp_.y())
            m.addAction("Normal to sketch", self._normal_to_sketch)
            m.addAction("Finish sketch", self.finish_sketch)
            m.addAction("Cancel sketch", self.cancel_sketch)
            m.exec(gpos)
            return
        if self.mode != "idle":
            return
        lp = self.viewport.mapFromGlobal(gpos)
        osp = self.viewport.cam.to_screen(np.zeros(3))
        if abs(osp[0] - lp.x()) <= 10 and abs(osp[1] - lp.y()) <= 10:      # right-click on the Origin
            self._add_origin_connector_action(m)
            m.addSeparator()
        if len(self.viewport.pick_all(lp.x(), lp.y())) >= 2:
            m.addAction("Select other\u2026", lambda: self.viewport.open_select_other(lp.x(), lp.y()))
            m.addSeparator()
        if pk is not None:
            if pk.kind in ("plane", "face"):
                item = pick_to_item(self.viewport, pk, {"plane"}, getattr(self.doc, "state", None))
                if item:
                    m.addAction("New sketch", lambda: (self.viewport.set_selection([pk]), self.start_sketch()))

                    def nt(item=item):
                        try:
                            self.viewport.normal_to(F.resolve_plane(self.doc.state, item[1]))
                        except K.KernelError:
                            pass
                    m.addAction("Normal to", nt)
            if pk.kind in ("face", "edge"):
                pid = pk.key[0]
                m.addAction("Hide part", lambda: self.doc.toggle_part_hidden(pid))
                m.addAction("Isolate part", lambda: self._isolate(pid))
                m.addAction("Select part", lambda: self.viewport.set_selection([Pick("part", (pid,))]))
                m.addAction("Mass properties", lambda: self._mass_props(pid))
            if pk.kind in ("region", "sketch_line"):
                fid = pk.key[0]
                m.addAction("Edit sketch", lambda: self.edit_feature(fid))
                m.addAction("Hide sketch", lambda: self.doc.toggle_feature_hidden(fid))
            if pk.kind == "plane":
                key = pk.key
                hid = key[1]
                m.addAction("Hide plane", lambda: self.doc.toggle_feature_hidden(hid))
            m.addSeparator()
        m.addAction("Zoom to fit", lambda: self.viewport.fit())
        vm = m.addMenu("Views")
        for name in ("Front", "Back", "Left", "Right", "Top", "Bottom", "Isometric"):
            vm.addAction(name, lambda n=name: self.viewport.set_standard_view(n))
        if self.doc.hidden_parts:
            m.addAction("Show all parts", self._show_all_parts)
        m.exec(gpos)

    def _selection_changed(self):
        if self.mode != "idle":
            return
        sel = self.viewport.selection
        if self.is_asm:
            self.asm.panel.select_instances({pk.key[0] for pk in sel if pk.kind in ("face", "edge", "part")})
        text = self._measure_text(sel)
        if not sel:
            self.viewport.measure_line = None
            self.viewport.update()
        if text:
            self.measure.setText(text)
            self.measure.adjustSize()
            self.measure.move(self.viewport.width() - self.measure.width() - 14,
                              self.viewport.height() - self.measure.height() - 14)
            self.measure.show()
            self.measure.raise_()
        else:
            self.measure.hide()

    def _sub(self, pk):
        shape = self.viewport.part_shapes.get(pk.key[0])
        if shape is None:
            return None
        if pk.kind == "face":
            return K.faces(shape)[pk.key[1]]
        if pk.kind == "edge":
            return K.edges(shape)[pk.key[1]]
        if pk.kind == "part":
            return shape
        return None

    def _measure_text(self, sel):
        from .. import measure as MEAS
        items, names = [], []
        for pk in sel:
            if pk.kind in ("face", "edge", "part"):
                items.append((pk.kind, self._sub(pk)))
                p = self.doc.state.parts.get(pk.key[0])
                if self.is_asm and pk.kind == "part":
                    inst = self.doc.instance(pk.key[0])
                    names.append(inst["name"] if inst else None)
                else:
                    names.append(p.name if p and pk.kind == "part" else None)
            elif pk.kind == "region":
                for fid, sr, _ in self.viewport.sketches:
                    if fid == pk.key[0] and pk.key[1] < len(sr.regions):
                        items.append(("region", sr.regions[pk.key[1]]))
                        names.append(None)
        if not items:
            self.viewport.measure_line = None
            return f"{len(sel)} items selected" if sel else ""
        try:
            m = MEAS.measure(items, names)
        except Exception:
            self.viewport.measure_line = None
            return ""
        self.viewport.measure_line = m.get("line") if m else None
        self.viewport.update()
        return MEAS.to_html(m)

    # ================================================================== files
    @staticmethod
    def _has_content(doc):
        return bool(doc.features) or bool(getattr(doc, "instances", None)) or bool(getattr(doc, "views", None)) \
            or bool(getattr(doc, "annots", None))

    def _confirm_discard(self, doc=None):
        doc = doc or self.doc
        if not doc.dirty or not self._has_content(doc):
            return True
        r = QMessageBox.question(self, APP_NAME, f"Save changes to “{doc.name}” first?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def _leave_modes(self):
        if self.is_dwg:
            self.dwg.leave()
            self.mode = "idle"
            return
        if self.is_asm:
            self.asm.leave()
            self.mode = "idle"
            return
        if self.mode == "dialog":
            self.cancel_dialog()
        elif self.mode == "sketch":
            self.cancel_sketch()

    # ------------------------------------------------------------------ tabs
    def _new_document(self, name=None):
        self._studio_counter += 1
        doc = PartStudioDoc()
        doc.name = name or f"Part Studio {self._studio_counter}"
        doc.rebuild()
        return doc

    def _tab_index(self, doc):
        for i in range(self.tabs.count()):
            if self.docs.get(self.tabs.tabData(i)) is doc:
                return i
        return -1

    def _refresh_tab_title(self, doc):
        i = self._tab_index(doc)
        if i >= 0:
            self.tabs.setTabText(i, doc.name + (" •" if doc.dirty else ""))
            self.tabs.setTabToolTip(i, (doc.path or doc.name) + "\nDouble-click to rename · right-click or middle-click to close")

    def _add_tab(self, doc, switch=True):
        if switch and hasattr(self, "main_stack"):
            self.show_workspace()
        self.var_tabs.prepare(doc)
        key = id(doc)
        self.docs[key] = doc
        if self.studio_engine is not None:
            self.studio_engine.attach(doc)         # Part Studio, Variable Studio, Assembly or Drawing tab
        doc.listeners.append(lambda d=doc: self._doc_changed_from(d))
        if doc.kind == "assembly" and self._folder_tab(doc) is None:
            doc.resolver = self._resolve_source
        if doc.kind == "drawing":
            if self._folder_tab(doc) is None:
                doc.resolver = self._resolve_drawing_source
            doc.hlr_async = self.dwg.request_exact
        i = self.tabs.addTab(icon({"assembly": "assembly", "drawing": "drawing"}.get(doc.kind, "part"), 16), doc.name)
        self.tabs.setTabData(i, key)
        if not switch:
            self._refresh_tab_title(doc)
            return
        if self.tabs.currentIndex() == i:
            self._switch_tab(i)
        else:
            self.tabs.setCurrentIndex(i)
        self._refresh_tab_title(doc)

    def _doc_changed_from(self, doc):
        if doc is self.doc:
            self._on_doc_changed()
        else:
            self._refresh_tab_title(doc)

    def _is_pristine(self, doc):
        return doc is not None and not self._has_content(doc) and not doc.path and not doc.dirty

    def _switch_tab(self, i):
        if i < 0:
            return
        doc = self.docs.get(self.tabs.tabData(i))
        if doc is None or doc is self.doc:
            return
        self._leave_modes()
        if self.section_panel is not None:
            self.toggle_section()
        if self.doc is not None and self.doc.kind != "variables":
            self._update_thumbnail()
            if self.doc.kind != "drawing":
                self._views[id(self.doc)] = self.viewport.cam.state()
        self.doc = doc
        self.viewport.clear_selection()
        self.measure.hide()
        if self.var_tabs.is_variables(doc):
            self.var_tabs.show(doc)
            return
        asm = doc.kind == "assembly"
        if doc.kind == "drawing":
            self.tool_stack.setCurrentIndex(3)
            self.left_stack.setCurrentIndex(2)
            self.view_stack.setCurrentIndex(1)
            self.viewport.drag_handler = None
            self.dwg.activate()
            self._on_doc_changed()
            return
        self.view_stack.setCurrentIndex(0)
        self.tool_stack.setCurrentIndex(2 if asm else 0)
        self.left_stack.setCurrentIndex(1 if asm else 0)
        self.viewport.drag_handler = self.asm if asm else None
        self.viewport.triads = []
        self.viewport.ghosts = []
        self._on_doc_changed()                 # (an Assembly or Drawing is refreshed when its inputs change: LiveTabs)
        saved = self._views.get(id(doc))
        if saved is not None:
            self.viewport.cam.set_state(saved)
            self.viewport.update()
        else:
            QTimer.singleShot(0, lambda: self.viewport.set_standard_view("Isometric", animate=False))

    def close_tab(self, i):
        if i < 0:
            return
        doc = self.docs.get(self.tabs.tabData(i))
        if doc is None:
            return
        if doc is not self.doc:
            self.tabs.setCurrentIndex(i)
        self._leave_modes()
        if not self._confirm_discard(doc):
            return
        if self.tabs.count() == 1:
            self._add_tab(self._new_document())        # always keep one tab open
            i = self._tab_index(doc)
        key = self.tabs.tabData(i)
        self.tabs.removeTab(i)
        self._drop_autosave(self.docs.get(key))
        self.docs.pop(key, None)
        self._views.pop(key, None)
        if self.studio_engine is not None:
            self.studio_engine.prune()

    def _rename_tab(self, i):
        if i < 0:
            return
        doc = self.docs.get(self.tabs.tabData(i))
        name, ok = QInputDialog.getText(self, "Rename tab", "Name:", text=doc.name)
        if ok and name.strip():
            doc.name = name.strip()
            doc.dirty = True
            doc.notify()

    def _tab_context(self, pos):
        i = self.tabs.tabAt(pos)
        if i < 0:
            return
        m = QMenu(self)
        m.addAction("Rename…", lambda: self._rename_tab(i))
        m.addAction("Duplicate", lambda: self._duplicate_tab(i))
        m.addSeparator()
        m.addAction("Close", lambda: self.close_tab(i))
        m.exec(self.tabs.mapToGlobal(pos))

    def _duplicate_tab(self, i):
        src = self.docs.get(self.tabs.tabData(i))
        if src is None:
            return
        import json
        if src.kind == "drawing":
            g = DrawingDoc()
            d = json.loads(src.to_json(embed=False))
            g.source, g.sheet, g.title = d["source"], d["sheet"], d["title"]
            g.views, g.annots = d["views"], d["annots"]
            g._counter = src._counter
            g._fallback = src._fallback
            g.name = src.name + " (copy)"
            g.resolver = self._resolve_drawing_source
            g.refresh(notify=False)
            g.dirty = True
            self._register_in_document(g, like=src)
            self._add_tab(g)
            return
        if src.kind == "assembly":
            d = json.loads(src.to_json(embed=False))
            a = AssemblyDoc()
            a.instances, a.mates, a.blobs = d["instances"], d["mates"], dict(src.blobs)
            a._counter = src._counter
            a.name = src.name + " (copy)"
            a.resolver = self._resolve_source
            a.refresh(solve=False, notify=False)
            a.dirty = True
            self._register_in_document(a, like=src)
            self._add_tab(a)
            return
        doc = PartStudioDoc()
        d = json.loads(src.to_json())
        doc.features, doc.rollback = d["features"], d["rollback"]
        doc.part_overrides, doc.hidden_parts = d["overrides"], set(d["hidden"])
        doc.hidden_features = set(d.get("hidden_features", []))
        doc._counter = src._counter
        doc.name = src.name + " (copy)"
        doc.rebuild()
        doc.dirty = True
        self._register_in_document(doc, like=src)
        self._add_tab(doc)

    def new_doc(self):
        self._leave_modes()
        doc = self._new_document()
        self._register_in_document(doc)
        self._add_tab(doc)

    def new_assembly(self):
        self._leave_modes()
        self._asm_counter = getattr(self, "_asm_counter", 0) + 1
        a = AssemblyDoc()
        a.name = f"Assembly {self._asm_counter}"
        a.resolver = self._resolve_source
        self._register_in_document(a)
        self._add_tab(a)
        QTimer.singleShot(0, lambda: self.viewport.set_standard_view("Isometric", animate=False))
        if self.studio_docs() and any(d.state.parts for d in self.studio_docs()):
            QTimer.singleShot(50, self.asm.toggle_insert)

    def drawing_sources(self):
        """Everything a drawing can show: each open Part Studio (all parts, or one part) and each Assembly."""
        out = []
        for i in range(self.tabs.count()):
            d = self.docs.get(self.tabs.tabData(i))
            if d is None:
                continue
            if d.kind == "studio" and any(not p.reference for p in d.state.parts.values()):
                base = self.studio_link(d)
                entries = []
                if sum(not p.reference for p in d.state.parts.values()) > 1:
                    entries.append(("All parts", {**base, "part": "*"}))
                for p in (p for p in d.state.parts.values() if not p.reference):
                    entries.append((p.name, {**base, "part": p.id, "part_name": p.name}))
                    if p.id in getattr(d.state, "sheet", {}):
                        entries.append((p.name + " — flat pattern", {**base, "part": p.id, "part_name": p.name,
                                                                      "flat": True}))
                out.append((d.name, "part", entries))
            elif d.kind == "assembly" and d.instances:
                out.append((d.name, "assembly", [("Whole assembly", self.assembly_link(d))]))
        return out

    def new_drawing(self, preselect=None):
        """Create a Drawing tab of a part, a Part Studio or an Assembly."""
        self._leave_modes()
        from .drawing_ui import NewDrawingDialog
        sources = self.drawing_sources()
        if not sources:
            QMessageBox.information(self, APP_NAME, "Make (or open) a part or an assembly first — a drawing shows "
                                    "one of your open Part Studios or Assemblies.")
            return
        if preselect is None and self.doc is not None and self.doc.kind in ("studio", "assembly"):
            if self.doc.kind == "studio":
                own = [i for i, p in self.doc.state.parts.items() if not p.reference]
                only = own[0] if len(own) == 1 else "*"
                preselect = self.studio_link(self.doc, only)
            else:
                preselect = self.assembly_link(self.doc)
        dlg = NewDrawingDialog(self, sources, preselect, self.settings)
        if dlg.exec() != QDialog.Accepted:
            return
        src = dlg.chosen()
        if src is None:
            return
        g = DrawingDoc()
        self._dwg_counter = getattr(self, "_dwg_counter", 0) + 1
        g.name = (src.get("part_name") or src.get("name") or src.get("studio_name") or f"Drawing {self._dwg_counter}")
        if src.get("flat"):
            g.name += " flat pattern"
        g.source = dict(src)
        g.sheet.update(size=dlg.size.currentData(), portrait=bool(dlg.orient.currentData()),
                       projection=dlg.proj.currentData())
        g.title.update(drawn=dlg.drawn.text().strip(), company=dlg.company.text().strip(), title=g.name)
        for k, v in (("dwg_size", g.sheet["size"]), ("dwg_proj", g.sheet["projection"]),
                     ("dwg_drawn", g.title["drawn"]), ("dwg_company", g.title["company"])):
            self.settings.setValue(k, v)
        g.resolver = self._resolve_drawing_source
        g.hlr_async = self.dwg.request_exact
        if src.get("tab") is not None and self.folder is not None:       # a Document tab: resolve by tab id
            g.resolver = self.folder.resolver.resolve_drawing_source
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            g.refresh(notify=False)
            if dlg.views.isChecked():
                g.auto_layout()
        finally:
            QApplication.restoreOverrideCursor()
        g.dirty = True
        if src.get("tab") is not None:
            self._register_in_document(g, like=self._source_tab_doc(src))
        self._add_tab(g)

    def _source_tab_doc(self, src):
        tab = self.folder.tab(src.get("tab")) if self.folder is not None and src.get("tab") else None
        return tab.doc if tab is not None else None

    def _resolve_drawing_source(self, src):
        return self.resolver.resolve_drawing_source(src)

    # ------------------------------------------------------------------ links between tabs
    def studio_docs(self):
        out = []
        for i in range(self.tabs.count()):
            d = self.docs.get(self.tabs.tabData(i))
            if d is not None and d.kind == "studio":
                out.append(d)
        return out

    def _find_open_studio(self, uid, path):
        studios = self.studio_docs()
        doc = next((d for d in studios if uid and d.uid == uid), None)
        if doc is None and path:
            ap = os.path.abspath(path)
            doc = next((d for d in studios if d.path and os.path.abspath(d.path) == ap), None)
        return doc

    def _find_open_assembly(self, uid, path):
        asms = [d for d in self.docs.values() if d.kind == "assembly"]
        doc = next((d for d in asms if uid and d.uid == uid), None)
        if doc is None and path:
            ap = os.path.abspath(path)
            doc = next((d for d in asms if d.path and os.path.abspath(d.path) == ap), None)
        return doc

    def studio_by_uid(self, uid, path=None, load=False, show=False):
        """The Part Studio an assembly instance links to: an open tab (by id, then by file), else the file."""
        if show:
            return self._show_studio(uid, path, load)
        return self.resolver.studio_by_uid(uid, path, load=load)

    def _show_studio(self, uid, path, load):
        """studio_by_uid(show=True): switch to the tab if it's already open, else open it in one."""
        doc = self.resolver.studio_by_uid(uid, path)
        if doc is not None:
            self.tabs.setCurrentIndex(self._tab_index(doc))
            return doc
        if load and path and os.path.exists(path):
            self.open_file(path)
            return self.resolver.studio_by_uid(uid, path)
        return None

    def _resolve_source(self, src):
        return self.resolver.resolve_source(src)

    def open_studio_for_link(self, path):
        """Open a Part Studio file in a background tab (keeping the current tab), for inserting from it."""
        for d in self.studio_docs():
            if d.path and os.path.abspath(d.path) == os.path.abspath(path):
                return d
        try:
            doc = PartStudioDoc.load(path)
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, f"Could not open {path}:\n{e}")
            return None
        doc.name = os.path.splitext(os.path.basename(path))[0]
        self._add_tab(doc, switch=False)
        self._add_recent(path)
        return doc

    def open_dialog(self):
        self._leave_modes()
        from .home import open_dialog_filter
        path, _ = QFileDialog.getOpenFileName(self, "Open", self._dialog_dir("doc"), open_dialog_filter())
        if path:
            remember_dir(self.settings, "doc", path)
            self.open_any(path)

    def _dialog_dir(self, kind, name=""):
        """Start folder (joined with a default file name) for a file dialog."""
        d = start_dir(self.settings, kind, getattr(self.doc, "path", None))
        return os.path.join(d, name) if name else d

    def open_file(self, path):
        # already open? just switch to it
        for key, d in self.docs.items():
            if d.path and os.path.abspath(d.path) == os.path.abspath(path):
                self.tabs.setCurrentIndex(self._tab_index(d))
                return
        from .progress import ImportProgress
        prog = ImportProgress(self, f"Opening {os.path.basename(path)}")
        try:
            kind = TK.kind_for_path(path)
            if kind in (None, "studio") and not self._prefetch_imports(path, prog):
                self.status.showMessage("Opening cancelled", 5000)
                return
            prog.stage("Rebuilding the model…")
            self.viewport.progress_cb = prog.step
            try:
                if kind == "assembly":
                    doc = AssemblyDoc.load(path, resolver=self._resolve_source)
                elif kind == "drawing":
                    prog.stage("Working out the views…")
                    doc = DrawingDoc.load(path, resolver=self._resolve_drawing_source,
                                          hlr_async=self.dwg.request_exact)
                else:
                    doc = TK.get(kind or "studio").load(path)
            except Exception as e:
                prog.close()
                prog = None
                QMessageBox.critical(self, APP_NAME, f"Could not open {path}:\n{e}")
                return
            doc.name = os.path.splitext(os.path.basename(path))[0]
            self._open_in_tab(doc)
            QTimer.singleShot(400, lambda d=doc: self._update_thumbnail(d))
        finally:
            self.viewport.progress_cb = None
            if prog is not None:
                prog.close()
        self._add_recent(path)

    def _open_in_tab(self, doc):
        """New tab — or reuse the current one if it's still empty."""
        old = self.doc
        if self._is_pristine(old):
            old_key = id(old)
            self._add_tab(doc)
            self.tabs.removeTab(self._tab_index(old))
            self.docs.pop(old_key, None)
            self._views.pop(old_key, None)
        else:
            self._add_tab(doc)
        QTimer.singleShot(60, lambda: self.viewport.set_standard_view("Isometric", animate=False))

    def save(self):
        if self.versions_ui.refuse_save():
            return False
        if self._folder_tab(self.doc) is not None:
            return self._save_document()
        if not self.doc.path:
            return self.save_as()
        if self.is_asm or self.is_dwg:
            links.repair_links(self.resolver, self.doc)
        if self.is_asm:
            unsaved = sorted({s.name for s in self.studio_docs() if not s.path and any(
                i["source"].get("uid") == s.uid for i in self.doc.instances)})
            if unsaved:
                self.status.showMessage("Note: " + ", ".join(unsaved) + " isn't saved yet — the assembly keeps a "
                                        "copy of its parts, but save the Part Studio too so it stays linked", 10000)
        try:
            self.doc.save()
        except OSError as e:
            QMessageBox.critical(self, APP_NAME, f"Could not save:\n{e}")
            return False
        self._on_doc_changed()
        self._update_thumbnail()
        self._add_recent(self.doc.path)
        self._drop_autosave(self.doc)
        self.status.showMessage(f"Saved {self.doc.path}", 4000)
        return True

    def save_as(self):
        if self.versions_ui.refuse_save():
            return False
        if self._folder_tab(self.doc) is not None:       # a Document's tabs save with the Document
            return self._save_document()
        rec = TK.find(getattr(self.doc, "kind", "studio")) or TK.get("studio")
        ext = rec.ext
        filt = f"FreeShape {rec.plural.lower() if rec.kind != 'studio' else 'documents'} (*{ext})"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save as", self.doc.path or self._dialog_dir("doc", f"{self.doc.name}{ext}"), filt)
        if not path:
            return False
        remember_dir(self.settings, "doc", path)
        if not path.lower().endswith(ext):
            path += ext
        self.doc.path = path
        return self.save()

    def import_step(self, into_current=False):
        if self.mode != "idle":
            self._leave_modes()
        path, _ = QFileDialog.getOpenFileName(self, "Import STEP", self._dialog_dir("doc"),
                                              "STEP files (*.step *.stp);;All files (*)")
        if path:
            remember_dir(self.settings, "doc", path)
            self.import_step_file(path, into_current)

    def import_step_file(self, path, into_current=False):
        from .progress import ImportProgress, read_step_in_background
        if into_current and self.is_asm:
            n = self.asm.insert_step(path)
            self.status.showMessage(f"Inserted {n} part(s) from {os.path.basename(path)}", 5000)
            return
        fname = os.path.basename(path)
        with open(path, "rb") as fh:
            raw = fh.read()
        prog = ImportProgress(self, f"Importing {fname}")
        try:
            if not read_step_in_background(raw, prog, self):
                self.status.showMessage("Import cancelled", 5000)
                return
            data = base64.b64encode(raw).decode()
            if into_current:
                doc = self.doc
            else:
                doc = PartStudioDoc()
                doc.name = os.path.splitext(fname)[0]
            f = doc.make_feature("import")
            f["name"] = "Import " + fname
            f["params"] = {"data": data, "filename": fname}
            prog.stage("Building the parts…")
            self.viewport.progress_cb = prog.step
            if not into_current:
                doc._thumb_for = os.path.abspath(path)
            doc.add_feature(f)
            if not into_current:
                self._open_in_tab(doc)
        finally:
            self.viewport.progress_cb = None
            prog.close()
        if doc.errors.get(f["id"]):
            QMessageBox.warning(self, APP_NAME, doc.errors[f["id"]])
        QTimer.singleShot(80, lambda: self.viewport.set_standard_view("Isometric", animate=False))
        n = len(doc.state.parts)
        self.status.showMessage(f"Imported {fname} — {n} part{'s' if n != 1 else ''}", 5000)
        if not doc.errors.get(f["id"]):
            self._add_recent(path)
            QTimer.singleShot(500, lambda d=doc: self._update_thumbnail(d))

    def _prefetch_imports(self, path, prog):
        """Read the STEP files a .fshape imports in the background first (big ones only). False = cancelled. A file
        written since I4.9 names each by blob key (`params.blob`, in `blobs/` beside the file); an older one carries
        it inline (`params.data`)."""
        import json
        from .progress import read_step_in_background
        try:
            with open(path) as fh:
                feats = json.load(fh).get("features", [])
        except Exception:
            return True
        for f in feats:
            p = f.get("params", {}) if f.get("type") == "import" and not f.get("suppressed") else {}
            if p.get("blob") or p.get("data"):
                try:
                    raw = blobstore.store_for(path).get(p["blob"]) if p.get("blob") else base64.b64decode(p["data"])
                except Exception:
                    continue
                if not read_step_in_background(raw, prog, self):
                    return False
        return True

    def _stl_options(self, path):
        """Mesh options from the dialog for STL, 3MF and OBJ (no ASCII choice for the last two); None for any
        other file type, False if cancelled."""
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".stl", ".3mf", ".obj"):
            return None
        from .export_dialog import StlOptionsDialog
        return StlOptionsDialog.ask(self, self.settings, ext[1:].upper())

    def export(self, part_ids, fmt_=None):
        if self.is_dwg:
            self.dwg.export(fmt_ if fmt_ in ("dxf", "pdf", "svg", "png") else None)
            return
        if self.is_asm:
            if not self.doc.instances:
                QMessageBox.information(self, APP_NAME, "Insert some parts first.")
                return
            sel = "STL (*.stl)" if fmt_ == "stl" else "STEP (*.step *.stp)"
            default = self.doc.name.replace(" ", "_") + (".stl" if fmt_ == "stl" else ".step")
            path, chosen = QFileDialog.getSaveFileName(self, "Export assembly", self._dialog_dir("export", default),
                                                       "STEP (*.step *.stp);;STL (*.stl);;3MF (*.3mf);;OBJ (*.obj)", sel)
            if not path:
                return
            remember_dir(self.settings, "export", path)
            if not os.path.splitext(path)[1]:
                path += ".stl" if "STL" in chosen else ".3mf" if "3MF" in chosen else ".obj" if "OBJ" in chosen else ".step"
            opts = self._stl_options(path)
            if opts is False:
                return
            try:
                self.doc.export(path, options=opts)
                self.status.showMessage(f"Exported {path}", 5000)
            except Exception as e:
                QMessageBox.critical(self, APP_NAME, f"Export failed:\n{e}")
            return
        if not self.doc.state.parts:
            QMessageBox.information(self, APP_NAME, "There are no parts to export yet.")
            return
        filters = "STEP (*.step *.stp);;STL (*.stl);;3MF (*.3mf);;OBJ (*.obj)"
        default = "part.stl" if fmt_ == "stl" else "part.step"
        if part_ids and len(part_ids) == 1:
            default = self.doc.state.parts[part_ids[0]].name.replace(" ", "_") + (".stl" if fmt_ == "stl" else ".step")
        sel = "STL (*.stl)" if fmt_ == "stl" else "STEP (*.step *.stp)"
        path, chosen = QFileDialog.getSaveFileName(self, "Export", self._dialog_dir("export", default), filters, sel)
        if not path:
            return
        remember_dir(self.settings, "export", path)
        if not os.path.splitext(path)[1]:
            path += ".stl" if "STL" in chosen else ".3mf" if "3MF" in chosen else ".obj" if "OBJ" in chosen else ".step"
        opts = self._stl_options(path)
        if opts is False:
            return
        try:
            self.doc.export(path, part_ids, options=opts)
            self.status.showMessage(f"Exported {path}", 5000)
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, f"Export failed:\n{e}")

    def show_about(self):
        from .. import __version__
        QMessageBox.about(self, "About FreeShape", (
            f"<h3>FreeShape {__version__}</h3>"
            "<p>Free, offline, Onshape-style parametric CAD — parts, assemblies, drawings and sheet metal.</p>"
            "<p>Copyright © 2026 Richard Willmott</p>"
            "<p>This program is free software: you can redistribute it and/or modify it under the terms of the "
            "GNU General Public License, version 3 or any later version. It comes with ABSOLUTELY NO WARRANTY. "
            "See the LICENSE file for details.</p>"
            "<p>Built on open-source libraries including the Open CASCADE Technology geometry kernel (LGPL 2.1) "
            "and Qt / PySide6 (LGPL 3) — see THIRD-PARTY-NOTICES.txt.</p>"
            "<p><a href='https://github.com/richiewillmott/FreeShape'>github.com/richiewillmott/FreeShape</a></p>"))

    def show_shortcuts(self):
        QMessageBox.information(self, "Keyboard shortcuts", (
            "<b>Navigation</b><br>Right-drag: rotate · Middle-drag or Ctrl+right-drag: pan · Wheel: zoom<br>"
            "Arrow keys: turn the view 15° per tap · F: zoom to fit · "
            "Shift+1…7: Front, Back, Left, Right, Top, Bottom, Isometric · N: normal to sketch<br><br>"
            "<b>Selecting</b><br>Space: deselect all · Drag a box: left-to-right selects what is fully inside, "
            "right-to-left also what it touches · Shift / Ctrl: add<br><br>"
            "<b>Features</b><br>Alt+C or Ctrl+Space: search tools · "
            "Shift+S: sketch · Shift+E: extrude · Shift+W: revolve · Shift+F: fillet<br>"
            "Ctrl+Z / Ctrl+Y: undo / redo · Enter: accept dialog · Esc: cancel<br><br>"
            "<b>Sketch</b><br>L line · R rectangle · C circle · A arc · P point · D dimension · Q construction<br>"
            "I coincident · H horizontal · V vertical · E equal · T tangent · U use edges · Del delete · Esc stop tool<br><br>"
            "<b>Assembly</b><br>I insert · Shift+F fastened · Shift+R revolute · Shift+L slider · "
            "left-drag a part to move it · Del delete"))

    def closeEvent(self, e):
        self._leave_modes()
        self._update_thumbnail()
        for i in range(self.tabs.count()):
            doc = self.docs.get(self.tabs.tabData(i))
            if doc is not None and doc.dirty and self._has_content(doc):
                self.tabs.setCurrentIndex(i)
                if not self._confirm_discard(doc):
                    e.ignore()
                    return
        for doc in self.docs.values():
            self._drop_autosave(doc)
        try:
            os.rmdir(self.autosave_dir())        # only succeeds if it's empty
        except OSError:
            pass
        if self.studio_engine is not None:
            self.studio_engine.shutdown()
        e.accept()

    def keyPressEvent(self, e):
        # assembly keys (plain shortcuts would steal the same letters from the sketcher)
        if self.is_asm and self.mode == "idle":
            k, mods = e.key(), e.modifiers()
            if k == Qt.Key_I and not mods:
                self.asm.toggle_insert()
                return
            gz = self.asm.gizmo
            txt = e.text()
            if gz.active() and gz.last is not None and gz.editor is None and txt and txt in "0123456789-.+":
                gz.open_editor(initial=txt)          # typing a number after a drag sets it, no click needed
                return
            if k == Qt.Key_Escape and self.asm.gizmo.inst is not None:
                self.asm.gizmo.clear()
                return
            if mods == Qt.ShiftModifier and k in (Qt.Key_R, Qt.Key_L):
                self.asm.start_mate("revolute" if k == Qt.Key_R else "slider")
                return
        super().keyPressEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.section_panel is not None:
            self.section_panel.place()
        if self.measure.isVisible():
            self._selection_changed()


class _FakeKey:
    """Lets window shortcuts hand a key to the drawing tools."""

    def __init__(self, key):
        self._k = key

    def key(self):
        return self._k

    def modifiers(self):
        return Qt.NoModifier
