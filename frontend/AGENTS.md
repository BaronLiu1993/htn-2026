<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

---
name: AI Agent Evaluation Design System
version: 1.0.0
status: canonical
audience: [design-agents, coding-agents, product-designers, frontend-engineers]
default_theme: light
product_type: ai-agent-evaluation-benchmark
---

# AI Agent Evaluation Design System

This file is the visual and interaction source of truth for the AI Agent Evaluation Benchmark. An implementation agent should follow it directly and should not invent new colors, spacing values, component styles, or interaction patterns unless a product requirement cannot be satisfied with the system below.

## 1. Implementation contract

### Required

- Use semantic design tokens rather than raw hex values inside components.
- Use the light theme as the canonical product experience.
- Use purple only for primary actions, active navigation, focus, and selected states.
- Use semantic colors for evaluation outcomes: green for pass, amber for review, red for fail, blue for running or informational states.
- Pair every status color with text, an icon, or a shape. Never communicate status using color alone.
- Keep application surfaces flat and structured with one-pixel borders. Use shadows sparingly.
- Optimize for information density without reducing body text below 14px.
- Use tabular numerals for scores, costs, latency, tokens, and percentages.
- Provide visible hover, focus, active, disabled, loading, empty, and error states.
- Preserve keyboard navigation and WCAG 2.2 AA contrast.

### Avoid

- Do not use neon gradients across large surfaces.
- Do not use purple for pass/fail states.
- Do not place borders around every piece of text.
- Do not use glassmorphism blur as the default card treatment.
- Do not use more than one primary button in the same local action group.
- Do not use serif typography inside tables, forms, logs, navigation, or charts.
- Do not use pure black `#000000` or pure white `#FFFFFF` as large background surfaces.
- Do not add decorative animations to analytical or high-density views.

## 2. Visual direction

The interface should feel like a precise evaluation laboratory: calm, technical, inspectable, and trustworthy. It combines an editorial presentation layer with a dense observability-style application layer.

Four principles govern the system:

1. **Evidence first:** scores and claims should visually lead toward their supporting evidence.
2. **Calm density:** show meaningful information without making every item compete for attention.
3. **Visible state:** selected, running, passed, failed, stale, and disabled states must be immediately distinguishable.
4. **Progressive depth:** show the conclusion first, then allow users to inspect criteria, traces, tool calls, and raw data.

## 3. Color system

### 3.1 Canonical light palette

| Token | Value | Intended use |
| --- | --- | --- |
| `color.bg.canvas` | `#F7F7FA` | Application background |
| `color.bg.subtle` | `#F0F0F5` | Sidebar and recessed areas |
| `color.surface.1` | `#FFFFFF` | Primary panels and cards |
| `color.surface.2` | `#F7F6FA` | Elevated controls and hover surfaces |
| `color.surface.3` | `#EEEAF5` | Menus, popovers, selected neutral surfaces |
| `color.border.subtle` | `#E4E2EA` | Quiet separators and table rows |
| `color.border.default` | `#D4D1DC` | Inputs, cards, and visible structure |
| `color.border.strong` | `#A8A3B2` | Emphasized boundaries |
| `color.text.primary` | `#18161D` | Primary text and important values |
| `color.text.secondary` | `#514E59` | Body copy and labels |
| `color.text.muted` | `#77727F` | Metadata and placeholders |
| `color.text.disabled` | `#AAA5B0` | Disabled content |
| `color.overlay` | `rgba(24, 22, 29, 0.36)` | Modal and drawer overlay |

### 3.2 Brand palette

| Token | Value | Intended use |
| --- | --- | --- |
| `color.brand.50` | `#F5F3FF` | Very light tint |
| `color.brand.100` | `#EDE9FE` | Subtle selected content |
| `color.brand.200` | `#DDD6FE` | Decorative highlight |
| `color.brand.300` | `#C4B5FD` | Dark-theme icon highlight |
| `color.brand.400` | `#A78BFA` | Focus ring and secondary accent |
| `color.brand.500` | `#8B5CF6` | Active state and charts |
| `color.brand.600` | `#7C3AED` | Primary button background |
| `color.brand.700` | `#6D28D9` | Primary button hover |
| `color.brand.800` | `#5B21B6` | Primary button pressed |
| `color.brand.900` | `#4C1D95` | Deep accent surface |

Use `brand.700` as the default filled action. Use `brand.600` for focus outlines on light surfaces. Use `brand.600` for selected chart series. Do not use a purple gradient on normal controls.

### 3.3 Semantic palette

| State | Foreground | Subtle background | Border |
| --- | --- | --- | --- |
| Pass / success | `#047857` | `rgba(4, 120, 87, 0.09)` | `rgba(4, 120, 87, 0.30)` |
| Review / warning | `#B45309` | `rgba(180, 83, 9, 0.10)` | `rgba(180, 83, 9, 0.30)` |
| Fail / danger | `#BE123C` | `rgba(190, 18, 60, 0.09)` | `rgba(190, 18, 60, 0.30)` |
| Running / info | `#2563EB` | `rgba(37, 99, 235, 0.09)` | `rgba(37, 99, 235, 0.30)` |
| Neutral / queued | `#625D6B` | `rgba(98, 93, 107, 0.09)` | `rgba(98, 93, 107, 0.24)` |

Status mapping is fixed:

```text
passed, improved, healthy          -> success
needs-review, partial, disputed    -> warning
failed, regressed, blocked, error  -> danger
running, syncing, informational    -> info
queued, draft, cancelled, unknown  -> neutral
```

### 3.4 Data visualization palette

Use series colors in this order:

```text
1  #6D28D9  violet
2  #0E7490  cyan
3  #047857  green
4  #B45309  amber
5  #BE123C  rose
6  #2563EB  blue
7  #BE185D  pink
8  #4D7C0F  lime
```

Rules:

- Use violet for the current or selected agent.
- Use neutral gray for baselines whenever possible.
- Use green and red only when the data means improvement and regression.
- Directly label important series instead of relying only on a legend.
- For more than eight series, group, filter, or use small multiples. Do not invent more colors.
- Chart grid lines use `color.border.subtle` at 60% opacity.
- Axis text uses `color.text.muted` at 12px.

### 3.5 Optional dark palette

Light mode is the product default. If dark mode is explicitly required, use these tokens and keep all component behavior unchanged.

| Token | Value |
| --- | --- |
| `color.bg.canvas` | `#0B0B0F` |
| `color.bg.subtle` | `#0F0F14` |
| `color.surface.1` | `#141419` |
| `color.surface.2` | `#1A1A21` |
| `color.surface.3` | `#22222B` |
| `color.border.subtle` | `#25252D` |
| `color.border.default` | `#32323D` |
| `color.border.strong` | `#4A4857` |
| `color.text.primary` | `#F4F2F7` |
| `color.text.secondary` | `#B7B4C2` |
| `color.text.muted` | `#858190` |
| `color.text.disabled` | `#5E5B68` |

## 4. Typography

### Font families

```css
--font-display: "Instrument Serif", "Iowan Old Style", Georgia, serif;
--font-ui: "Geist", Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
--font-mono: "Geist Mono", "JetBrains Mono", ui-monospace, SFMono-Regular, monospace;
```

- Use `font-display` only for landing-page headlines, empty-state statements, or a single high-level editorial moment.
- Use `font-ui` everywhere in the application.
- Use `font-mono` for identifiers, timestamps, code, JSON, prompts, trace payloads, and raw metric values when alignment matters.

### Type scale

| Token | Size / line height | Weight | Usage |
| --- | --- | --- | --- |
| `type.display.xl` | `64px / 0.98` | 400 display | Marketing hero only |
| `type.display.lg` | `48px / 1.02` | 400 display | Marketing section headline |
| `type.heading.1` | `32px / 1.18` | 600 | Main page title |
| `type.heading.2` | `24px / 1.25` | 600 | Major section title |
| `type.heading.3` | `18px / 1.35` | 600 | Panel title |
| `type.body.lg` | `16px / 1.55` | 400 | Introductory body copy |
| `type.body.md` | `14px / 1.5` | 400 | Default application text |
| `type.body.sm` | `13px / 1.45` | 400 | Dense secondary content |
| `type.label.md` | `13px / 1.2` | 550 | Controls and tabs |
| `type.label.sm` | `11px / 1.2` | 600 | Eyebrows and compact labels |
| `type.metric.lg` | `36px / 1` | 600 | Primary metric |
| `type.metric.md` | `24px / 1` | 600 | Secondary metric |
| `type.code` | `12px / 1.55` | 400 mono | Logs and structured data |

Rules:

- Use sentence case, including buttons and tabs.
- Use uppercase only for short 11px eyebrows, never for sentences.
- Use tabular numerals for metrics: `font-variant-numeric: tabular-nums`.
- Limit marketing prose to 62 characters per line and product prose to 75 characters per line.

## 5. Spacing, sizing, and shape

### Spacing scale

```text
space.0  = 0
space.1  = 4px
space.2  = 8px
space.3  = 12px
space.4  = 16px
space.5  = 20px
space.6  = 24px
space.8  = 32px
space.10 = 40px
space.12 = 48px
space.16 = 64px
space.20 = 80px
space.24 = 96px
```

Use a four-pixel base grid. Default component gaps are 8px or 12px. Default panel padding is 20px on compact screens and 24px on desktop.

### Radius

```text
radius.xs   = 4px
radius.sm   = 6px
radius.md   = 8px
radius.lg   = 12px
radius.xl   = 16px
radius.pill = 999px
```

- Inputs and buttons: `radius.sm`
- Cards and panels: `radius.md`
- Dialogs and large overlays: `radius.lg`
- Status badges and filter chips: `radius.pill`
- Avoid mixing more than two radius sizes in a single component.

### Control heights

```text
compact = 28px
small   = 32px
medium  = 36px
large   = 44px
```

Use 36px for most desktop controls and 44px for important forms or touch-first layouts.

### Elevation

```css
--shadow-popover: 0 12px 32px rgba(0, 0, 0, 0.34), 0 0 0 1px rgba(255,255,255,0.04);
--shadow-dialog: 0 24px 80px rgba(0, 0, 0, 0.52), 0 0 0 1px rgba(255,255,255,0.05);
```

Cards should not use drop shadows. Popovers, menus, dialogs, and floating command surfaces may use elevation.

## 6. Layout system

### Breakpoints

```text
sm  = 640px
md  = 768px
lg  = 1024px
xl  = 1280px
2xl = 1536px
```

### Application shell

- Top bar height: 56px.
- Expanded sidebar width: 232px.
- Collapsed sidebar width: 64px.
- Optional inspector width: 360px, resizable from 300px to 480px.
- Main content maximum width: 1600px; data tables may use the full available width.
- Main content padding: 16px below `md`, 24px from `md` to `xl`, and 32px at `xl` and above.
- Use a 12-column grid for dashboards with 24px gutters.

### Density modes

The default density is `comfortable`. Tables, trace lists, and case queues may expose a `compact` option.

| Mode | Row height | Cell horizontal padding |
| --- | ---: | ---: |
| Comfortable | 44px | 16px |
| Compact | 36px | 12px |

Do not compress form controls below 32px.

## 7. Component specifications

### 7.1 Buttons

Variants:

- `primary`: brand-filled; one per local action group.
- `secondary`: neutral surface with default border.
- `ghost`: transparent; toolbar and low-emphasis actions.
- `danger`: danger-filled or danger-outlined for destructive actions.

Rules:

- Order button content as leading icon, label, trailing shortcut or chevron.
- Maintain at least 8px between icon and label.
- Loading buttons preserve their width and replace the leading icon with a spinner.
- Disabled buttons use disabled text and a subtle surface; do not reduce opacity below 55%.
- Icon-only buttons require an accessible label and tooltip.

### 7.2 Inputs and selectors

An input contains label, optional description, control, and validation message.

```text
default border   -> color.border.default
hover border     -> color.border.strong
focus border     -> color.brand.400
focus ring       -> 0 0 0 3px rgba(167, 139, 250, 0.18)
error border     -> semantic danger foreground
disabled surface -> color.bg.subtle
```

- Labels remain visible; placeholders never replace labels.
- Put units such as `ms`, `%`, `$`, or `tokens` inside a suffix slot.
- Search inputs may omit a visible label only when an accessible label is present.
- Multi-select values use removable chips inside or below the control.

### 7.3 Badges and status indicators

- Height: 22px compact or 26px standard.
- Use subtle semantic background, semantic foreground, and semantic border.
- Include a 6px status dot or 12px icon plus a text label.
- Scores are not status badges. Display scores as numbers with a nearby status only when a threshold has been applied.

### 7.4 Cards and panels

Use three structural variants:

- `panel`: surface 1, default border, 8px radius.
- `recessed`: background subtle, subtle border, 8px radius.
- `interactive`: surface 1, default border; on hover use surface 2 and strong border.

Panel anatomy:

```text
header: title + supporting metadata + actions
body: primary content
footer: optional summary or secondary actions
```

Do not nest more than two bordered panels. Inside a panel, use spacing and dividers before adding another card.

### 7.5 Metric tiles

Metric tiles contain:

1. Label
2. Primary value
3. Delta or threshold status
4. Optional sparkline

- The value is the most visually prominent element.
- Positive/negative color depends on meaning, not mathematical sign. Lower cost or latency can be positive.
- Always state the comparison period or baseline.
- Use no more than six metric tiles in one row.

### 7.6 Tables

- Header text uses `type.label.sm`, secondary text, and sentence case.
- Keep the first identifying column sticky when horizontal scrolling is required.
- Right-align numerical columns and use tabular numerals.
- Left-align names, labels, and statuses.
- Use row selection only when a bulk action exists.
- Hover uses `color.surface.2`.
- Selected rows use a brand-tinted surface plus a 2px left accent.
- Preserve columns while loading; use skeleton cells rather than replacing the entire table.
- Empty states appear inside the table frame and explain how to add or generate data.

### 7.7 Tabs

- Use underline tabs for page-level navigation.
- Use contained tabs for switching representations inside a panel.
- Active underline: 2px `brand.500`.
- Keep labels short and stable. Do not place dynamic counts in every tab unless the counts help navigation.

### 7.8 Sidebar navigation

- Group items by user goal rather than data type.
- Active item uses surface 2, primary text, and a 2px brand accent on the left.
- Inactive icons and labels use secondary text.
- Hover changes the surface before changing text color.
- Collapsed mode retains icons and tooltips.
- The bottom area is reserved for settings, help, and account controls.

### 7.9 Dialogs, drawers, and popovers

- Use a dialog for focused confirmation or short creation flows.
- Use a right drawer for contextual inspection that should preserve the current page.
- Use a popover for transient choices and filters.
- Never open a dialog from another dialog. Replace the current dialog content or move to a full page.
- Destructive confirmations name the affected object and describe recoverability.

### 7.10 Toasts and inline feedback

- Toasts confirm background or completed actions and disappear after 5–8 seconds.
- Persistent problems use inline banners near the affected content.
- Validation errors appear directly below the relevant field.
- A toast must not be the only place where an important failure is described.

## 8. Domain-specific evaluation components

These components express evaluation concepts while following the general visual system.

### Score display

- Show a score as a number first: `87.4` or `87.4%`.
- Display the scale when it is not obvious: `4.2 / 5`.
- Do not use a gauge when a number and threshold marker communicate the same information.
- For a threshold, show the threshold explicitly: `87.4% · passes ≥ 80%`.
- Use a progress bar only for bounded scores, never for cost, latency, or unbounded values.

### Evaluation matrix

- Rows represent test cases; columns represent agents, versions, or graders.
- Freeze the identifying row and column headers.
- Each cell contains the score or status icon, not only a background color.
- Hover reveals exact score, grader, duration, and timestamp.
- Selecting a cell opens contextual detail without losing matrix position.

### Trace timeline

- Use a vertical chronological rail.
- Each event shows icon, event name, relative duration, and state.
- Expanded events reveal input, output, metadata, and errors.
- Tool calls, model calls, retrieval, grader calls, retries, and human actions use distinct icons.
- Use indentation for parent-child relationships; maximum visible nesting is three levels.
- Long payloads collapse after 12 lines and offer `Show full payload`.

### Output diff

- Support side-by-side and unified views.
- Additions use success styling; removals use danger styling.
- Changed meaning or grader annotations use warning styling.
- Preserve whitespace and use the mono font.
- Allow wrapping, but default to no wrapping for structured data.

### Run progress

- Display completed, total, passed, failed, and remaining counts.
- A running state uses blue, not purple.
- A stopped or cancelled run is neutral unless an error caused the stop.
- Do not animate the entire panel; animate only the progress indicator or running icon.

### Grader result

An expanded grader result contains:

```text
grader name and type
score and verdict
criterion evaluated
short explanation
evidence or citations
confidence when available
grader version and timestamp
```

Place deterministic checks before model-based judgements, then human review.

## 9. Data visualization rules

Preferred charts:

- Horizontal bar: category or agent comparison.
- Line: performance, cost, or latency over time.
- Scatter: quality versus cost or latency.
- Histogram: score and latency distributions.
- Heat map: performance across cases and versions.
- Stacked bar: mutually exclusive outcome composition.

Avoid by default:

- Radar charts
- 3D charts
- Pie charts with more than four slices
- Dual-axis charts
- Decorative area gradients

Charts must include:

- Descriptive title
- Defined unit
- Visible time range or comparison baseline
- Tooltip with exact values
- Empty and insufficient-data states
- Accessible tabular alternative when the visualization contains critical information

## 10. Iconography

- Use Lucide icons or another single 1.5–2px stroke icon family.
- Standard icon sizes: 14px, 16px, 18px, and 20px.
- Use filled icons only for compact status marks or a selected product logo.
- Do not mix emoji with interface icons.
- Recommended mappings:

```text
passed        -> circle-check
failed        -> circle-x
review        -> triangle-alert
running       -> loader-circle
queued        -> clock-3
agent/model   -> bot
test case     -> flask-conical
grader        -> scan-search
trace         -> list-tree
tool call     -> wrench
dataset       -> database
comparison    -> columns-2
cost          -> badge-dollar-sign
latency       -> timer
tokens        -> binary
```

## 11. Motion

```text
motion.fast   = 120ms
motion.normal = 180ms
motion.slow   = 260ms
ease.standard = cubic-bezier(0.2, 0, 0, 1)
```

- Use 120ms for hover and pressed states.
- Use 180ms for menus, tooltips, and collapsible content.
- Use 260ms for drawers and dialogs.
- Respect `prefers-reduced-motion` and remove nonessential movement.
- Never animate metric values during routine page load.
- Skeletons may use a subtle opacity pulse, not a bright sweeping shimmer.

## 12. Content and naming

- Use direct labels: `Run benchmark`, `Compare versions`, `Review failures`.
- Prefer `passed`, `failed`, `needs review`, and `running` over clever terminology.
- Pair technical identifiers with human names where possible.
- Use absolute timestamps in detail views and relative timestamps in lists. Expose the absolute value in a tooltip.
- Error messages state what failed, why when known, and the next action.
- Empty states explain what belongs in the area and provide one clear next step.

Example:

```text
Title: No benchmark runs yet
Body: Run this benchmark to measure the selected agent against 128 test cases.
Action: Run benchmark
```

## 13. Responsive behavior

- Below `lg`, collapse the sidebar by default.
- Below `md`, stack dashboard regions into one column.
- Convert persistent inspector panels into full-height drawers below `lg`.
- Tables may scroll horizontally; do not collapse critical evaluation columns into ambiguous cards.
- Preserve the primary score, status, and action above the fold.
- On small screens, secondary actions move into an overflow menu.
- Touch targets must be at least 44px even when the visible control is smaller.

## 14. Accessibility

- Meet WCAG 2.2 AA for text and meaningful interface graphics.
- Provide a 2px visible keyboard focus outline with at least 2px offset.
- Maintain logical heading order.
- Use native buttons, links, tables, labels, dialogs, and form controls whenever possible.
- Announce asynchronous run state changes through an appropriate live region.
- Do not trap focus outside modal surfaces.
- Give charts accessible names and provide underlying data when the chart is decision-critical.
- Error, warning, success, and selected states require non-color indicators.

## 15. Token implementation

Use the following CSS variables as the initial implementation. Components should consume semantic variables, not palette variables directly.

```css
:root,
[data-theme="light"] {
  color-scheme: light;

  --bg-canvas: #f7f7fa;
  --bg-subtle: #f0f0f5;
  --surface-1: #ffffff;
  --surface-2: #f7f6fa;
  --surface-3: #eeeaf5;

  --border-subtle: #e4e2ea;
  --border-default: #d4d1dc;
  --border-strong: #a8a3b2;

  --text-primary: #18161d;
  --text-secondary: #514e59;
  --text-muted: #77727f;
  --text-disabled: #aaa5b0;

  --accent: #6d28d9;
  --accent-hover: #5b21b6;
  --accent-pressed: #4c1d95;
  --accent-focus: #7c3aed;
  --accent-subtle: rgba(109, 40, 217, 0.10);

  --success: #047857;
  --success-subtle: rgba(4, 120, 87, 0.09);
  --warning: #b45309;
  --warning-subtle: rgba(180, 83, 9, 0.10);
  --danger: #be123c;
  --danger-subtle: rgba(190, 18, 60, 0.09);
  --info: #2563eb;
  --info-subtle: rgba(37, 99, 235, 0.09);

  --radius-sm: 6px;
  --radius-md: 8px;
  --radius-lg: 12px;

  --focus-ring: 0 0 0 3px rgba(109, 40, 217, 0.16);
  --shadow-popover: 0 12px 32px rgba(24, 22, 29, 0.12), 0 0 0 1px rgba(24,22,29,0.05);
  --shadow-dialog: 0 24px 80px rgba(24, 22, 29, 0.20), 0 0 0 1px rgba(24,22,29,0.06);
}
```

## 16. Agent composition recipes

When constructing a new screen, use these recipes before inventing a layout.

### Analytical overview

```text
page header
summary metric strip
primary 8-column visualization + secondary 4-column panel
full-width searchable table
```

### Detail and inspection

```text
page header with object status and actions
horizontal summary strip
main content
optional 360px contextual inspector
```

### Queue and review

```text
filter toolbar
320px item list
flexible detail area
optional evidence or trace drawer
```

### Creation flow

```text
compact progress indicator
single focused form column, maximum 720px
sticky footer with secondary and primary actions
```

## 17. Final quality checklist

Before considering a screen complete, verify all of the following:

- [ ] All colors reference semantic tokens.
- [ ] There is only one primary action per local action group.
- [ ] Numerical columns are aligned and use tabular numerals.
- [ ] Pass, review, fail, and running states include text or icons.
- [ ] Keyboard focus is visible on every interactive element.
- [ ] Hover, active, disabled, loading, empty, and error states exist.
- [ ] Important evidence is reachable without losing the current context.
- [ ] Panels use borders and spacing before shadows.
- [ ] Serif type is restricted to editorial display moments.
- [ ] Charts state their unit and comparison baseline.
- [ ] Dense content remains readable at 100% browser zoom.
- [ ] The layout works at 1440px, 1024px, 768px, and 390px widths.
- [ ] Reduced-motion behavior has been considered.
- [ ] No new arbitrary radius, spacing, or color value was introduced.

## 18. Decision priority

If two rules conflict, resolve them in this order:

1. Accessibility and user comprehension
2. Correct communication of evaluation state
3. Consistency with semantic tokens
4. Information hierarchy
5. Visual polish

The intended result is a dark, restrained, evidence-oriented interface. It should look deliberately designed for rigorous AI evaluation, not like a generic analytics dashboard or a decorative AI demo.


<!-- END:nextjs-agent-rules -->
