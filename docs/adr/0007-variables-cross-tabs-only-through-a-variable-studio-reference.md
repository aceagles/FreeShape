# 7. Variables cross tabs only through a Variable Studio reference

Date: 2026-11-13

## Status

Accepted. Supersedes the 2026-11-12 grill decision that Derived carries ticked variables (recorded in the Roadmap, never an ADR).

## Context

Two mechanisms could put another tab's variables into a Part Studio: the Variable Studio reference feature, built in Foundations, and Derived, which the 2026-11-12 grill said would carry ticked variables from its source Part Studio. Both write into the same variable table, so the plan had to say how a name defined by both behaves.

Onshape's model was read on 2026-11-13. A Part Studio takes variables only from a Variable Studio, the whole table or chosen variables, from the same document or a version of another. Names are plain; a later definition overrides an earlier one and the overridden one is marked. Onshape's Derived carries parts, sketches, surfaces, planes and mate connectors, and no variables.

Alternatives considered:

- **Derived carries ticked variables, later definition wins.** The 2026-11-12 decision. Two ways to do one thing, and a name clash between them.
- **Derived variables arrive qualified, `#partstudio1.w`.** No clash, but a second kind of name, a parser change, and a tab rename that breaks expressions unless an alias is stored.

## Decision

Variables cross tabs only through a Variable Studio reference. The reference brings the whole Studio or a ticked subset, from this Document or pinned to a Version of another. A later definition of a name wins and the overridden definition is marked. Derived carries no variables. Variable names stay unqualified.

## Consequences

- One mechanism and one override rule, the same as Onshape's.
- A value that exists only in a Part Studio, such as one measured from geometry, cannot be shared with another tab. It has to be computed in a Variable Studio. Onshape has the same gap.
- The expression parser is unchanged.
- The spike's first recommendation (variables as a derivable kind) is met by the Variable Studio alone.
