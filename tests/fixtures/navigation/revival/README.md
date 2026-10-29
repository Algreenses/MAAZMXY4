# Public UI composites

These are offline composites, not live screenshots or acceptance-test evidence.
The public 4399 resurrection SWF was rendered by JPEXS FFDec with mutually
exclusive normal/VIP and one-/two-player placements selected as the original
ResurrectionMediator does. Each up/hover/down button state is rendered in the
complete dialog to retain fractional Flash placement. The SWF is never executed.

The twelve dialog images use BICUBIC resizing, independently of the runtime
templates' LANCZOS crops, on a neutral 1147×720 background. The six return images
use complete transparent up/hover/down result buttons plus the existing result
title crops, on the same neutral background. Tests check button locations,
missing-caption/button negatives, wrong result type, and revival blocking.

Source URLs and SWF/template hashes are in `agent/data/navigation/battle-ui.json`;
frame hashes are in the case manifests here. Game artwork belongs to 4399 and
the respective rights holders. Raw SWFs and decompiled scripts remain outside
the distributed source/runtime resources.
