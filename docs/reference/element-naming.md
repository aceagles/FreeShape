# Element naming — the FreeCAD rules FreeShape ports

Reference source: FreeCAD tag **1.0.2**, commit **256fc7ef** (full id
`256fc7eff3379911ab5daf88e10182c509aa8052`), read-only at `/opt/freecad-src` in the harness image.

This is a description, in our own words, of how FreeCAD 1.0's element map names faces, edges and
vertices, written so that a later card can implement the same rules without reading the FreeCAD
tree. [ADR 0003](../adr/0003-element-naming-port-of-freecad-element-map.md) decided to port the
*approach*, not the code: FreeCAD is LGPL-2.1-or-later and none of it is copied here. Every rule in
sections 1 to 8 cites the place in the reference tree it was read from, as
`/opt/freecad-src/<path>:<line>`.

Two things FreeShape deliberately does differently from FreeCAD are called out inline, marked
**FreeShape**:

* FreeCAD can replace any name fragment with a `StringHasher` id (`#<hex>`) so long names stay
  cheap to store and to save (`/opt/freecad-src/src/App/StringHasher.h:56`,
  `/opt/freecad-src/src/App/ElementMap.cpp:676`). ADR 0003 rules out a string hasher. FreeShape
  stores names in full and **recomputes every name on every rebuild**, so it never has to persist,
  reload or garbage-collect a hash table.
* FreeCAD treats a reference whose name no longer resolves as a warning and carries on. FreeShape
  treats a name that is present on the reference but missing from the rebuilt map as an error that
  names the lost element (ADR 0003).

Throughout, "tag" means the id of the document object that produced a shape — FreeCAD's
`DocumentObject` id, FreeShape's feature id.

---

## 1. Name grammar

A mapped name is a byte string built by appending to the name the element already had. It is never
a fresh opaque id: the whole history is readable in the string. Reading one left to right gives the
oldest fragment first and the most recent operation last.

* **Separator.** Fragments are separated by the element-map prefix, the single character `;`. A
  name is stored without a leading separator and gets one back whenever it is handed out as text
  (`/opt/freecad-src/src/App/MappedName.h:68`,
  `/opt/freecad-src/src/App/MappedName.h:635`). `.` and whitespace are rejected outright, because
  `.` separates the mapped name from the trailing indexed name in a full reference
  (`/opt/freecad-src/src/App/ElementMap.cpp:553`).

* **Op code.** Each operation contributes a short upper-case code naming the kind of operation:
  `FUS` fuse, `CUT` cut, `CMN` common, `XTR` extrude, `PSM` prism-up-to, `RVL` revolve, `FLT`
  fillet, `CHF` chamfer, `RFI` refine, `MIR` mirror, `SKT` sketch, `SPT` split, `MAK` the fallback
  for an unnamed maker (`/opt/freecad-src/src/Mod/Part/App/TopoShapeOpCode.h:35`, codes at
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeOpCode.h:48` onwards — `XTR`
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeOpCode.h:65`, `FLT`
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeOpCode.h:71`, `MIR`
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeOpCode.h:83`, `SKT`
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeOpCode.h:84`, `SPT`
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeOpCode.h:93`). An operation that passes no code gets
  `MAK` (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1415`). The whole encoding
  carries a scheme version number; bumping it invalidates saved maps
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeOpCode.h:44`).

* **Generated and Modified markers.** The marker says how the new element relates to the source it
  was named from. There are three, spelled `;:G` (generated), `;:M` (modified) and `;:MG` (both);
  they are chosen by the index sign collected from the history pass — a positive index means the
  source was *modified* into this element, a negative one means it *generated* it
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1651`, marker selection at
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1736`, the three marker helpers at
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1302`,
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1308` and
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1314`). `;:MG` is reached only when one
  source both generates and modifies the same element, which is logged as suspicious
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1711`).

* **Index suffix.** When one source element maps to several new elements, the marker is followed by
  the 1-based position OCCT reported the element at. Position 1 is written as nothing at all, so
  the common one-to-one case stays short; positions 2 and up are written as decimal digits
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1745`). Two positions are reserved:
  `0` for the new face found parallel to the source face and `00` for the coplanar one. Those two
  get fixed markers rather than an OCCT ordinal precisely so the top and bottom face of an
  extrusion keep stable names when the profile changes
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1593`,
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1745`).

* **Extra sources.** If several source elements map onto the same new element, the first in sort
  order supplies the name and the rest are appended as a parenthesised, `|`-separated list, each
  marked `;K` plus its own index. At most four extra sources are recorded, to bound the name length
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1658`, the `K` marker at
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1684`, the cap at
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1720`). The `;K` marker is written only
  when the extra source mapped to more than one result: at position 1 — the ordinary case of a
  source that maps onto this element alone — the marker is left out altogether and the extra source
  contributes just its name and a tag postfix, not the marker with an empty index
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1681`).

* **Tag postfix.** Every fragment ends with the tag postfix `;:H`, then the producing object's tag
  in hex (negative tags written with a leading `-`), then `:` and the length in hex of the op-code
  text that precedes this postfix, then `,` and a single character for the element type: `F` face,
  `E` edge, `V` vertex. The tag and the length are both omitted when they are zero
  (`/opt/freecad-src/src/App/ElementMap.cpp:656`). A worked example and the field layout are
  written out in the reference itself
  (`/opt/freecad-src/src/App/MappedName.cpp:58`,
  `/opt/freecad-src/src/App/MappedName.cpp:93`); the length field is what makes a name
  parseable backwards, see section 2. An older variant used `;:T` with decimal numbers and is still
  read (`/opt/freecad-src/src/App/MappedName.cpp:65`). **FreeShape** identifies a feature by a short
  string rather than a number (`F3`), so the tag field holds that id verbatim where FreeCAD writes
  hex; an id carrying `;`, `.`, `:`, `,`, `|`, `(`, `)`, `#` or whitespace is refused rather than
  escaped. The tag written is the id of the feature running the operation, which is what a rebuild
  has to hand (FreeCAD records the tag of the object the *source* element came from,
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1508` and
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1757`).

* **Duplicate suffix.** Two elements may end up with the same constructed name — the map allows an
  element to carry several names but never a name to point at two elements. Insertion is therefore
  retried: each attempt re-encodes the name with a disambiguator `;D` plus a counter in hex as its
  op code, so the `;D` lands at the end of the name like any other operation, in front of a fresh
  tag postfix; up to 100 attempts, after which the collision is logged and the undisambiguated
  name returned (`/opt/freecad-src/src/App/ElementMap.cpp:581`, the retry cap at
  `/opt/freecad-src/src/App/ElementMap.cpp:587`, the rename itself at
  `/opt/freecad-src/src/App/ElementMap.cpp:735` and the suffix at
  `/opt/freecad-src/src/App/ElementMap.cpp:750`). In release builds the counter is a random draw
  rather than the attempt number (`/opt/freecad-src/src/App/ElementMap.cpp:743`). A second,
  separate disambiguator `;:C<n>` exists for repeated *child maps* — identical instances of the
  same sub-shape, see section 7 (`/opt/freecad-src/src/App/ElementMap.cpp:1220`).

* **Derived-from-neighbour suffixes.** Two more markers appear when an element gets no name from
  history at all and is named from its neighbours instead: `;:U<n>` for an element named from a
  higher element that contains it, and `;:L` for one named from the lower elements it is built from
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1841`,
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1912`). These are the main source of
  names in practice; see section 4.

* **Full reference.** What a downstream feature stores is the mapped name, a `.`, and the current
  indexed name, e.g. `…;:H19:8,F.Face2`. The indexed name after the dot is a cache, not the
  identity: it is stripped before lookup
  (`/opt/freecad-src/src/App/ComplexGeoData.cpp:266`) and FreeCAD's own test asserts only the
  `,F.Face2` tail (`/opt/freecad-src/src/Mod/PartDesign/PartDesignTests/TestTopologicalNamingProblem.py:2337`).

* **Hashing.** At any point above, FreeCAD may substitute a `StringHasher` id for the name so far,
  written `#<hex>`; the marker and tag postfix are still appended in clear so history tracing keeps
  working (`/opt/freecad-src/src/App/ElementMap.cpp:650`,
  `/opt/freecad-src/src/App/ElementMap.cpp:676`,
  `/opt/freecad-src/src/App/StringHasher.h:56`). **FreeShape** has no hasher, so none of the
  `#<hex>` forms and no `dehash` step exist; names are always stored expanded and are rebuilt from
  scratch on every recompute.

## 2. Composition

Names compose by suffixing, so a name is a stack of operations and the lookup key for a face is the
whole stack, not just the last step.

* **Carrying a name forward unchanged.** When an operation does not alter an element, the element
  keeps its existing name and only acquires the new op code and tag. This is one pass over the
  matched sub-shapes, re-encoding each existing name with the new operation and storing it against
  the new index (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:829`, the encode/store
  pair at `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:850`). Because the operation is
  matched by sub-shape identity, pass-through survives renumbering: an element that was `Edge7` and
  is now `Edge3` keeps its name.

* **No-op encodings are skipped.** If there is no op code to add and the producing tag is zero or
  equal to the owning object's own tag, nothing is appended at all — the name is already correct
  (`/opt/freecad-src/src/App/ElementMap.cpp:615`). This keeps an object's internal, multi-step
  modelling from inflating every name.

* **Same tag twice collapses.** When an object performs several kernel steps in one recompute,
  every step would otherwise append the same tag. FreeCAD allows exactly one extra level per tag
  (`/opt/freecad-src/src/App/ElementMap.cpp:625`). The test is one fragment further back than that
  description suggests: before appending, it cuts the name at its last tag postfix, un-hashes what
  is left and reads *that* fragment's tag, so the collapse fires only once a tag is about to appear
  for the third time in a row. What is then dropped is the last tag postfix alone — the op code in
  front of it stays and the new operation's text is appended after it
  (`/opt/freecad-src/src/App/ElementMap.cpp:644`, the truncation at
  `/opt/freecad-src/src/App/ElementMap.cpp:646`). So a tag settles at two levels however many steps
  the object runs. Intermediate shapes have no document object, so nothing is lost by collapsing
  them.

* **Why the length field exists.** The `:<len>` field in the tag postfix records how many bytes of
  op-code text sit in front of that postfix, which is what lets a reader cut the last operation off
  a name and recover the name the element had before it
  (`/opt/freecad-src/src/App/MappedName.cpp:118`,
  `/opt/freecad-src/src/App/MappedName.cpp:192`). Without it the grammar would not be parseable
  backwards, because op codes are not self-delimiting.

* **Walking the stack.** Stepping back through a name is a loop: find the last tag postfix, strip
  that operation, un-hash what remains, repeat. One step is `getElementHistory`
  (`/opt/freecad-src/src/App/ElementMap.cpp:1300`); the full walk with a callback per step is
  `traceElement` (`/opt/freecad-src/src/App/ElementMap.cpp:1353`). The walk is bounded at 50 steps
  and aborts if a tag repeats, because a name encoded against another document's string table can
  otherwise loop (`/opt/freecad-src/src/App/ElementMap.cpp:1392`,
  `/opt/freecad-src/src/App/ElementMap.cpp:1415`). "Was this element generated rather than
  inherited?" is answered by walking one level and testing for the generated marker
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:5757`).

* **Compounds compose by reference, not by copying.** When a result is a compound whose children
  are exactly the input shapes, the children's element maps are attached whole, each with an index
  offset and the op code as its postfix, instead of every child name being rewritten
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1041`, the offset/postfix assignment
  at `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1076`; the record type is
  `/opt/freecad-src/src/App/ElementMap.h:178`). A child map is only used when it covers at least
  five elements; below that the names are expanded one by one
  (`/opt/freecad-src/src/App/ElementMap.cpp:1165`). Nested child maps are flattened at insertion
  time, concatenating the postfixes, so lookups stay shallow
  (`/opt/freecad-src/src/App/ElementMap.cpp:1133`).

* **Order of construction within one operation.** Names are built in three passes, and the pass
  that names an element decides the marker it gets: history first (vertices, then edges, then
  faces), then a reverse pass naming unnamed lower elements from the named higher ones that contain
  them (`;:U`), then a forward pass naming still-unnamed higher elements from their lower ones
  (`;:L`). If anything is still unnamed, the whole loop runs a second time with the
  deliberately-deferred low-quality names allowed in
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1615`, reverse pass
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1764`, forward pass
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1852`, the retry at
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1948`).

## 3. History sources

FreeCAD never asks OCCT directly for history at the naming site. Every operation hands the naming
code an object answering two questions — "what did this input shape turn into?" and "what did this
input shape produce?" — and the naming code is written against that interface only. The interface
is two virtuals returning lists of `TopoDS_Shape`, both defaulting to empty
(`/opt/freecad-src/src/Mod/Part/App/TopoShape.h:1511`), and the comment there states the reason for
the indirection: the OCCT history APIs misbehave in enough derived classes that each needs its own
adaptor.

The adaptors, and the OCCT history each one reads:

| Adaptor | OCCT history it reads | Declared / implemented |
| --- | --- | --- |
| `MapperMaker` | `BRepBuilderAPI_MakeShape::Modified()` and `::Generated()` | `/opt/freecad-src/src/Mod/Part/App/TopoShape.h:2722`, `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:5281` |
| `MapperHistory` | `BRepTools_History::Modified()` / `::Generated()`; also constructible from a `BRepTools_ReShape` (its `History()`) or a `ShapeFix_Root` (its context's `History()`) | `/opt/freecad-src/src/Mod/Part/App/TopoShape.h:2746`, `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:5315` |
| `MapperSewing` | `BRepBuilderAPI_Sewing` — modified only, via its per-shape section/modified queries | `/opt/freecad-src/src/Mod/Part/App/TopoShape.h:2732`, `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:3440` |
| `MapperThruSections` | `BRepOffsetAPI_ThruSections::GeneratedFace()`, falling back to `MapperMaker` | `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:3463` |
| `MapperPrism` | `BRepFeat_MakePrism` plus a hand-built vertex and edge table, falling back to `MapperMaker` | `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:3506` |
| `MapperFill` | `BRepFill_Generator::GeneratedShapes()` | `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:5257` |
| `ShapeMapper` / `GenericShapeMapper` | no OCCT history at all: an explicit table the caller populates, each entry tagged generated or modified | `/opt/freecad-src/src/Mod/Part/App/TopoShapeMapper.h:110`, `/opt/freecad-src/src/Mod/Part/App/TopoShapeMapper.h:118`, `/opt/freecad-src/src/Mod/Part/App/TopoShapeMapper.h:241` |

Which operation uses which:

* **Booleans** (fuse, cut, common, section) build a `BRepAlgoAPI_BooleanOperation` and name through
  `MapperMaker` (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:5686`, naming call at
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:5739`). **General fuse** is the same
  with `BRepAlgoAPI_BuilderAlgo`, and additionally re-reads `Modified()` per input to report which
  pieces came from which argument
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:4052`,
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:4077`).
* **Pad / extrude** is `BRepPrimAPI_MakePrism` through `MapperMaker`, op `XTR`
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:4214`). **Pad up to a face** is
  `BRepFeat_MakePrism` through `MapperPrism`, op `PSM`
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:4124`).
* **Revolve** is `BRepPrimAPI_MakeRevol` through `MapperMaker`
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:4442`).
* **Fillet** and **chamfer** are `BRepFilletAPI_MakeFillet` / `BRepFilletAPI_MakeChamfer` through
  `MapperMaker` (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:3971`,
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:4002`).
* **Offset**, **thicken**, **draft**, **pipe** and **mirror** are likewise plain
  `BRepBuilderAPI_MakeShape` subclasses through `MapperMaker`
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:2434`,
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:2998`,
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:4535`,
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:5605`,
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:3879`).
* **Shape fixing** uses `MapperHistory` over `ShapeFix_Shape`'s context history
  (`/opt/freecad-src/src/Mod/Part/App/TopoShape.cpp:3104`).
* **Refine** (unify same domain) and **curve splitting** use the explicit-table mappers, not an
  OCCT history query — see section 5
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:4647`,
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:4744`).
* **Rigid transform and plain copy** have no history: there is no maker to query, so the map is
  re-attached one-to-one instead — see section 7
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:3329`,
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:3416`).

Two robustness rules fall out of the adaptor layer and should be ported with it. An OCCT exception
while reading history is swallowed and yields an empty list, so a kernel failure degrades the names
instead of failing the recompute
(`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:5290`). And a history entry naming a
shape that is not in the result, or of an unexpected type, is logged and skipped rather than
trusted (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1489`,
`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1522`).

## 4. Sketch to solid

A sketch is itself a named shape: its edges and vertices get names under the `SKT` op code before
any solid exists (`/opt/freecad-src/src/Mod/Part/App/TopoShapeOpCode.h:84`; a rectangle sketch
holds twelve map entries covering four edges and four vertices,
`/opt/freecad-src/src/Mod/PartDesign/PartDesignTests/TestTopologicalNamingProblem.py:124`, and a
one-circle sketch holds two,
`/opt/freecad-src/src/Mod/PartDesign/PartDesignTests/TestTopologicalNamingProblem.py:854`). A real
name from a padded sketch looks like
`;g815v1;SKT;:H976,V;:L#2;PSM;:H976:9,E;:L#8;PSM;:H976:9,F;:H-977,F`, quoted in the reference tests
at `/opt/freecad-src/src/Mod/PartDesign/PartDesignTests/TestTopologicalNamingProblem.py:2998` —
read right to left it is a face named from its edges, named from a vertex of the sketch.

**FreeShape: sketch regions.** A FreeShape sketch hands the solid features *regions* — the bounded
faces left when its curves split the sketch plane — rather than wires, so the sketch root is named
in two steps (`naming.name_sketch`). Each curve's edge is seeded with the sketch entity id as its
label, `<entity id>;SKT;:H<sketch id>,E`, and its vertices as `<entity id>v<n>`, the counterpart of
FreeCAD's `g<geometry id>` and `g<id>v<n>`. The split is then named through its own history (the
Splitter's `BRepTools_History`): an edge the split leaves alone keeps its seed name, one it rebuilds
gets `;:M`, and an edge cut into pieces gives `;:M`, `;:M2`, … as in "An edge is split" below.
Region faces have no history entry, so the forward pass names each from the edges of its outer
wire, `;:L` (rule 4 below): a region is identified by the entities that bound it, and a hole inside
it does not rename it. Only the regions are named; the unbounded pieces of the plane are outside
the result and their history entries are skipped (section 3). A region reference stores this name
and resolves by it alone. A reference with no name is a legacy one: it is matched by its geometric
signature and the consuming feature is marked recovered.

**How a pad's elements get their names.** The pad feeds the sketch face to `BRepPrimAPI_MakePrism`
and names the result through `MapperMaker`
(`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:4214`). The three passes of section 2
then do the following:

1. **Side faces from sketch edges.** OCCT reports each profile edge as *generating* the side face
   swept from it, so each side face gets a `;:G`-marked name built on that sketch edge's name. This
   is the load-bearing rule: a side face is identified by *which sketch edge* it came from, not by
   its index.
2. **Bottom and top faces.** The profile face is reported as modified into the bottom face. OCCT
   also reports the profile face as generating the entire solid, which is useless — it would couple
   every side face and its edges to unrelated profile edges. Such higher-level reports are
   recognised, pushed to the back of the sort order and skipped entirely if any more precise
   mapping exists; only if elements remain unnamed is the loop re-run with them allowed in
   (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1537`, the deferral at
   `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1637`, the re-run at
   `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1948`). To keep the top and bottom
   faces stable anyway, a generated face that is parallel or coplanar to the source face is
   detected geometrically and given the reserved fixed indices `0` and `00` rather than an OCCT
   ordinal (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1332`,
   `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1554`,
   `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1593`). The first coplanar face found
   takes `00`, the next face parallel to the source takes `0`, and the pair is looked for afresh in
   each reported image (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1529`).
   **FreeShape:** the kernel helpers also report a sweep's end cap as a generated face directly
   (`FirstShape` / `LastShape`, which FreeCAD's `MapperMaker` does not read), so the geometric pin
   is applied to any generated face that is parallel or coplanar with a planar source face, not only
   to the faces reached by exploring a high-level report. Without that the cap would take an OCCT
   ordinal counted after the solid's faces, and the top face of an extrusion would be renamed by
   every edge added to the profile — the very thing the fixed indices exist to prevent.
3. **Edges and vertices of those faces.** They usually have no history of their own. The reverse
   pass names each unnamed edge from the named face containing it, and each unnamed vertex from its
   edge, as `;:U<n>` where `n` is the position of the sub-element inside the parent. An element
   lying in several parents collects several candidate names, which are sorted so the choice is
   deterministic, and the first is used
   (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1764`, name assignment at
   `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1832`).
4. **Faces with no history left.** The forward pass names a still-unnamed face from the edges of
   its *outer wire only* — inner wires are deliberately excluded, so punching a hole in a face does
   not rename the face (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1852`, the
   outer-wire restriction at
   `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1867`). The lowest-sorting edge name
   supplies the base, with `;:L` and, if there are several, a parenthesised list of up to four more
   (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1911`). A face is only named this way
   if *every* one of its outer-wire edges is already named; otherwise it is left for the next round
   (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1884`).

The pad's map therefore holds the solid's own 26 elements plus the 4 names inherited from the
sketch, and none of the solid's elements keeps its raw indexed name
(`/opt/freecad-src/src/Mod/PartDesign/PartDesignTests/TestTopologicalNamingProblem.py:565`,
`/opt/freecad-src/src/Mod/PartDesign/PartDesignTests/TestTopologicalNamingProblem.py:571`).

**What happens when the sketch changes.**

* **An edge is added.** The new sketch edge gets a name that did not exist before, and every
  pre-existing sketch edge keeps the name it had, because sketch names are not positional. So the
  pad's existing side faces keep their names and one new side face appears, named from the new
  edge. The top and bottom faces keep their names because they are pinned by the
  parallel/coplanar markers rather than by edge count. The reference test for this adds four line
  segments to a sketch that already carries a downstream sketch-on-face plus pad, and asserts the
  downstream feature still lands correctly
  (`/opt/freecad-src/src/Mod/PartDesign/PartDesignTests/TestTopologicalNamingProblem.py:1154`, the
  added geometry at
  `/opt/freecad-src/src/Mod/PartDesign/PartDesignTests/TestTopologicalNamingProblem.py:1217`).
* **An edge is removed.** Its name is simply absent from the rebuilt map, as is the side face named
  from it, and the faces that were adjacent get new `;:L` names if their outer-wire edge set
  changed. Looking the old name up returns an empty indexed name — there is no fallback and no
  nearest match (`/opt/freecad-src/src/App/ComplexGeoData.cpp:240`). The reference test deletes one
  edge of a hexagon under a pad plus attached sketch
  (`/opt/freecad-src/src/Mod/PartDesign/PartDesignTests/TestTopologicalNamingProblem.py:2264`).
  **FreeShape:** per ADR 0003 this is the error case that names the lost element, not a silent
  re-match.
* **An edge is split.** A split is a single source element mapping to several results, so each
  piece gets the *same* base name with a different index suffix — `;:M`, `;:M2`, `;:M3`, … The one
  place in the reference tree that builds such a mapping by hand, splitting one closed edge into
  four, shows the shape of it exactly: one modified entry with four results, plus two generated
  entries for the retained end vertices
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:4744`). The consequence is the one to
  port: an existing reference to the unsplit edge does not survive a split, because none of the
  pieces inherits the bare name — they all carry an index suffix.

## 5. Split and merge

Split and merge are the same mechanism read in two directions, so they are described together.

* **Split: one source, many results.** Whatever produces it — a boolean cutting a face in two, a
  trim, a sketch edge divided — the history reports one old element mapping to several new ones.
  Each result is named from the same source with its OCCT position as the index suffix, position 1
  written as nothing (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1745`); the
  hand-built one-to-four example is at
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:4744`. The comment at
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1691` is explicit that including the
  OCCT ordinal costs name stability and is accepted only because duplicate names are worse. So:
  after a split, a reference to the original face resolves to the piece OCCT happens to report
  first, and the other pieces have names that did not exist before.

* **Merge: many sources, one result.** All the sources are collected against the one new element,
  sorted, and the first supplies the name; the others are appended as the `;K`-marked parenthesised
  list of section 1, capped at four
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1634`,
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1658`,
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1720`). The merged element's name is
  therefore not equal to any of the source names: a reference to either face before the merge does
  not resolve afterwards. If the sources disagree about whether they generated or modified the
  result, the marker degrades to `;:MG` and the disagreement is logged
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1711`).

* **The unify-same-domain step.** "Refine" is the pass that removes the seams a boolean leaves
  behind, merging coplanar faces and collinear edges, and it is where most real-world merges
  happen. FreeCAD does *not* read an OCCT history for it. It subclasses the refine maker purely to
  reach its internal modified-shapes table, and replays every entry as **generated**, not modified
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:4614`, the replay at
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:4628`), then names through the generic
  explicit-table mapper under op code `RFI`
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:4633`,
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeOpCode.h:67`). Two consequences to port: a refined
  face carries `;:G`-marked names even though it is intuitively a modification, and because refine
  is applied *after* the boolean under a tag of its own, every face of a refined result has one
  more fragment on its name than the same face of an unrefined one. Refine also falls back to the
  input shape wholesale, map and all, if the operation throws or changes the solid's closedness
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:4654`).

  **FreeShape:** refine is not a separate naming step here. A kernel helper runs the boolean, the
  refine and (in careful mode) the healer, and composes one `BRepTools_History` over all of them, so
  one helper call is one operation and adds one fragment under the helper's own op code — a refined
  result is not a fragment longer than an unrefined one, and the marker a merged face ends up with
  is whatever the composed history reports (`;:M` for the usual boolean-then-refine path) rather
  than refine's replayed `;:G`. The merge rule itself is unchanged: the sources are sorted, the
  first names the face and the rest follow in the parenthesised list.

* **A failure mode worth recording.** Because the resulting names differ, turning refine on or off
  changes every downstream reference. The reference tree's own topological-naming test for an
  inserted base feature sets `Refine = False` explicitly
  (`/opt/freecad-src/src/Mod/PartDesign/PartDesignTests/TestTopologicalNamingProblem.py:2440`),
  which is why section 9 lists the merge scenario as untested upstream.

## 6. Deleted elements

* **A deleted element simply has no name.** Nothing marks a deletion. If no history entry names an
  element, the history pass assigns it nothing, and if the reverse and forward passes cannot reach
  it either, it stays unmapped and the attempt is logged
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1884`). Conversely, storing a null
  name against an element is how the map is told to drop it
  (`/opt/freecad-src/src/App/ElementMap.cpp:548`, the erase path at
  `/opt/freecad-src/src/App/ElementMap.cpp:760`).

* **Looking up a lost name yields nothing, not a guess.** Resolving a mapped name to an index
  returns an empty indexed name when the map has no entry, with no geometric fallback anywhere on
  the path (`/opt/freecad-src/src/App/ComplexGeoData.cpp:240`). The combined lookup returns a pair
  of indexed and mapped name, so the caller can see that the name survived in the reference while
  the index came back empty (`/opt/freecad-src/src/App/ComplexGeoData.cpp:253`; the pair type is
  `/opt/freecad-src/src/App/MappedElement.h:41`). Looking up by index in the other direction
  likewise returns empty rather than inventing a name, unless the caller explicitly asks for the
  raw indexed name as a fallback
  (`/opt/freecad-src/src/App/ComplexGeoData.cpp:218`).

* **FreeCAD's response is a warning.** A reference whose element vanished produces a
  `missing element reference` log line naming the object and the full mapped name, and the
  recompute continues; the reference is then silently re-bound to whatever index the property
  resolution settles on. The reference tests capture exactly this, quoting the warning text and
  then asserting that the attachment has moved to `Face11`
  (`/opt/freecad-src/src/Mod/PartDesign/PartDesignTests/TestTopologicalNamingProblem.py:2996`, the
  assertion at
  `/opt/freecad-src/src/Mod/PartDesign/PartDesignTests/TestTopologicalNamingProblem.py:3000`).
  A second instance of the same behaviour is an attachment auto-changing from `Face8` to `Face9`
  when a base feature is inserted
  (`/opt/freecad-src/src/Mod/PartDesign/PartDesignTests/TestTopologicalNamingProblem.py:2542`).

* **FreeShape:** per ADR 0003 this is the one place we deliberately diverge. A reference that
  carries an element name which is not in the rebuilt map is an error that names the lost element.
  There is no fall back to the geometric signature matcher on a named miss — that is exactly the
  silent wrong match the rejected alternatives section of ADR 0003 rules out. The signature is used
  only for a legacy reference that carries no name at all, and the result is flagged as recovered.

## 7. Copies

* **Mirror.** A mirror is a real kernel operation: a `BRepBuilderAPI_Transform` named through
  `MapperMaker` under op code `MIR`
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:3865`,
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeOpCode.h:83`). The transform reports each source
  element as modified into exactly one result, so every element of the mirrored copy gets a
  `;:M`-marked name built on its source's name plus `;MIR` and the mirroring feature's tag. The
  mirrored face is therefore *distinguishable* from its original — the names differ by the appended
  fragment — which is the property a signature matcher cannot provide for a symmetric part.

* **Rigid transform and plain copy.** When a transform has no scaling, OCCT does not rebuild the
  topology, so there is no maker and no history to read. The map is re-attached one-to-one instead:
  the whole source map becomes a single child entry with the op code as its postfix and the source
  object's tag, and the new map is built from that
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:3329`, the branch that re-maps rather
  than aliasing at `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:3360`, the child
  construction at `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:754` and
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:775`). A plain copy and a general
  (scaling) transform take the same route
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:3416`,
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:3374`). Crucially the re-map is skipped
  altogether when there is no op code and the source already belongs to this object — the shape is
  then taken over unchanged, names and all
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:3368`). So a feature's own internal
  repositioning does not touch names, but a transform feature does add a fragment to all of them.

* **Pattern instances.** A pattern builds a compound of transformed copies. Each instance keeps its
  source map whole, registered as a child entry with an index offset equal to the number of
  elements already placed, so instance *k*'s `Face3` resolves through the source map's `Face3`
  (`/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1041`, the running offset at
  `/opt/freecad-src/src/Mod/Part/App/TopoShapeExpansion.cpp:1076`). Below five elements per
  instance the child map is not used and the names are written out individually
  (`/opt/freecad-src/src/App/ElementMap.cpp:1165`).

* **Telling identical instances apart.** Instances of a pattern share a tag and an op code, so
  their child entries would collide. Occurrences are counted, and from the second onwards a
  `;:C<n>` disambiguator is inserted before the tag postfix — the comment names the draft array as
  the motivating case and is explicit that this postfix exists only to disambiguate and is not an
  index to be read back (`/opt/freecad-src/src/App/ElementMap.cpp:1177`, the suffix at
  `/opt/freecad-src/src/App/ElementMap.cpp:1220`). An instance that has its own element map and a
  duplicate tag/postfix pair is expanded element by element instead of sharing
  (`/opt/freecad-src/src/App/ElementMap.cpp:1182`). A genuine duplicate child entry is refused and
  logged (`/opt/freecad-src/src/App/ElementMap.cpp:1246`).

* **After the pattern, the fuse.** A pattern that merges its instances into one solid runs a
  boolean afterwards, so the instance faces that touch are subject to the merge rule of section 5
  and the surviving faces carry both the `;:C` instance marker and the boolean's fragment. The
  reference test for a multi-transform asserts only the resulting map size, 26 entries for a
  transformed box (`/opt/freecad-src/src/Mod/PartDesign/PartDesignTests/TestTopologicalNamingProblem.py:1060`).

## 8. Joint frame

This section is about FreeCAD's Assembly workbench rather than the element map: it is the rule
FreeShape needs for placing a joint once an element reference has resolved.

* **A joint reference is an object plus two sub-element names.** The placement routine takes the
  joint's reference, reads the picked element from the first sub-element name and a secondary
  "where on it" element from the second
  (`/opt/freecad-src/src/Mod/Assembly/UtilsAssembly.py:885`, the two names at
  `/opt/freecad-src/src/Mod/Assembly/UtilsAssembly.py:892`). Both are parsed into a type and a
  1-based index (`/opt/freecad-src/src/Mod/Assembly/UtilsAssembly.py:909`) and then resolved purely
  positionally against the shape's element lists; an out-of-range index prints
  `Joint Corrupted` and returns nothing
  (`/opt/freecad-src/src/Mod/Assembly/UtilsAssembly.py:1027`). That positional resolution is what
  FreeShape replaces with a named lookup.

* **Origin on a planar face.** For a face reference the surface is taken from the face
  (`/opt/freecad-src/src/Mod/Assembly/UtilsAssembly.py:953`). If the secondary element is the face
  itself — i.e. the user picked the face, not a point on it — the origin is the face's centre of
  gravity. That is the default branch; cylinders, tori, spheres and cones take their axis centre,
  centre and apex respectively instead
  (`/opt/freecad-src/src/Mod/Assembly/UtilsAssembly.py:961`, the planar default at
  `/opt/freecad-src/src/Mod/Assembly/UtilsAssembly.py:972`). Note this is the centre of gravity of
  the *face*, so it moves when the face's outline changes and it is not necessarily inside the face
  for a non-convex outline.

* **Origin when something on the face was picked.** If the secondary element is a circular edge of
  the face, the origin is that circle's centre
  (`/opt/freecad-src/src/Mod/Assembly/UtilsAssembly.py:973`); there is a special case for the
  B-spline intersection curve of two cylinders
  (`/opt/freecad-src/src/Mod/Assembly/UtilsAssembly.py:984`). Otherwise it is the picked vertex
  (`/opt/freecad-src/src/Mod/Assembly/UtilsAssembly.py:990`).

* **Orientation.** The rotation comes from the surface's own placement rotation, whenever the
  surface has one — for a plane that is the frame whose Z is the plane normal
  (`/opt/freecad-src/src/Mod/Assembly/UtilsAssembly.py:998`). Nothing re-derives the normal from
  the face's orientation flag, so the frame's Z follows the underlying surface, not the face sense.
  A vertex reference is given the identity rotation, and a straight edge is given the frame of a
  plane built on the edge direction
  (`/opt/freecad-src/src/Mod/Assembly/UtilsAssembly.py:1009`).

* **Frame of expression.** The placement is computed in the owning object's own coordinates and
  then re-expressed relative to that object's placement, so a part placement does not leak into the
  joint frame (`/opt/freecad-src/src/Mod/Assembly/UtilsAssembly.py:1006`).

* **The joint's offset is applied last.** The joint multiplies the frame it got by its own
  attachment offset, choosing offset 1 or 2 by which side of the joint is being placed; a Distance
  joint asks for the frame with the secondary vertex ignored, i.e. always centred
  (`/opt/freecad-src/src/Mod/Assembly/JointObject.py:648`, the Distance special case at
  `/opt/freecad-src/src/Mod/Assembly/JointObject.py:649`).

### 8.1 FreeShape's connector X axis

FreeShape follows FreeCAD's orientation rule for a mate connector on a flat face: X is fixed in the
plane's own frame, not derived from the face's edges. An edit that changes which edge is longest
therefore does not turn X round. One addition keeps existing connectors where they are:

* When a connector is made, X still starts along the face's longest straight edge, so square parts
  line up squarely. The connector stores that X as `x_ref`: its components along the plane's own X
  axis and Z × X (`connector_x_ref` in `freeshape/assembly.py`).
* When the part changes, the frame is rebuilt from `x_ref` against the new face's plane
  (`apply_x_ref`). Edge lengths take no part.
* Every other connector kind stores `x_ref` against perp(Z) and Z × perp(Z), which is `[1, 0]`;
  its frame is unchanged.
* `x_ref` is optional in `.fasm` files. A connector loaded without one takes X from the
  longest-edge rule on its first refresh, then stores it, so saved assemblies keep their mates.

## 9. Scenarios

The cases later cards port as tests. Each names the reference test it comes from, under
`/opt/freecad-src/src/Mod/PartDesign/PartDesignTests/TestTopologicalNamingProblem.py`, or says
plainly that there is none. A scenario with no FreeCAD test still has to be covered — the note just
records that we are writing it from the rules above rather than transcribing an upstream case.

| Scenario | Source |
| --- | --- |
| **Sketch dimension change** — a datum on the base sketch is edited under a downstream sketch-on-face and pad. | `testRectanglewithArcChangeinGlobalCenter`, line 2576 (the datum edits at 2633 and 2637) and `testRectanglewithArcChangeinGlobalUpperRight`, line 2642. |
| **Sketch curve added** — four line segments are added to the base sketch of a pad that already carries a sketch-on-face and a second pad. | `testChangeSketch`, line 1154 (added geometry at 1217). |
| **Face split by an inserted hole** — a hole is cut into a padded box through a sketch attached to one of its faces, splitting the face set and adding the bore wall. | `testPartDesignElementMapHole`, line 832 (asserts 7 faces / 15 edges / 10 vertices and a 32-entry map); the related case of a pocket cutting across a filleted face is `testPartDesignTNPChamfer`, line 1715. |
| **Faces merged by an edit** — an edit makes two faces coplanar and the refine / unify-same-domain pass fuses them into one. | **no FreeCAD test.** The nearest upstream case, `testBaseFeatureAttachmentSupport` at line 2425, switches refine off explicitly at line 2440 to avoid it. Write this one from section 5. |
| **Inserted part-creating feature** — a feature is inserted below existing features, renumbering everything downstream. | `testBaseFeatureAttachmentSupport`, line 2425 (a fused pair of boxes becomes the base feature and the attachment moves from `Face8` to `Face9`, line 2542) and `testPadsOnBaseObject`, line 52 (three stacked pads, the middle one moved). |
| **Consumed edge** — an edge is removed by an edit rather than merely reshaped: a sketch fillet eats a corner, or a trim removes a segment. | `testApplyFillet`, line 1268 (a sketch fillet applied under a live pad plus downstream pad); the trim variant is inside `testRectanglewithArcChangeinGlobalCenter` at line 2629. A whole-edge deletion is `testPD_TNPSketchPadSketchDelete`, line 2163 (deletion at 2264). |
| **Twin edges** — two edges a geometric signature cannot tell apart, so only the name distinguishes them. | **no FreeCAD test.** The nearest upstream case is `testPD_TNPSketchPadTouching`, line 1955, which pads touching circles and so produces coincident geometry, but it asserts volume only. The duplicate-name machinery this exercises is `/opt/freecad-src/src/App/ElementMap.cpp:735`. |
| **Mirrored copy** — a mirror instance's elements must be distinguishable from their originals. | **no FreeCAD test.** The upstream slot `testPD_TNPSketchPadSketchSymmetry` at line 2290 is an empty stub, and the only real transform test, `testPartDesignElementMapTransform` at line 1060, uses a scaled multi-transform and asserts map size only. Write this one from section 7. |
| **Hole through a fillet** — a pocket is sketched on a face created by a fillet, then the fillet's edge list is edited underneath it. | `testPartDesignTNPFillet`, line 1821, and its chamfer twin `testPartDesignTNPChamfer`, line 1715. Both assert the resulting map size and face/edge/vertex counts as well as volumes. |

One upstream case worth porting alongside these, though it is not on the required list, is
`testPadChange_UpToFirst_to_Dimension` at line 2826: changing a pad from up-to-first to a length
makes a referenced face genuinely disappear, and the test documents FreeCAD's
`missing element reference` warning verbatim at line 2996. Under ADR 0003 that is FreeShape's error
case, so it is the natural regression test for the error path described in section 6.
