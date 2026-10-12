# UI Review Checklist

Load this when the cleanup target is UI code. It lists the generic visual
defaults that make a generated interface read as unreviewed. The reasoning
behind each one lives in `omh-frontend/references/taste-foundations.md`
(Review prompts - not bans); this file does not repeat it. What it adds is how
a cleanup pass treats each one.

Every item is a question, not a ban. A stated reason - brand, accessibility,
product density, or a design-system rule - closes it and the pattern stays.
No reason means the default was never chosen, and that is the finding.

## The six prompts

| Prompt | Look for in the diff or the rendered page | Cleanup treatment |
| --- | --- | --- |
| Korean body text below 14px | body copy set at 11-13px on a page with a Korean audience | raise to the 14px floor; a validated dense-data table may keep its size if the reason is written down |
| Shadows everywhere | the same shadow on every card, logo, icon, and background | delete shadows that mark no elevation or interaction; keep the ones that do |
| Stacked section labels | eyebrow, title, description, and an extra paragraph above every section; generic emoji badges | delete the layers the title already carries |
| Default palette | framework-default blue (`#3B82F6`) or a blue-to-purple scheme with no brand rationale | flag it; choosing the replacement palette is a design decision for `frontend` |
| Uniform grid | every row a perfect 3- or 4-column grid of equal cards | flag it when the items are not peers; the new layout is `frontend` work |
| Extreme gradients | saturated multi-stop gradients on surfaces, text, or buttons | tone down or delete unless the brand owns that look |

Deletion-first still holds. Removing a shadow, a label, or a gradient is a
cleanup. Picking a new palette or a new layout is a redesign, and a redesign
goes to `frontend`, or to `frontend-refactor` when component behavior must stay
identical.

## Evidence

A finding about how a page looks needs the rendered page: a screenshot, or a
visual-QA verdict. Reading the CSS proves which value is set, not that it is
what renders. Say which of the two a finding rests on.

## Attribution

The six prompts and the review-mode split adapt concepts from the
`ai-slop-cleaner` skill of `Yeachan-Heo/oh-my-claudecode@8262f9f` (MIT). No
upstream text is reproduced; the wording and the cleanup treatments are OMH's
own.
