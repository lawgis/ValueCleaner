# Value Cleaner

A QGIS plugin that finds spellings of the same value in a text field and fixes
them in one go.

Data that was typed by hand, collected in the field or merged from several
sources is full of near-duplicates: `Kaduna`, `kaduna `, `KADUNA`, `Kadunna`.
They break filters, joins, statistics and categorized styling. Value Cleaner
groups them, proposes the correct spelling, and rewrites the layer.

## Usage

1. Open **Vector → Value Cleaner** (or the toolbar button).
2. Pick a layer and a text field. Optionally tick **Only selected features**.
3. Click **Find similar values**.
4. Review the groups. Each shows every spelling found with its feature count
   and the spelling it will become (the most common one by default).
   - Untick a group to leave it alone.
   - Double-click **Will become** to type a different correct spelling.
5. Choose how to apply the changes and click **Apply**:
   - **Save to a new layer** (default): a temporary copy with the values
     corrected. Your original layer is not touched.
   - **Edit this layer**: changes go through the layer's edit buffer as a
     single undo step (Ctrl+Z). Nothing is written to disk until you save the
     layer edits.

## What counts as "similar"

- Differences in capital letters, extra or leading/trailing spaces,
  punctuation and accents are always treated as the same value.
- Small typos (a missing, extra or wrong letter, or two letters swapped) are
  grouped according to the **Similarity** setting (default 0.85, higher is
  stricter). At 1.00 only case, spacing and punctuation differences are grouped.
- Values that contain **different numbers are never grouped**, so `Zone 1` and
  `Zone 2`, or `Plot 12` and `Plot 13`, stay separate.
- Very short values (under 4 characters) are only grouped when they differ by
  case, spacing or punctuation.

Always review the groups before applying. Two different names that happen to
be spelled almost alike can be grouped; untick them or raise the similarity.

## Notes

- Works on text fields of any vector layer or table. Empty values are ignored.
- No external Python packages are needed.

Author: Lawal Lawal Adeniran
