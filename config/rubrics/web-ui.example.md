# Example web-UI polish rubric

A rubric is the Verifier's oracle for taste-adjacent work: each line becomes one `VisionFinding`
(pass / fail / needs-human) when the vision model judges the screenshot. Keep lines concrete and
individually checkable — "looks nice" is not a rubric line; "primary action button is visually
dominant and above the fold" is.

## Layout
- The primary action is visible without scrolling and is the most visually dominant element.
- Content has consistent margins; nothing is clipped at the viewport edge.
- Related controls are grouped; unrelated controls are visually separated.

## Typography
- At most two type families; heading/body hierarchy is clear.
- No text overlaps or is truncated mid-word.

## Color & state
- Interactive elements are visually distinguishable from static text.
- Disabled, loading, and error states are visibly distinct (if present in this screen).

## Consistency
- Spacing follows a consistent scale (no one-off arbitrary gaps).
- Iconography style is uniform.

## Needs-human (do not auto-pass/fail)
- Whether the overall aesthetic matches brand intent — flag as needs-human.
