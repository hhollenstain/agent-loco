---
name: style
description: >-
  Style the organization site from this guide. Use when editing CSS, themes,
  color, type, or component look, and when an engineer asks to change how
  the site is styled. Update this skill in that same change.
---

# Site style

Agents style the organization site from this guide. Color lives in
`static/themes.css`. Component look lives in `static/layout.css`. The page
is `templates/index.html`. Light is the default. Dark is
`html[data-theme="dark"]`.

## When an engineer changes the style

An engineer asking to change how the site is styled updates this skill in
that same change. Write the new color, type, spacing, shape, or component
rule here, then make the CSS match it. A CSS change that leaves this skill
describing the old look is not done. Do not add a second palette, type
scale, or button style beside the ones below.

## Color

Use the variables in `themes.css`. A component does not get its own hex
color. A new color is added to both `:root` and `[data-theme="dark"]`, and
to the list below.

| Variable | Use |
| --- | --- |
| `--bg`, `--bg-subtle` | Page, and the sidebar |
| `--panel`, `--panel-2` | Cards, fields, secondary buttons |
| `--ink`, `--muted` | Text, and labels, hints, kickers |
| `--line` | Borders and dividers |
| `--navy`, `--on-navy` | Primary buttons and page titles |
| `--accent`, `--on-accent`, `--ok` | Logo mark, success, open work |
| `--warn`, `--bad` | Caution, and failure |
| `--queued` | Queued work |
| `--pr-open`, `--pr-merged`, `--pr-closed` | Pull request state |
| `--code-bg` | Code |
| `--shadow`, `--overlay` | Raised panels and backdrops |

Status color uses those names. A new status reuses one of them unless the
engineer adds a token to both themes and to this table.

## Type

Body text is `15px/1.5` system UI. The logo wordmark is Avenir Next, weight
600, letter-spacing `0.16em`. A section kicker is `.ask-kicker`: uppercase,
`--muted`, `0.72rem`. A page title is `.ask-heading`: `--navy`, `1.55rem`,
weight 680. Labels are uppercase `--muted` at `0.78rem`. Hints are `.hint`
at `0.78rem` `--muted`.

## Shape

Buttons, fields, and icon buttons use a `10px` radius. Cards, rail items,
and inbox rows use `8px`. Status chips use a full pill. Borders are `1px
solid var(--line)`. The page is `--bg`. The sidebar is `--bg-subtle`.

## Controls

A primary button is `button`: `--navy` background and `--on-navy` text. A
secondary button is `button.secondary`: `--panel-2` background, `--ink`
text, and a `--line` border. An icon button is square, about `2.15rem`,
with a `10px` radius. A disabled button is `opacity: 0.5`. Put the class in
`layout.css`. Do not add an inline `style` attribute.

An organization table is `.org-table`. The header is uppercase `--muted` on
`--panel-2`. Rows sit on `--panel` with a `--line` divider and an `8px`
radius around the whole table. A switch is `.org-switch`: a pill, off on
`--panel-2` with a `--muted` knob, on with `--accent` and an `--on-accent`
knob. A remove control is a secondary icon button with a trash glyph. A
section plus is a secondary icon button. A skill loaded from a repository
is an `.org-skill-chip` pill.

## Theme

The theme control sets `data-theme="dark"` on the document element. Light
removes that attribute. Every color variable exists in both themes.
