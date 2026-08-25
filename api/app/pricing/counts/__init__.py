"""Laddered corners & cards pricing — INACTIVE.

Nothing in the running app imports this package. It exists so the cards tool can
one day price a line Paddy Power quotes and Pinnacle does not, which is most
fixtures: today a comparison is only possible where the two books happen to pick
the same line.

WHAT IT IS, AND WHAT IT IS NOT. This contains no football knowledge whatsoever —
it never sees the teams. Its whole input is Pinnacle's own quoted ladder, and all
it does is find the smooth count distribution that best explains those quotes,
then read other lines off it. It is a MARKET-READING model, not a match-
prediction model, and the distinction decides how much to trust it:

  * Between Pinnacle's quoted lines it inherits Pinnacle's accuracy, which is
    effectively best-in-class. You are interpolating a sharp book's own opinion.
  * Beyond the quoted range accuracy decays toward the shape assumption alone.
    Nothing anchors the tail, and no benchmark exists for that task.
  * It can NEVER beat Pinnacle. Where Pinnacle is wrong, this reproduces the
    error faithfully — it has no independent view to disagree with.

Which matters because the big edges will appear in the tails, exactly where it is
weakest: a +20% edge three rungs past the ladder is far more likely to be
tail-shape error than a +8% edge inside it. `price.py` carries the distance from
the quoted range on every price and `edge_vs` scales the bar accordingly. Do not
strip that out to make more rows appear.

Pure Python on purpose, same reasoning as pricing/grid.py: the grid is 81 points
and the fit has 2 free parameters, so numpy/scipy would add ~171 MB of image and
~79 MB of RSS (measured) to a 512 MB instance while being no faster at this size
— array-creation overhead dominates below a few thousand elements. A full fit
runs in ~0.8 ms and the 12-refit uncertainty band in ~10 ms. Python floats ARE
float64, so the numerics match a numpy implementation; only the API differs.
Do not "fix" this back to scipy without re-measuring both.
"""
