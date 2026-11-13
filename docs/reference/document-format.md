# Document format reference

This page lists what FreeShape writes to disk today. It was written from the code, not from memory: the `to_json` methods, `FolderDocument._write_manifest`, the migration steps registered with `freeshape/migrations.py`, `freeshape/links.py` and `freeshape/blobstore.py`. `tests/test_format_reference.py` builds one object of each kind with every optional key present, saves it, and fails if a top-level key is missing from this page.

Terms follow [CONTEXT.md](../../CONTEXT.md): Document, Tab, Part Studio, Assembly, Drawing, Variable Studio, Version, Link.

## Layout on disk

A Document is a folder under git ([ADR 0001](../adr/0001-document-is-a-folder-under-git.md)). It holds:

| Path | Content |
| --- | --- |
| `document.json` | The manifest. One row per Tab. |
| `<tab file>` | One JSON file per Tab. The extension gives the kind: `.fshape` Part Studio, `.fasm` Assembly, `.fdwg` Drawing, `.fvars` Variable Studio. |
| `blobs/` | Content-addressed binary files: every tab's binary data (see Where binary data lives). Created beside a tab file by its first save that needs one. |

Every file is JSON written with `indent=1`. Each save writes `<path>.tmp` and moves it into place with `os.replace`. A file also exists outside a Document folder (a loose file); the same four tab formats apply.

A tab kind has a name in `document.json` (`studio`, `assembly`, `drawing`, `variables`) and a format tag in its own file (see each section).

## Document manifest

File: `document.json`. Format tag `freeshape-document`. Current version: 1. The manifest has no migration chain: a file with a higher version is refused.

Keys that are not listed here are kept and written back unchanged (`FolderDocument.extra`).

| Key | Type | Meaning |
| --- | --- | --- |
| `format` | string | Always `freeshape-document`. |
| `version` | integer | Manifest version. A value above the current one is refused. |
| `id` | string | The Document id. Cross-Document Links name it. |
| `name` | string | Display name of the Document. |
| `tabs` | list | One entry per Tab, in display order. See Tab entry. |

### Tab entry

| Key | Type | Meaning |
| --- | --- | --- |
| `id` | string | Tab id, made once and never changed. Non-empty and unique in the manifest. Links name it. |
| `kind` | string | `studio`, `assembly`, `drawing` or `variables`. |
| `file` | string | Path of the Tab file inside the Document folder. It must resolve inside the folder (no absolute path, no `..`, no symlink). |
| `name` | string | Display name of the Tab. |

Other keys on an entry are kept and written back unchanged (`Tab.extra`).

### Key stamped into every Tab file

On save, `FolderDocument.save` adds `_tab_id` to each Tab file. It repeats the manifest id, so a rename of the file on disk does not lose it. Open does not repair a drifted `file`; it only warns when `_tab_id` differs from the manifest id. No tab class reads `_tab_id`.

## Part Studio file

Written by `Document.to_json` (`freeshape/document.py`). Format tag `freeshape`. Current version: 4. Extension `.fshape`.

| Key | Type | Meaning |
| --- | --- | --- |
| `format` | string | Always `freeshape`. |
| `version` | integer | File version. Current is 4. |
| `name` | string | Display name. |
| `uid` | string | Id of the studio, used by loose-file Links. |
| `features` | list | The feature list, in order. See Feature. |
| `rollback` | integer or null | Index of the rollback bar. Null means no rollback. |
| `overrides` | object | Part id to part override. See Part override. |
| `hidden` | list of string | Ids of hidden parts, sorted. |
| `hidden_features` | list of string | Ids of hidden features, sorted. |
| `part_aliases` | object | Old part id (`<fid>:<counter>`) to new part id (`<fid>/<n>`). Written by the version 1 to 2 step; kept so older Assemblies and Drawings still resolve. |
| `python_builds` | object | Only written when a Python feature, a Derived feature or a plugin feature has a stored build. Feature id to Python build entry, or, for a plugin feature, plugin copy entry. |
| `folders` | object | Feature-list folders: folder id to `{name, open}`. A feature in a folder carries the folder id in its `folder` key. Written only when there is a folder. Since version 4. |
| `configs` | object | Configurations: `{rows: [{name, vars, suppress}], active}`. `vars` maps a variable name to an expression, `suppress` lists feature ids, `active` is the index of the row in use. Written only when there is a row. Since version 4. |
| `plugin_copies` | object | Read only: upstream FreeShape 2.0's saved results of plugin features (type `plugin:<id>`), feature id to `{stamp, plugin, numeric, breps}`, with `breps` a list of base64 zlib-compressed BREP texts. On load each becomes a plugin copy entry in `python_builds`, its BREPs held as blobs for the next save. Not written. Since version 4. |

`part_aliases` is since version 2, and `folders`, `configs` and `plugin_copies` since version 4. The other keys exist in version 1. On load, a missing `overrides`, `hidden`, `hidden_features` or `part_aliases` reads as empty, and a missing `rollback` as null.

### Feature

Each entry of `features` is an object.

| Key | Type | Meaning |
| --- | --- | --- |
| `id` | string | Feature id. Part ids are `<id>/<n>`. |
| `type` | string | Feature type. See Feature types. |
| `name` | string | Display name. |
| `suppressed` | boolean | A suppressed feature is skipped. |
| `params` | object | The type's parameters. Their keys are set per type in `default_params` (`freeshape/features.py`). |
| `plugin` | object | Python features only: `{"id": <plugin id>, "version": <plugin version>}` recorded when the feature was made or last acknowledged. |
| `folder` | string | Optional. Id of the feature-list folder the feature is in. See `folders`. Since version 4. |

#### Feature types

Built-in types: `sketch`, `extrude`, `revolve`, `fillet`, `chamfer`, `shell`, `plane`, `mirror`, `linear_pattern`, `circular_pattern`, `boolean`, `import`, `hole`, `variable`, `sweep`, `loft`, `draft`, `split`, `move`, `thread`, `sm_base`, `sm_flange`, `sm_hem`, `sm_jog`, `sm_tab`, `sm_convert`, `delete_face`, `move_face`, `offset_surface`, `delete_part`, `variable_studio_ref`, `derived`, `mate_connector`, `context`. A Python feature has a type owned by a Plugin (`py:<plugin>/<name>`). A feature added by an upstream-style plugin file has type `plugin:<id>`.

Types whose `params` name something outside the feature:

| Type | `params` key | Type | Meaning |
| --- | --- | --- | --- |
| `sketch` | `sketch` | object | `{points, entities, constraints}` of the sketch. Ids such as `p4` and `l7` live here. |
| `import` | `blob` | string | Blob key of the imported STEP file. Since version 3. |
| `import` | `data` | string | Before version 3: base64 of the imported STEP file, inline. Since version 3 written only as an empty string, by an import that holds no file yet. |
| `import` | `filename` | string | Name of the imported file. |
| `variable_studio_ref` | `source` | object | Link source of a Variable Studio Tab. See Link sources. |
| `variable_studio_ref` | `names` | list of string | Optional. Only these variables are taken. Absent means all. |
| `derived` | `source` | object | Link source of a Part Studio Tab or a Version of one. See Link sources. |
| `derived` | `parts` | list of string | Part ids taken from the source. |
| `derived` | `sketches` | list of string | Sketch ids taken from the source. |
| `derived` | `connectors` | list | Mate connectors taken from the source. |
| `mate_connector` | `name`, `ref`, `kind`, `click`, `snap`, `x_ref`, `flip`, `owner` | mixed | A mate connector the studio publishes. `ref` is a face or edge reference, `owner` the part it belongs to. |
| `context` | `assembly` | string | The Assembly Tab, by id or uid. |
| `context` | `instance` | string | The instance id the capture was taken for. |
| `context` | `capture` | string | Blob key of the capture listing. See Where binary data lives. |
| `delete_part` | `parts` | list of string | Parts removed from the result. |
| `extrude`, `revolve` | `end`, `up_to`, `offset` | mixed | Since version 4 an up-to end is `end` `up_to_face`, `up_to_vertex`, `up_to_part` or `up_to_next`, with its target in `up_to` (`{kind: face, plane or vertex, ref}` or `{kind: part, id}`) and an optional `offset` in mm. Before version 4 this fork wrote `end` `upto` with a reference in `upto`; the 3 to 4 step converts it. `end` `to_face` with its target in `to` is unchanged. |
| `split` | `tools` | list | Since version 4: `{kind: plane, face or path, ref, both, flip}` per cutting tool. A file with the older `plane` and `keep` keys still reads. |
| `mirror`, `linear_pattern`, `circular_pattern` | `type`, `features` | mixed | `type` `features` repeats the listed feature ids instead of whole parts. |

The engine adds a `resolved` entry to `params` while it rebuilds. It is not part of the stored format.

### Part override

Value of `overrides[<part id>]`. Every key is optional.

| Key | Type | Meaning |
| --- | --- | --- |
| `name` | string | Display name of the part. |
| `color` | list of number | RGB colour, each 0 to 1. |
| `material` | string | Material name. The Assembly copies it to the instance `material`. |

### Python build entry

Value of `python_builds[<feature id>]`, written by `host.build_record`.

| Key | Type | Meaning |
| --- | --- | --- |
| `plugin` | object | `{"id", "version"}` of the Plugin that made the build. |
| `parts` | list | One entry per part, in creation order. |

Each entry of `parts`:

| Key | Type | Meaning |
| --- | --- | --- |
| `id` | string | Part id `<feature id>/<n>`. |
| `brep` | string | Blob key (sha256 hex) of the part's BREP bytes. |
| `names` | string | Blob key of the part's Element name map, as JSON. |

### Plugin copy entry

Value of `python_builds[<feature id>]` for a plugin feature (type `plugin:<id>`), written by `plugins.saved_copy` in the process that built it. The feature builds from it when its plugin is not installed and `stamp` still matches.

| Key | Type | Meaning |
| --- | --- | --- |
| `stamp` | string | Hash of the feature's type and parameters, evaluated values included, when the copy was made. |
| `plugin` | string | Name of the plugin file that made it. |
| `numeric` | list of string | Parameter keys that take expressions, so they still evaluate without the plugin. |
| `breps` | list of string | Blob keys of the BREP bytes of each body the plugin returned, before the boolean into existing parts. |

## Assembly file

Written by `AssemblyDoc.to_json` (`freeshape/assembly.py`). Format tag `freeshape-assembly`. Current version: 9. Extension `.fasm`.

| Key | Type | Meaning |
| --- | --- | --- |
| `format` | string | Always `freeshape-assembly`. |
| `version` | integer | File version. Current is 9. |
| `name` | string | Display name. |
| `uid` | string | Id of the Assembly, used by loose-file Links. |
| `instances` | list | The placed parts. See Instance. |
| `mates` | list | The Mates. See Mate. |
| `groups` | list | Rigid sets of instances. See Group. Since version 3. |
| `relations` | list | Relations between Mate values. See Relation. Since version 6. |
| `connectors` | list | Explicit mate connectors owned by the Assembly. See Assembly connector. Written only when the list is not empty. No migration step: a file without it reads as empty. |
| `explode` | object | The exploded view: `{steps: [{ids, dir, dist}], amount}`. A step moves the instances in `ids` by `dist` mm along `dir`; `amount` is how far the view is opened, 0 to 1. Written only when there is a step. Since version 9. |
| `patterns` | list | Instance patterns: `{id, type, name, seeds, count, spacing, angle, axis, copies}`. `type` is `linear` or `circular`, `seeds` the patterned instance ids, `axis` `{kind: world, name, flip}` or `{kind: mate, mate}`, and `copies` one list of instance ids per seed. Written only when there is a pattern. Since version 9. |

### Instance

| Key | Type | Meaning |
| --- | --- | --- |
| `id` | string | Instance id, `I<n>`. |
| `name` | string | Display name, unique in the Assembly (`<part> <1>`). |
| `source` | object | Where the part comes from. See Instance source. |
| `T` | 4 by 4 list of number | Placement matrix. |
| `fixed` | boolean | The instance does not move in the solve. The first instance is fixed. |
| `hidden` | boolean | Hidden in the viewport. |
| `part_name` | string | Name of the part when it was inserted. Used in the bill of materials. |
| `material` | string or null | Material name taken from the part's override when inserted. |
| `color` | list of number | Optional. Instance colour overriding the part's. |
| `suppressed` | boolean | Optional. A suppressed instance is left out of the solve, bill of materials and export. |
| `pattern` | string | Optional. Id of the instance pattern this instance is a copy of. A copy is fixed and follows its seed. Since version 9. |
| `_brep_blob` | string | Blob key of the part's BREP (zlib-compressed `K.shape_to_brep` bytes), written so the file opens when its Part Studio is missing. Not written for `step` or `standard` sources. Used when the source cannot be resolved. Since version 8; before it, `_brep` held the same bytes in base64. |

`material` is not gated by a migration step. A file without it reads as no material.

### Instance source

The `kind` key picks the form.

| `kind` | Key | Type | Meaning |
| --- | --- | --- | --- |
| any | `kind` | string | `studio`, `step` or `standard`. |
| `studio` | `tab` | string | Tab id of the Part Studio, inside a Document. |
| `studio` | `part` | string | Part id in that studio. |
| `studio` | `studio_name` | string | Display name of the studio, refreshed on save. |
| `studio` | `document` | string | Cross-Document Link: id of the other Document. |
| `studio` | `version` | string | Version of that Document, or of this one, that the Link reads. |
| `studio` | `uid` | string | Loose-file Link: uid of the studio. Removed when the Link becomes a tab id. |
| `studio` | `path` | string | Loose-file Link: path of the studio file. |
| `studio` | `relpath` | string | Path relative to the Assembly file, written on save. |
| `studio` | `config` | string | Optional. Name of the configuration of the Part Studio this instance shows. Absent means the active one. |
| `step` | `blob` | string | Blob key of the imported STEP file. Before version 8, a key into the file's own `blobs` object (the first 16 hex characters of the sha1 of the base64 text). |
| `step` | `index` | integer | Which solid of the STEP file. |
| `step` | `filename` | string | Name of the imported file. |
| `standard` | `type` | string | Standard part type, `sp:<plugin>/<name>`. Since version 7. |
| `standard` | `plugin` | string | Plugin id. |
| `standard` | `plugin_version` | string | Plugin version the instance was made or last acknowledged with. |
| `standard` | `params` | object | The part's parameters, as cleaned by its `validate`. |

The `studio` form is the one built by `links.studio_source`: `kind`, `tab`, `part`, `studio_name`, plus `document` and `version` for another Document.

### Mate

| Key | Type | Meaning |
| --- | --- | --- |
| `id` | string | Mate id, `M<n>`. |
| `type` | string | `fastened`, `revolute`, `slider`, `cylindrical`, `planar`, `pin_slot`, `parallel` or `ball`. |
| `name` | string | Display name. |
| `a` | object | First mate end. See Mate end. Null until set. |
| `b` | object | Second mate end. |
| `flip` | boolean | Reverses the Z axis of the second end. |
| `offset` | number | Offset along Z, in mm. |
| `angle` | number | Rotation about Z, in degrees. |
| `suppressed` | boolean | A suppressed Mate is left out of the solve. |
| `values` | object | Mate values by freedom (`tx`, `ty`, `tz`, `rz`): translations in mm, rotations in degrees ([ADR 0005](../adr/0005-mate-values-are-stored-and-relations-hold-a-phase.md)). Since version 4. |
| `limits` | object | Optional. Freedom to `[min, max]`, either end null. Since version 5. |

### Mate end

A mate end is a connector dict. A face, edge or vertex end carries:

| Key | Type | Meaning |
| --- | --- | --- |
| `inst` | string | Instance id. |
| `kind` | string | `face`, `edge`, `named`, `origin` or `connector`. |
| `index` | integer or null | Face or edge index in the part. Null for named ends. |
| `click` | list of number or null | Pick point in part coordinates. |
| `ref` | object or null | Reference to the picked element: its Element name when the part has one, plus a geometric signature. |
| `snap` | object | Optional. The snap point used: `type`, `p`, `edge_ref`. |
| `frame` | object | Connector frame `{o, z, x, what}`, in part coordinates. |
| `x_ref` | list of number | Direction of X as two components along the frame's base axes. |

A named end (a Standard part's connector, or a mate connector a Part Studio publishes) has these extra or changed keys:

| Key | Type | Meaning |
| --- | --- | --- |
| `named` | string | Id of the published connector or Assembly connector. |
| `name` | string | Display name of the connector. For a Standard part end this is the connector name, with `kind` `named` and no `index`, `click`, `ref` or `snap`. |

### Group

| Key | Type | Meaning |
| --- | --- | --- |
| `id` | string | Group id, `G<n>`. |
| `name` | string | Display name. |
| `instances` | list of string | Member instance ids. A group has at least two. |

### Relation

| Key | Type | Meaning |
| --- | --- | --- |
| `id` | string | Relation id, `R<n>`. |
| `type` | string | `linear`, `gear`, `rack_pinion` or `screw`. |
| `name` | string | Display name. |
| `a` | object | `{"mate": <mate id>, "dof": <freedom>}` of the driving side. |
| `b` | object | The same, for the driven side. |
| `ratio` | number | Non-zero. Unit depends on the type. |
| `reverse` | boolean | Reverses the sign of the ratio. |
| `phase` | number or null | Offset captured when the Relation was added, so nothing moves ([ADR 0005](../adr/0005-mate-values-are-stored-and-relations-hold-a-phase.md)). Null while either freedom has no value. |
| `suppressed` | boolean | A suppressed Relation is inert. |

### Assembly connector

An entry of `connectors`.

| Key | Type | Meaning |
| --- | --- | --- |
| `id` | string | Connector id, `C<n>`. |
| `inst` | string | Instance id. |
| `name` | string | Display name. |
| `kind` | string | `origin`: a connector at the instance's part origin. |

## Drawing file

Written by `DrawingDoc.to_json` (`freeshape/drawing.py`). Format tag `freeshape-drawing`. Current version: 3. Extension `.fdwg`.

| Key | Type | Meaning |
| --- | --- | --- |
| `format` | string | Always `freeshape-drawing`. |
| `version` | integer | File version. Current is 3. |
| `name` | string | Display name. |
| `uid` | string | Id of the Drawing. |
| `source` | object or null | What the Drawing shows. See Drawing source. |
| `sheet` | object | Sheet settings. See Sheet. |
| `title` | object | Title block fields. See Title block. |
| `views` | list | The views. See View. |
| `annots` | list | Dimensions, notes, balloons and the parts list. See Annotation. |
| `bom` | list | Parts list rows, `{item, name, qty, mass, material}` (`material` only when the row has one), filled from the source Assembly's bill of materials when the table is placed or refreshed. Written only when the list is not empty. No migration step: a file without it reads as empty. |
| `_brep_blob` | string | Blob key of the shown shape's BREP (zlib-compressed `K.shape_to_brep` bytes), a fallback when the source is missing. Since version 3; before it, `_brep` held the same bytes in base64. |
| `_views_blob` | string | Blob key of the exact hidden-line results, so a reopen does not recompute them: JSON `{"model": <sha1 of the BREP bytes>, "views": [{"d", "up", "curves"}]}`. Used only when the model hash still matches. Since version 3; before it, `_views` held the same object inline. |

`_brep_blob` and `_views_blob` are written only when the shape is available.

### Drawing source

Built by the new-Drawing dialog and the link helpers.

| Key | Type | Meaning |
| --- | --- | --- |
| `kind` | string | `studio` or `assembly`. |
| `tab` | string | Tab id of the source, inside a Document. |
| `part` | string or null | Part id for a studio source. Null or `*` means the whole studio. |
| `studio_name` | string | Display name of a studio source. |
| `name` | string | Display name of an Assembly source. |
| `part_name` | string | Name of the part, refreshed on save. |
| `flat` | boolean | Show the flat pattern of a sheet-metal part. |
| `bends` | list | Bend lines of a flat pattern, re-read from the source on every refresh. |
| `document`, `version` | string | Cross-Document or pinned Link. See Link sources. |
| `exploded` | boolean | Optional, for an Assembly source: show the Assembly's exploded view. |
| `uid`, `path`, `relpath` | string | Loose-file Link. See Link sources. |

### Sheet

| Key | Type | Meaning |
| --- | --- | --- |
| `size` | string | `A4`, `A3`, `A2`, `A1` or `A0`. |
| `portrait` | boolean | Portrait orientation. |
| `scale` | number | Sheet scale. |
| `projection` | string | `first` (ISO) or `third` (ANSI). |

### Title block

| Key | Type | Meaning |
| --- | --- | --- |
| `title` | string | Drawing title. |
| `number` | string | Drawing number. |
| `rev` | string | Revision. |
| `drawn` | string | Drawn by. |
| `date` | string | Date, `dd/mm/yyyy`. |
| `company` | string | Company. |
| `material` | string | Material. |
| `tolerance` | string | General tolerance note. |
| `sheet` | string | Sheet number, such as `1/1`. |

### View

| Key | Type | Meaning |
| --- | --- | --- |
| `id` | string | View id, `V<n>`. |
| `type` | string | `base`, `projected`, `iso`, `section` or `detail`. |
| `d` | list of number | Direction from the part towards the viewer. |
| `up` | list of number | Up vector. |
| `o` | list of number | Paper position of the model origin, in mm. |
| `scale` | number or null | View scale. Null follows the sheet scale. |
| `hidden` | boolean | Show hidden edges dashed. |
| `tangent` | boolean | Show tangent edges. |
| `centre_marks` | boolean | Show centre marks on holes. |
| `parent` | string or null | Id of the view this one was projected from. |
| `align` | string or null | Alignment to the parent view. |
| `label` | string | View label. |
| `letter` | string | Letter of a section or detail view, as in A-A or Detail B. |
| `cut` | object | Section view only: `{p0}`, a model point on the cutting plane. The plane's normal is the view direction `d`. |
| `detail` | object | Detail view only: `{c, r}`, the centre (in the parent view's plane) and radius of the circle it shows. |

### Annotation

Each entry of `annots` is a dimension or a note.

| Key | Type | Meaning |
| --- | --- | --- |
| `id` | string | `D<n>` for a dimension, `N<n>` for a note, `A<n>` for a balloon or the parts list. |
| `type` | string | `lin`, `pl`, `dia`, `rad`, `ang`, `note`, `balloon` or `bom` (the parts list table). |
| `view` | string | View id the dimension belongs to. |
| `a`, `b`, `b2`, `c`, `d` | object | Dimension ends. A point end holds `kind` (`vertex`, `centre` or `uv`), `p` (3D model point) or `uv`, and for a model end `i`, `n`, `r`, `name`, `nkind` and `lost`. `name` is the Element name of the vertex, edge or face the end sits on. |
| `orient` | string | `h`, `v` or `a` for a linear dimension. |
| `pos` | list of number | Paper position of the text or dimension line. |
| `text` | string | Typed text replacing the measured value. |
| `dp` | integer | Decimal places. Absent means up to 3. |
| `prefix`, `suffix` | string | Text before and after the value. |
| `tol` | object | `{type: none, sym, dev or lim, up, lo, dp}`. |
| `height` | number | Text height of a note. |
| `item` | integer | Balloon only: the parts list item number it shows. |
| `anchor` | list of number | Balloon only: the model point its leader points at. |

## Variable Studio file

Written by `VariableStudioDoc.to_json` (`freeshape/variable_studio.py`). Format tag `freeshape-variables`. Current version: 1. Extension `.fvars`. The chain has no steps yet.

| Key | Type | Meaning |
| --- | --- | --- |
| `format` | string | Always `freeshape-variables`. |
| `version` | integer | File version. Current is 1. |
| `name` | string | Display name. |
| `rows` | list | Rows in evaluation order. See Row. |

### Row

| Key | Type | Meaning |
| --- | --- | --- |
| `name` | string | Variable name. Later rows and other tabs refer to it as `#name`. |
| `expression` | string | Expression. A unit is written inside it (`20 mm`). |

## Migration chains

`freeshape/migrations.py` holds one ordered list of steps per format. Step i upgrades a file from version i to version i + 1. The latest version is one more than the number of steps. `migrate` applies every step a file still needs and stamps the result. A file whose version is above the latest is refused before anything else is read. Steps register from the module that owns the format. An additive key needs no step.

### Chain document

Part Studio. Registered in `freeshape/document.py`. Latest version 4. 3 steps.

| Step | Function | Change |
| --- | --- | --- |
| 1 to 2 | `_migrate_part_ids` | Part ids change from `<fid>:<counter>` to `<fid>/<n>`. The step replays the features, rewrites the ids that `params`, `overrides` and `hidden` name, and records `part_aliases`. |
| 2 to 3 | `_move_import_data_to_blobs` | An `import` feature's base64 `params.data` becomes `params.blob`, the key of the decoded bytes. The bytes are held in memory by the loaded studio and written to the blobs by its next save. |
| 3 to 4 | `_adopt_upstream_2_0` | The upstream 2.0 merge. A file may now hold `folders`, `configs`, `plugin_copies` and upstream's feature types, which an earlier build would drop or fail on. One conversion: an extrude saved with end `upto` takes upstream's shape, an `up_to_*` end with its target in `up_to`. |

### Chain assembly

Assembly. Registered in `freeshape/assembly.py`. Latest version 9. 8 steps.

| Step | Function | Change |
| --- | --- | --- |
| 1 to 2 | `_upgrade_part_id_syntax` | No data change. Marks a file written by code that understands the new part id syntax. A source's old id is translated through the studio's `part_aliases` until the next save. |
| 2 to 3 | `_add_groups` | Adds an empty `groups` list. |
| 3 to 4 | `_upgrade_mate_values` | No data change. `AssemblyDoc.load` fills a Mate's `values` from the saved pose once the instances resolve. |
| 4 to 5 | `_add_mate_limits` | No data change. A Mate may carry `limits`. |
| 5 to 6 | `_add_relations` | Adds an empty `relations` list. |
| 6 to 7 | `_upgrade_standard_part_source` | No data change. An instance source may have `kind` `standard`. |
| 7 to 8 | `_move_geometry_to_blobs` | An instance's base64 `_brep` becomes `_brep_blob`. The `blobs` object leaves the file and a `step` source's `blob` becomes the key of the decoded STEP bytes. The bytes are held in memory by the loaded Assembly and written to the blobs by its next save. |
| 8 to 9 | `_add_explode_and_patterns` | No data change. An Assembly may carry `explode` and `patterns`, and an instance `pattern`. Both top-level keys are written only when used and read with defaults. |

### Chain drawing

Drawing. Registered in `freeshape/drawing.py`. Latest version 3. 2 steps.

| Step | Function | Change |
| --- | --- | --- |
| 1 to 2 | `_upgrade_part_id_syntax` | No data change. Same purpose as the Assembly step 1 to 2. |
| 2 to 3 | `_move_geometry_to_blobs` | The base64 `_brep` becomes `_brep_blob`, and the inline `_views` object becomes `_views_blob`, the key of its JSON. Held and written as for the Assembly step 7 to 8. |

### Chain variables

Variable Studio. Latest version 1. 0 steps.

### Not on a chain

The manifest has its own constant (`FORMAT_VERSION` in `freeshape/doc_folder.py`, 1) and no steps. Links to a Part Studio or Assembly Tab by uid and path are converted to Tab ids when the Document opens (`links.migrate_links_to_tab_ids`). That conversion runs on open and marks the changed Tabs dirty. It is not a chain step.

## Link sources

A Link is a `source` dict. An Assembly instance holds one in `source`. A Drawing holds one in `source`. A Part Studio holds one in `params.source` of a `derived` or `variable_studio_ref` feature. `links.tab_sources` lists them for a Tab. `LinkResolver` (`freeshape/links.py`) resolves them. Resolving never changes the dict. `repair_links` writes display names and the tab-id form back, and runs only before a save.

### Loose file

Keys: `kind`, `uid`, `path`, `relpath` (plus `part` or `name`, and `studio_name`).

Used outside a Document folder. `LinkResolver` looks for an open tab with that `uid`, then with that `path`, then loads the file at `path`, cached by path and mtime. `relpath` is the path relative to the file that holds the source. On load it is used when `path` no longer exists.

### In-Document

Keys: `kind`, `tab`, plus `part` and `studio_name` for a Part Studio, `name` for an Assembly, `name` for a Variable Studio. No `uid`, no `path`.

`LinkResolver` looks `tab` up in the Document's manifest. A Tab id that the manifest does not have, a Tab of the wrong kind, and a Tab that failed to open each raise an error that names the Tab. A Tab never saved still resolves, and renaming its file does not break the Link.

A legacy loose-file source found inside a Document is converted to this form when the Document opens. A legacy `path` that points outside the Document folder is never read: the instance is reported unresolved.

A source that has `version` and no `document` is pinned to a Version of its own Document. It is read from that Version, read-only.

### Cross-Document

Keys: the in-Document keys plus `document` (the other Document's id) and `version` (a Version name).

`LinkResolver` finds the other Document through the registry, which must hold a folder that the app created or imported. It opens that Version read-only and looks `tab` up in it. A Link with `document` and no `version` is an error. The Link does not update by itself: `update_link` re-pins it to the newest Version, and `link_status` reports whether one exists. No path is stored.

## Where binary data lives

| Data | Where | Form |
| --- | --- | --- |
| Part shape for an Assembly instance | `blobs/<sha256>` beside the `.fasm` file, named by `instances[]._brep_blob` | zlib-compressed BREP, one blob per part shape. |
| Imported STEP file in an Assembly | `blobs/<sha256>` beside the `.fasm` file, named by a `step` source's `blob` | The STEP file's bytes. |
| Imported STEP file in a Part Studio | `blobs/<sha256>` beside the `.fshape` file, named by `params.blob` of an `import` feature | The STEP file's bytes. |
| Shown shape of a Drawing | `blobs/<sha256>` beside the `.fdwg` file, named by `_brep_blob` | zlib-compressed BREP. |
| Exact Drawing views | `blobs/<sha256>` beside the `.fdwg` file, named by `_views_blob` | JSON curves plus the sha1 of the BREP. |
| Last build of a Python feature | `blobs/<sha256>` beside the `.fshape` file, named by `python_builds` | BREP bytes and a names JSON, one blob each. |
| Saved copy of a plugin feature | `blobs/<sha256>` beside the `.fshape` file, named by `python_builds[<fid>].breps` | BREP bytes, one blob per body. |
| Context capture ([ADR 0004](../adr/0004-context-stores-captured-shapes.md)) | `blobs/<sha256>` beside the `.fshape` file, named by `params.capture` | A JSON listing `{assembly, instance, solved, neighbours}` plus a BREP blob and a names blob per neighbour. Each neighbour has `id`, `name`, `hash`, `T`, `brep`, `names`. |

A tab file holds blob keys only. A Document tab's blobs go through the store (`DocumentStore.blobs(tab_id)`: the `blobs/` folder beside the tab file in a folder, a dict per tab in a `MemoryStore`). A loose file keeps them in the `blobs/` folder beside itself. An old file that carried data inline loads without any store: its migration step keeps the bytes in memory (`blobstore.HELD`) and the next save writes them. A blob that the tab cannot find leaves that instance, feature or Drawing red with an error naming the key. A blob no tab names is left where it is: nothing deletes blobs.

Blob rules (`freeshape/blobstore.py`): a blob file is named by the sha256 of its bytes, in 64 lowercase hex characters. The `blobs/` folder sits beside the tab file. The store refuses a symlinked folder or file, a blob over 64 MiB, and content whose hash is not its name. Bytes reach a parser only after that check. Writes use a `.tmp` file and `os.replace`. Equal content is stored once.

[ADR 0009](../adr/0009-tab-data-goes-through-a-document-store.md) records that all binary data lives in the store's blobs, addressed by hash. Before I4.9 it was stored three ways: base64 inline in Assembly and Drawing files, an inline `blobs` object for imported STEP files, and these blob folders.
